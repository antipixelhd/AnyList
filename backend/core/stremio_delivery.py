"""Deliver canonical watch, library, and resume state to one Stremio connection.

The caller owns the database transaction. Provider writes are serialized per
connection and playback changes are confirmed before returning.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import nuvio_payloads, nuvio_projection, stremio, stremio_payloads
from core.connection_identity import refresh_stream_connection
from core.db_queries import latest_watched_at, select_in_chunks
from core.timestamps import milliseconds
from models.base import MediaType
from models.connections import MediaServerConnection
from models.media import Media
from models.show import Show

BATCH_SIZE = 500


async def media_records(
    db: AsyncSession,
    media_ids: set[int],
    api_key: str | None,
) -> dict[int, dict]:
    if not media_ids:
        return {}
    media_rows = await select_in_chunks(
        db,
        lambda chunk: select(Media).where(Media.id.in_(chunk)),
        list(media_ids),
    )
    show_ids = {media.show_id for media in media_rows if media.show_id is not None}
    shows_by_id: dict[int, Show] = {}
    if show_ids:
        shows = await select_in_chunks(
            db,
            lambda chunk: select(Show).where(Show.id.in_(chunk)),
            list(show_ids),
        )
        shows_by_id = {show.id: show for show in shows}
    await nuvio_projection.ensure_imdb_ids(media_rows, shows_by_id, api_key)

    records: dict[int, dict] = {}
    for media in media_rows:
        show = shows_by_id.get(media.show_id)
        content_id = nuvio_payloads.imdb_id(show or media)
        if not content_id:
            continue
        if (
            media.media_type == MediaType.episode
            and show is not None
            and media.season_number is not None
            and media.episode_number is not None
        ):
            records[media.id] = {
                "content_id": content_id,
                "content_type": "series",
                "title": show.title,
                "season": media.season_number,
                "episode": media.episode_number,
            }
        elif media.media_type in (MediaType.movie, MediaType.series):
            records[media.id] = {
                "content_id": content_id,
                "content_type": media.media_type.value,
                "title": media.title,
            }
    return records


async def changed_content_ids(
    db: AsyncSession,
    media_ids: set[int],
    api_key: str | None,
) -> set[str]:
    return {
        record["content_id"]
        for record in (await media_records(db, media_ids, api_key)).values()
    }


async def push_connection(
    db: AsyncSession,
    conn: MediaServerConnection,
    user_id: int,
    *,
    api_key: str | None,
    changed_media_ids: set[int] | None = None,
    watch_overrides: dict[int, bool] | None = None,
    watch_only: bool = False,
    skip_watch_media_ids: set[int] | None = None,
) -> int:
    effective_changed_ids = (
        set(changed_media_ids or set()) | set(watch_overrides or {})
        if changed_media_ids is not None or watch_overrides
        else None
    )
    effective_watch_ids = (
        set(effective_changed_ids) - set(skip_watch_media_ids or ())
        if effective_changed_ids is not None
        else None
    )
    all_library_records = (
        await nuvio_projection.build_library_items(db, user_id, api_key=api_key)
        if conn.push_collection and not watch_only
        else []
    )
    library_records = list(all_library_records)
    watched_records = (
        await nuvio_projection.build_watched_items(
            db,
            user_id,
            media_ids=effective_watch_ids,
            api_key=api_key,
            include_unknown_dates=True,
            tracked_only=True,
        )
        if conn.push_watched
        else []
    )
    if watch_overrides and conn.push_watched:
        override_media = await media_records(
            db,
            set(watch_overrides),
            api_key,
        )
        watched_at_by_media = await latest_watched_at(
            db,
            user_id,
            [media_id for media_id, watched in watch_overrides.items() if watched],
        )

        def watch_key(record: dict) -> tuple:
            return (
                record["content_id"],
                record.get("season"),
                record.get("episode"),
            )

        watched_by_key = {watch_key(record): record for record in watched_records}
        for media_id, watched in watch_overrides.items():
            if media_id in (skip_watch_media_ids or set()):
                continue
            record = override_media.get(media_id)
            if record is None:
                continue
            watched_at = watched_at_by_media.get(media_id)
            if watched_at is not None:
                if watched_at.tzinfo is None:
                    watched_at = watched_at.replace(tzinfo=timezone.utc)
                watched_at = int(watched_at.timestamp() * 1000)
            watched_by_key[watch_key(record)] = {
                **record,
                "watched": watched,
                "watched_at": watched_at,
            }
        watched_records = list(watched_by_key.values())
    progress_records = (
        await nuvio_projection.build_progress_items(db, user_id, api_key=api_key)
        if conn.push_playback and not watch_only
        else []
    )
    target_ids = (
        await changed_content_ids(db, effective_changed_ids, api_key)
        if effective_changed_ids is not None
        else None
    )
    if target_ids is not None:
        library_records = [
            record for record in library_records if record["content_id"] in target_ids
        ]
        watched_records = [
            record for record in watched_records if record["content_id"] in target_ids
        ]
        progress_records = [
            record for record in progress_records if record["content_id"] in target_ids
        ]

    current_library_ids = {
        item["content_id"]
        for item in all_library_records
    }
    previously_pushed_ids = set(conn.stremio_pushed_library_ids or [])
    removed_library_ids = (
        previously_pushed_ids - current_library_ids
        if conn.push_collection and not watch_only and conn.stremio_pushed_library_ids is not None
        else set()
    )
    if target_ids is not None:
        removed_library_ids &= target_ids

    lock = stremio.connection_lock(conn.id)
    async with lock:
        await refresh_stream_connection(db, conn)
        remote_items = await stremio.datastore_get(conn.token, all_items=True)
        remote_by_id = {
            str(item.get("_id")): item
            for item in remote_items
            if isinstance(item, dict) and item.get("_id")
        }
        now = milliseconds(datetime.now(timezone.utc)).isoformat(timespec='milliseconds').replace("+00:00", "Z")
        candidates: dict[str, dict] = {}
        clear_progress_ids: set[str] = set()
        if conn.push_playback and not watch_only:
            from models.tracking import StreamBaseline, TrackedEntry, TrackingDeletion

            baseline = await db.get(StreamBaseline, conn.id)
            mappings = (baseline.snapshot or {}).get("mappings", {}) if baseline else {}
            status_by_media: dict[tuple[int, str], str] = {}
            status_by_external: dict[tuple[str, str], str] = {}
            status_result = await db.execute(
                select(Media, TrackedEntry.status)
                .join(TrackedEntry, TrackedEntry.media_id == Media.id)
                .where(TrackedEntry.user_id == user_id,
                    Media.media_type.in_([MediaType.movie, MediaType.series]))
            )
            for media, status in status_result.all():
                media_type = media.media_type.value if hasattr(media.media_type, "value") else str(media.media_type)
                if media.tmdb_id is not None:
                    status_by_media[(int(media.tmdb_id), media_type)] = status
                for external_id in (media.imdb_id, nuvio_payloads.imdb_id(media),
                    f"tmdb:{media.tmdb_id}" if media.tmdb_id is not None else None):
                    if external_id:
                        status_by_external[(str(external_id), media_type)] = status
            deleted_result = await db.execute(
                select(Media)
                .join(TrackingDeletion, TrackingDeletion.media_id == Media.id)
                .where(TrackingDeletion.user_id == user_id,
                    Media.media_type.in_([MediaType.movie, MediaType.series]))
            )
            for media in deleted_result.scalars().all():
                media_type = media.media_type.value if hasattr(media.media_type, "value") else str(media.media_type)
                for external_id in (media.imdb_id, nuvio_payloads.imdb_id(media),
                    f"tmdb:{media.tmdb_id}" if media.tmdb_id is not None else None):
                    if external_id:
                        status_by_external[(str(external_id), media_type)] = "deleted"
            for content_id, remote in remote_by_id.items():
                if target_ids is not None and content_id not in target_ids:
                    continue
                content_type = str(remote.get("type") or "")
                tmdb_id = mappings.get(content_id)
                status = status_by_media.get((int(tmdb_id), content_type)) if tmdb_id is not None else None
                known = tmdb_id is not None or (content_id, content_type) in status_by_external
                if status is None:
                    status = status_by_external.get((content_id, content_type))
                if not known or status == "watching":
                    continue
                state = dict(remote.get("state") or {})
                if state.get("timeOffset") in (None, 0, "0"):
                    continue
                candidate = dict(remote)
                state["timeOffset"] = 0
                candidate["state"] = state
                candidates[content_id] = candidate
                clear_progress_ids.add(content_id)

        for record in library_records:
            content_id = str(record["content_id"])
            candidate = dict(
                candidates.get(content_id)
                or remote_by_id.get(content_id)
                or stremio_payloads.new_library_item(record, now)
            )
            candidate["removed"] = False
            candidate["temp"] = False
            candidate.setdefault("_ctime", now)
            candidate.setdefault("state", stremio_payloads.default_state())
            candidates[content_id] = candidate

        for content_id in removed_library_ids:
            if content_id not in remote_by_id:
                continue
            candidate = dict(candidates.get(content_id) or remote_by_id[content_id])
            candidate["removed"] = True
            candidate["temp"] = False
            candidates[content_id] = candidate

        records_by_series: dict[str, list[dict]] = {}
        for record in [*watched_records, *progress_records]:
            if record.get("content_type") == "series":
                records_by_series.setdefault(str(record["content_id"]), []).append(record)
        series_meta = await stremio.get_series_metadata(set(records_by_series))

        for record in watched_records:
            content_id = str(record["content_id"])
            is_watched = bool(record.get("watched", True))
            base_item = candidates.get(content_id) or remote_by_id.get(content_id)
            if base_item is None:
                if not is_watched:
                    continue
                base_item = stremio_payloads.new_library_item(
                    record,
                    now,
                    in_library=False,
                )
            candidates[content_id] = stremio_payloads.with_watch_state(
                base_item, record, series_meta.get(content_id, {}),
            )

        for record in progress_records:
            content_id = str(record["content_id"])
            base_item = (
                candidates.get(content_id)
                or remote_by_id.get(content_id)
                or stremio_payloads.new_library_item(record, now, in_library=False)
            )
            candidates[content_id] = stremio_payloads.with_progress_state(
                base_item, record, series_meta.get(content_id, {}),
                in_library=content_id in current_library_ids,
            )

        changes = []
        for content_id, candidate in candidates.items():
            candidate["_mtime"] = now
            existing = remote_by_id.get(content_id)
            if existing is None or not stremio_payloads.same_item(existing, candidate):
                changes.append(candidate)
        for start in range(0, len(changes), BATCH_SIZE):
            await stremio.datastore_put(
                conn.token,
                changes[start : start + BATCH_SIZE],
            )
        if progress_records:
            progress_ids = sorted({str(record["content_id"]) for record in progress_records})
            confirmed = await stremio.datastore_get(conn.token, ids=progress_ids)
            confirmed_by_id = {str(item.get("_id")): item for item in confirmed}
            for content_id in progress_ids:
                expected_state = (candidates.get(content_id) or {}).get("state") or {}
                actual = confirmed_by_id.get(content_id) or {}
                state = actual.get("state") or {}
                if (int(state.get("timeOffset") or 0) != int(expected_state.get("timeOffset") or 0)
                    or str(state.get("video_id") or "") != str(expected_state.get("video_id") or "")
                    or actual.get("removed")):
                    raise stremio.StremioAPIError(
                        "Stremio did not confirm the pushed Continue Watching progress"
                    )
        if clear_progress_ids:
            confirmed = await stremio.datastore_get(conn.token, ids=sorted(clear_progress_ids))
            confirmed_by_id = {str(item.get("_id")): item for item in confirmed if isinstance(item, dict)}
            if any(
                content_id not in confirmed_by_id
                or (confirmed_by_id[content_id].get("state") or {}).get("timeOffset") not in (0, "0", None)
                for content_id in clear_progress_ids
            ):
                raise stremio.StremioAPIError("Stremio did not confirm removal of stale progress")

    if conn.push_collection and not watch_only:
        conn.stremio_pushed_library_ids = sorted(current_library_ids)
    return len(changes)
