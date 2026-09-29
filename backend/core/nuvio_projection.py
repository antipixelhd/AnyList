"""Project canonical library, watch history, and resume state for Nuvio.

These queries do not send provider writes or commit the caller's transaction.
Payload formatting lives in nuvio_payloads; sync/delivery callers own transport.
"""

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import nuvio, nuvio_payloads, tmdb
from core.db_queries import select_in_chunks
from models.base import MediaType
from models.collection import Collection
from models.events import WatchEvent
from models.media import Media
from models.playback_progress import PlaybackProgress
from models.show import Show
from models.streaming_library import StreamingLibraryIntent

logger = logging.getLogger(__name__)
TMDB_CONCURRENCY = 5


async def ensure_imdb_ids(
    media_rows: list[Media],
    shows_by_id: dict[int, Show],
    api_key: str | None,
    shows_by_tmdb: dict[int, Show] | None = None,
) -> None:
    shows_by_tmdb = shows_by_tmdb or {}
    if not api_key:
        return

    targets: dict[tuple[str, int], Media | Show] = {}
    for media in media_rows:
        if media.media_type == MediaType.episode:
            show = shows_by_id.get(media.show_id)
            if show and show.tmdb_id and not nuvio_payloads.imdb_id(show):
                targets[("tv", show.tmdb_id)] = show
        elif media.tmdb_id:
            entity: Media | Show = (
                shows_by_tmdb.get(media.tmdb_id)
                if media.media_type == MediaType.series
                else media
            ) or media
            if nuvio_payloads.imdb_id(entity):
                continue
            target_type = "movie" if media.media_type == MediaType.movie else "tv"
            targets[(target_type, media.tmdb_id)] = entity
    if not targets:
        return

    semaphore = asyncio.Semaphore(TMDB_CONCURRENCY)

    async def fetch_imdb_id(target_type: str, tmdb_id: int, entity: Media | Show) -> None:
        async with semaphore:
            try:
                external_ids = await tmdb.get_external_ids(tmdb_id, target_type, api_key=api_key)
            except Exception as exc:
                logger.warning(
                    "Failed to resolve outbound Nuvio IMDb ID for TMDB %s (%s): %s",
                    tmdb_id,
                    target_type,
                    exc,
                )
                return
        imdb_id = str(external_ids.get("imdb_id") or "").strip()
        if not (imdb_id.startswith("tt") and imdb_id[2:].isdigit()):
            return
        tmdb_data = dict(entity.tmdb_data or {})
        stored_external_ids = dict(tmdb_data.get("external_ids") or {})
        stored_external_ids["imdb_id"] = imdb_id
        tmdb_data["external_ids"] = stored_external_ids
        entity.tmdb_data = tmdb_data

    await asyncio.gather(
        *[
            fetch_imdb_id(target_type, tmdb_id, entity)
            for (target_type, tmdb_id), entity in targets.items()
        ]
    )
    logger.info("Resolved %s outbound Nuvio IMDb identifiers through TMDB", len(targets))


async def build_library_items(
    db: AsyncSession,
    user_id: int,
    api_key: str | None = None,
    baseline=None,
) -> list[dict]:
    # An explicit Library removal must not be undone by another source's
    # CollectionFile during the next Stremio/Nuvio full push.
    explicitly_removed = select(StreamingLibraryIntent.media_id).where(
        StreamingLibraryIntent.user_id == user_id,
        StreamingLibraryIntent.desired.is_(False),
    )
    result = await db.execute(
        select(Collection.added_at, Media)
        .join(Media, Media.id == Collection.media_id)
        .where(Collection.user_id == user_id, Collection.media_id.not_in(explicitly_removed))
        .order_by(Collection.added_at, Collection.id)
    )
    rows = result.all()
    show_ids = {
        media.show_id
        for _, media in rows
        if media.media_type == MediaType.episode and media.show_id is not None
    }
    series_tmdb_ids = {
        media.tmdb_id
        for _, media in rows
        if media.media_type == MediaType.series and media.tmdb_id is not None
    }
    shows_by_id: dict[int, Show] = {}
    shows_by_tmdb: dict[int, Show] = {}
    if show_ids:
        shows = await select_in_chunks(
            db,
            lambda chunk: select(Show).where(Show.id.in_(chunk)),
            list(show_ids),
        )
        shows_by_id = {show.id: show for show in shows}
    if series_tmdb_ids:
        series_shows = await select_in_chunks(
            db,
            lambda chunk: select(Show).where(Show.tmdb_id.in_(chunk)),
            list(series_tmdb_ids),
        )
        shows_by_tmdb = {
            show.tmdb_id: show
            for show in series_shows
            if show.tmdb_id is not None
        }
    await ensure_imdb_ids(
        [media for _, media in rows],
        shows_by_id,
        api_key,
        shows_by_tmdb,
    )

    items_by_content_id: dict[str, dict] = {}
    for added_at, media in rows:
        show = (
            shows_by_id.get(media.show_id)
            if media.media_type == MediaType.episode
            else shows_by_tmdb.get(media.tmdb_id)
        )
        item = nuvio_payloads.library_item(media, added_at, show)
        if item:
            item = nuvio_payloads.remap_payload(item, media, show, baseline)
            items_by_content_id.setdefault(item["content_id"], item)
    return list(items_by_content_id.values())


async def build_watched_items(
    db: AsyncSession,
    user_id: int,
    media_ids: set[int] | None = None,
    api_key: str | None = None,
    *,
    include_unknown_dates: bool = True,
    baseline=None,
    tracked_only: bool = False,
) -> list[dict]:
    """Project canonical WatchEvents, using the event receipt time if needed.

    A missing watch timestamp is not evidence that an episode is unwatched.
    Nuvio requires a numeric date, so an older null-dated event uses the time
    AnyList first recorded it as a clearly inferred provider-side fallback.
    """
    event_query = (
        select(WatchEvent.media_id, WatchEvent.watched_at, WatchEvent.created_at)
        .where(WatchEvent.user_id == user_id, WatchEvent.completed == True)
        .order_by(WatchEvent.watched_at.desc().nulls_last())
    )
    if media_ids is not None:
        if not media_ids:
            return []
        event_query = event_query.where(WatchEvent.media_id.in_(media_ids))
    event_result = await db.execute(event_query)
    latest_watched_at: dict[int, datetime | None] = {}
    for media_id, watched_at, created_at in event_result.all():
        # Older imported events can still have an unknown watch date. Nuvio's
        # watched-items API and Android model require a numeric epoch, so use
        # the time AnyList first recorded that event as an inferred fallback.
        latest_watched_at.setdefault(media_id, watched_at or created_at)
    if not latest_watched_at:
        return []

    media_rows = await select_in_chunks(
        db,
        lambda chunk: select(Media).where(Media.id.in_(chunk)),
        list(latest_watched_at),
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

    if tracked_only:
        from models.tracking import TrackedEntry
        tracked_result = await db.execute(
            select(Media.id, Media.tmdb_id, Media.tvdb_id, Media.media_type)
            .join(TrackedEntry, TrackedEntry.media_id == Media.id)
            .where(TrackedEntry.user_id == user_id)
        )
        tracked_ids: set[int] = set()
        tracked_series_tmdb: set[int] = set()
        tracked_series_tvdb: set[int] = set()
        for tracked_id, tmdb_id, tvdb_id, media_type in tracked_result.all():
            tracked_ids.add(tracked_id)
            if media_type == MediaType.series and tmdb_id is not None:
                tracked_series_tmdb.add(tmdb_id)
            if media_type == MediaType.series and tvdb_id is not None:
                tracked_series_tvdb.add(tvdb_id)
        media_rows = [media for media in media_rows
            if media.id in tracked_ids or (
                media.media_type == MediaType.episode
                and media.show_id in shows_by_id
                and (shows_by_id[media.show_id].tmdb_id in tracked_series_tmdb
                    or shows_by_id[media.show_id].tvdb_id in tracked_series_tvdb)
            )]

    await ensure_imdb_ids(media_rows, shows_by_id, api_key)
    items: list[dict] = []
    for media in media_rows:
        item = nuvio_payloads.watched_item(
            media,
            latest_watched_at[media.id],
            shows_by_id.get(media.show_id),
            include_unknown_date=include_unknown_dates,
        )
        if item:
            items.append(nuvio_payloads.remap_payload(item, media, shows_by_id.get(media.show_id), baseline))
    return items


async def build_progress_items(
    db: AsyncSession,
    user_id: int,
    api_key: str | None = None,
    baseline=None,
    next_up_for_watched_series: bool = False,
) -> list[dict]:
    from models.tracking import TrackedEntry

    tracked_result = await db.execute(
        select(TrackedEntry, Media)
        .join(Media, Media.id == TrackedEntry.media_id)
        .where(TrackedEntry.user_id == user_id, TrackedEntry.status == "watching")
        .order_by(Media.id)
    )
    tracked_rows = tracked_result.all()
    if not tracked_rows:
        return []
    entries_by_media = {media.id: entry for entry, media in tracked_rows}
    media_by_id = {media.id: media for _, media in tracked_rows}
    series_media = [media for _, media in tracked_rows if media.media_type == MediaType.series]
    shows_by_id: dict[int, Show] = {}
    if series_media:
        series_tmdb_ids = {media.tmdb_id for media in series_media if media.tmdb_id is not None}
        if series_tmdb_ids:
            shows_result = await db.execute(select(Show).where(Show.tmdb_id.in_(series_tmdb_ids)))
            shows_by_id = {show.id: show for show in shows_result.scalars().all()}
    show_id_to_entry = {
        show.id: entries_by_media[media.id]
        for media in series_media
        for show in shows_by_id.values()
        if media.tmdb_id is not None and show.tmdb_id == media.tmdb_id
    }
    episode_by_show: dict[int, list[Media]] = {}
    if show_id_to_entry:
        episodes_result = await db.execute(select(Media).where(
            Media.show_id.in_(show_id_to_entry), Media.media_type == MediaType.episode,
        ).order_by(Media.show_id, Media.season_number, Media.episode_number))
        for episode in episodes_result.scalars().all():
            episode_by_show.setdefault(episode.show_id, []).append(episode)
            media_by_id[episode.id] = episode

    candidate_media_ids = set(entries_by_media)
    candidate_media_ids.update(episode.id for episodes in episode_by_show.values() for episode in episodes)
    progress_result = await db.execute(
        select(PlaybackProgress, Media)
        .join(Media, Media.id == PlaybackProgress.media_id)
        .where(PlaybackProgress.user_id == user_id, PlaybackProgress.media_id.in_(candidate_media_ids))
        .order_by(Media.id)
    )

    def fresh_for(progress: PlaybackProgress, entry) -> bool:
        changed_at = entry.status_changed_at
        updated_at = progress.updated_at
        if changed_at is None:
            return True
        if updated_at is None:
            return False
        if changed_at.tzinfo is None:
            changed_at = changed_at.replace(tzinfo=timezone.utc)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return updated_at >= changed_at

    fresh_progress: dict[int, PlaybackProgress] = {}
    for progress, media in progress_result.all():
        owner = entries_by_media.get(media.id) or show_id_to_entry.get(media.show_id)
        if owner is not None and fresh_for(progress, owner):
            fresh_progress[media.id] = progress

    def synthetic_at(entry) -> datetime:
        # Anchor synthetic resume activity to the latest stored status decision
        # so repeated full pushes do not make the title look newly played.
        value = entry.status_changed_at or entry.updated_at or datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    progress_by_media = dict(fresh_progress)
    series_with_watched_history: set[int] = set()
    for media_id, entry in entries_by_media.items():
        media = media_by_id[media_id]
        if media.media_type == MediaType.series:
            show = next((row for row in shows_by_id.values() if row.tmdb_id == media.tmdb_id), None)
            if show is None:
                continue
            episodes = episode_by_show.get(show.id, [])
            if not episodes:
                continue
            released = [episode for episode in episodes
                if not episode.release_date or episode.release_date <= datetime.now(timezone.utc).date().isoformat()]
            if not released:
                continue
            episode_ids = {episode.id for episode in released}
            watched_result = await db.execute(select(WatchEvent.media_id).where(
                WatchEvent.user_id == user_id, WatchEvent.completed.is_(True), WatchEvent.media_id.in_(episode_ids),
            ))
            watched_ids = set(watched_result.scalars().all())
            if watched_ids:
                series_with_watched_history.add(media_id)
            # A completed episode often retains a 100% PlaybackProgress row.
            # Sending that row as the show's resume position hides the next
            # episode from Continue Watching. Publish only one active resume
            # per series, or seed the next unwatched episode at one second.
            for episode in episodes:
                progress_by_media.pop(episode.id, None)
            active = [episode for episode in released
                if episode.id in fresh_progress
                and episode.id not in watched_ids
                and float(fresh_progress[episode.id].progress_percent or 0) < 0.95]
            if active:
                def activity_time(episode: Media) -> float:
                    value = fresh_progress[episode.id].updated_at
                    if value is None:
                        return 0
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=timezone.utc)
                    return value.timestamp()
                target = max(active, key=activity_time)
                progress_by_media[target.id] = fresh_progress[target.id]
            elif not next_up_for_watched_series or not watched_ids:
                target = next((episode for episode in released if episode.id not in watched_ids), released[0])
                progress_by_media[target.id] = SimpleNamespace(
                    progress_seconds=1, progress_percent=0.01, updated_at=synthetic_at(entry),
                    synthetic_resume=True,
                )
        elif media.media_type in (MediaType.movie, MediaType.episode) and media_id not in fresh_progress:
            progress_by_media[media_id] = SimpleNamespace(
                progress_seconds=1, progress_percent=0.01, updated_at=synthetic_at(entry),
                synthetic_resume=True,
            )

    media_rows = [media_by_id[media_id] for media_id in progress_by_media]
    show_ids = {media.show_id for media in media_rows
        if media.media_type == MediaType.episode and media.show_id is not None}
    if show_ids:
        shows_result = await db.execute(select(Show).where(Show.id.in_(show_ids)))
        shows_by_id.update({show.id: show for show in shows_result.scalars().all()})
    await ensure_imdb_ids(media_rows, shows_by_id, api_key)

    items: list[dict] = []
    for media_id, progress in progress_by_media.items():
        media = media_by_id[media_id]
        item = nuvio_payloads.progress_item(
            progress, media, shows_by_id.get(media.show_id),
            preserve_fractional_seconds=next_up_for_watched_series,
        )
        if item:
            if next_up_for_watched_series and getattr(progress, "synthetic_resume", False):
                item["synthetic_resume"] = True
            items.append(nuvio_payloads.remap_payload(item, media, shows_by_id.get(media.show_id), baseline))
    projected_content_ids = {str(item["content_id"]) for item in items}
    missing = []
    for _entry, media in tracked_rows:
        if media.media_type not in (MediaType.movie, MediaType.series):
            continue
        content_id = nuvio_payloads.imdb_id(media)
        if media.media_type == MediaType.series:
            show = next((row for row in shows_by_id.values() if row.tmdb_id == media.tmdb_id), None)
            content_id = nuvio_payloads.imdb_id(show) or content_id
        if content_id:
            content_id = nuvio_payloads.remap_payload({"content_id": content_id}, media, None, baseline)["content_id"]
        if not content_id or (
            content_id not in projected_content_ids
            and (not next_up_for_watched_series or media.id not in series_with_watched_history)
        ):
            missing.append(media.title)
    if missing:
        raise nuvio.NuvioAPIError(
            f"Cannot publish Continue Watching for {len(missing)} watching title(s): "
            + ", ".join(missing[:3])
        )
    return items


async def progress_keys_to_clear(db: AsyncSession, user_id: int, connection_id: int,
                                        remote_rows: list[dict]) -> list[str]:
    """Find mapped Nuvio resumes whose local title is no longer Watching."""
    from models.tracking import StreamBaseline, TrackedEntry, TrackingDeletion

    baseline = await db.get(StreamBaseline, connection_id)
    mappings = (baseline.snapshot or {}).get("mappings", {}) if baseline else {}
    statuses_result = await db.execute(
        select(Media, TrackedEntry.status)
        .join(TrackedEntry, TrackedEntry.media_id == Media.id)
        .where(TrackedEntry.user_id == user_id,
            Media.media_type.in_([MediaType.movie, MediaType.series]))
    )
    status_by_media: dict[tuple[int, str], str] = {}
    status_by_external: dict[tuple[str, str], str] = {}
    status_rows = statuses_result.all()
    for media, status in status_rows:
        media_type = media.media_type.value if hasattr(media.media_type, "value") else str(media.media_type)
        if media.tmdb_id is not None:
            status_by_media[(int(media.tmdb_id), media_type)] = status
        if (content_id := nuvio_payloads.imdb_id(media)):
            status_by_external[(content_id, media_type)] = status
    deleted_result = await db.execute(
        select(Media, TrackingDeletion.deleted_at)
        .join(TrackingDeletion, TrackingDeletion.media_id == Media.id)
        .where(TrackingDeletion.user_id == user_id,
            Media.media_type.in_([MediaType.movie, MediaType.series]))
    )
    for media, _deleted_at in deleted_result.all():
        media_type = media.media_type.value if hasattr(media.media_type, "value") else str(media.media_type)
        if (content_id := nuvio_payloads.imdb_id(media)):
            status_by_external[(content_id, media_type)] = "deleted"

    remote_series = [row for row in remote_rows
        if str(row.get("content_type") or "").lower() in ("series", "tv")]
    watched_series_content_ids: set[str] = set()
    outbound = (baseline.snapshot or {}).get("outbound", {}) if baseline else {}
    if remote_series:
        watching_series = [media for media, status in status_rows
            if status == "watching" and media.media_type == MediaType.series]
        series_tmdb_ids = {media.tmdb_id for media in watching_series if media.tmdb_id is not None}
        show_id_by_tmdb: dict[int, int] = {}
        if series_tmdb_ids:
            show_result = await db.execute(select(Show).where(Show.tmdb_id.in_(series_tmdb_ids)))
            show_id_by_tmdb = {show.tmdb_id: show.id
                for show in show_result.scalars().all()
                if show.tmdb_id is not None}
        show_ids = set(show_id_by_tmdb.values())
        if show_ids:
            watched_show_result = await db.execute(
                select(Media.show_id)
                .join(WatchEvent, WatchEvent.media_id == Media.id)
                .where(WatchEvent.user_id == user_id, WatchEvent.completed.is_(True),
                    Media.media_type == MediaType.episode, Media.show_id.in_(show_ids))
                .distinct()
            )
            watched_show_ids = set(watched_show_result.scalars().all())
            watched_tmdb_ids = {tmdb_id for tmdb_id, show_id in show_id_by_tmdb.items()
                if show_id in watched_show_ids}
            mappings = (baseline.snapshot or {}).get("mappings", {}) if baseline else {}
            for media in watching_series:
                if media.tmdb_id not in watched_tmdb_ids:
                    continue
                if media.tmdb_id is not None:
                    for key, value in mappings.items():
                        try:
                            matches = int(value) == media.tmdb_id
                        except (TypeError, ValueError):
                            matches = False
                        if matches:
                            watched_series_content_ids.add(str(key))
                if content_id := nuvio_payloads.imdb_id(media):
                    watched_series_content_ids.add(str(content_id))

    clear_keys: list[str] = []
    for row in remote_rows:
        content_id = str(row.get("content_id") or "")
        content_type = str(row.get("content_type") or "").lower()
        if content_type == "tv":
            content_type = "series"
        mapped_tmdb_id = mappings.get(content_id)
        previously_pushed = content_id in ((baseline.snapshot or {}).get("outbound", {}) if baseline else {})
        known = mapped_tmdb_id is not None or (content_id, content_type) in status_by_external or previously_pushed
        if not known:
            continue
        status = status_by_media.get((int(mapped_tmdb_id), content_type)) if mapped_tmdb_id is not None else None
        if status is None:
            status = status_by_external.get((content_id, content_type))
        if status == "watching":
            # Clear the synthetic resume this service created before it began
            # projecting watched series through Next Up. Require an exact
            # baseline echo for legacy rows because a real one-second playback
            # position is also possible.
            if content_type == "series" and content_id in watched_series_content_ids:
                key = str(row.get("progress_key") or "")
                prior = outbound.get(content_id, {})
                fields_match = (
                    prior.get("action") == "upsert"
                    and prior.get("progress_key") == key
                    and prior.get("position") == 1000
                    and row.get("position") == 1000
                    and all(prior.get(field) == row.get(field)
                        for field in ("duration", "season", "episode"))
                )
                timestamp_fields = ("last_watched", "updated_at", "observed_at")
                remote_timestamps = [field for field in timestamp_fields if row.get(field) is not None]
                timestamps_match = all(
                    prior.get(field) == row.get(field)
                    for field in remote_timestamps
                )
                has_legacy_echo_time = bool(remote_timestamps) and timestamps_match
                explicit_seed = prior.get("synthetic_resume") is True
                if fields_match and timestamps_match and (explicit_seed or has_legacy_echo_time):
                    if not key:
                        raise nuvio.NuvioAPIError(
                            "Nuvio progress row has no progress_key; refusing an unsafe clear"
                        )
                    clear_keys.append(key)
            continue
        progress_key = row.get("progress_key")
        if not progress_key:
            raise nuvio.NuvioAPIError("Nuvio progress row has no progress_key; refusing an unsafe clear")
        clear_keys.append(str(progress_key))
    return list(dict.fromkeys(clear_keys))


async def progress_mappings_for_items(db: AsyncSession, user_id: int,
                                            progress_items: list[dict]) -> dict[str, int]:
    from models.tracking import TrackedEntry

    content_ids = {str(item.get("content_id") or "") for item in progress_items}
    if not content_ids:
        return {}
    result = await db.execute(
        select(Media, TrackedEntry.status)
        .join(TrackedEntry, TrackedEntry.media_id == Media.id)
        .where(TrackedEntry.user_id == user_id, TrackedEntry.status == "watching",
            Media.media_type.in_([MediaType.movie, MediaType.series]))
    )
    mappings: dict[str, int] = {}
    for media, _status in result.all():
        content_id = nuvio_payloads.imdb_id(media)
        if content_id in content_ids and media.tmdb_id is not None:
            mappings[content_id] = int(media.tmdb_id)
    return mappings
