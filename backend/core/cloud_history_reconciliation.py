"""Apply newly imported cloud watch events to existing tracked entries safely."""
from datetime import date, datetime, timezone

from sqlalchemy import select

from core.tracking_rules import effective_score
from core.status_provenance import mark_status_change, status_changed_at
from core.web_push import queue_sync_completion_rating
from models import Media, Show, WatchEvent
from models.base import MediaType
from models.tracking import CloudBaseline, SyncReview, TrackedEntry


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _date_value(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _history_changes(entry, *, proposed_status, proposed_start, proposed_finish, proposed_progress):
    fields = []
    proposals = (
        ("status", entry.status, proposed_status),
        ("start_date", _date_value(entry.start_date), _date_value(proposed_start)),
        ("finish_date", _date_value(entry.finish_date), _date_value(proposed_finish)),
        ("progress", entry.progress, proposed_progress),
    )
    for field, previous, proposed in proposals:
        if proposed is not None and previous != proposed:
            fields.append({"field": field, "previous": previous, "proposed": proposed})
    return fields


async def _add_status_conflict(db, *, user_id, provider, entry, proposed_status, changes):
    pending = (await db.execute(select(SyncReview).where(
        SyncReview.user_id == user_id,
        SyncReview.provider == provider,
        SyncReview.media_id == entry.media_id,
        SyncReview.kind == "cloud_conflict",
        SyncReview.state == "pending",
    ).limit(1))).scalar_one_or_none()
    if pending is None:
        label = {"trakt": "Trakt", "simkl": "Simkl", "mdblist": "MDBList"}.get(provider, provider)
        db.add(SyncReview(
            user_id=user_id,
            media_id=entry.media_id,
            provider=provider,
            kind="cloud_conflict",
            previous_status=entry.status,
            proposed_status=proposed_status,
            payload={"changes": changes},
            message=(
                f"{label}: imported watch history differs from your local title data, "
                "but its time cannot be ordered against your local edit. Your local values were kept."
            ),
        ))
    else:
        pending.proposed_status = proposed_status
        pending.payload = {"changes": changes}


async def _add_applied_notification(db, *, user_id, provider, entry, previous_status, changes):
    if not changes:
        return
    label = {"trakt": "Trakt", "simkl": "Simkl", "mdblist": "MDBList"}.get(provider, provider)
    db.add(SyncReview(
        user_id=user_id,
        media_id=entry.media_id,
        provider=provider,
        kind="cloud_update",
        state="confirmed",
        previous_status=previous_status,
        proposed_status=entry.status,
        priority="low",
        payload={"changes": changes},
        message=f"{label}: newer watch history updated this title.",
    ))


async def reconcile_cloud_watch_events(
    db,
    *,
    user_id: int,
    provider: str,
    new_media_ids: set[int],
) -> dict[str, int]:
    stats = {"applied": 0, "conflicts": 0, "preserved": 0}
    if not new_media_ids:
        return stats
    initial_import = (await db.execute(select(CloudBaseline.id).where(
        CloudBaseline.user_id == user_id,
        CloudBaseline.provider == provider,
    ))).first() is None
    rows = (await db.execute(
        select(WatchEvent, Media)
        .join(Media, Media.id == WatchEvent.media_id)
        .where(WatchEvent.user_id == user_id, Media.id.in_(new_media_ids))
    )).all()

    roots: dict[int, dict] = {}
    show_ids = {media.show_id for _, media in rows if media.media_type == MediaType.episode and media.show_id}
    shows = {show.id: show for show in (await db.execute(
        select(Show).where(Show.id.in_(show_ids))
    )).scalars()} if show_ids else {}
    series = (await db.execute(select(Media).where(Media.media_type == MediaType.series))).scalars().all()
    series_by_tmdb = {media.tmdb_id: media for media in series if media.tmdb_id}
    series_by_tvdb = {media.tvdb_id: media for media in series if media.tvdb_id}

    for event, media in rows:
        root = media
        if media.media_type == MediaType.episode:
            show = shows.get(media.show_id)
            root = (series_by_tmdb.get(show.tmdb_id) or series_by_tvdb.get(show.tvdb_id)) if show else None
        if root is None:
            continue
        item = roots.setdefault(root.id, {"media": root, "events": []})
        item["events"].append((event, media))

    for root_id, item in roots.items():
        entry = (await db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == user_id,
            TrackedEntry.media_id == root_id,
        ))).scalar_one_or_none()
        if entry is None:
            continue
        latest_at = max(
            (_naive_utc(event.watched_at) for event, _ in item["events"] if event.watched_at),
            default=None,
        )
        earliest_at = min(
            (_naive_utc(event.watched_at) for event, _ in item["events"] if event.watched_at),
            default=None,
        )
        local_at = status_changed_at(entry)
        media = item["media"]
        proposed_status = "completed" if media.media_type == MediaType.movie else "watching"
        released = []
        watched_ids: set[int] = set()
        inferred_previous = []
        proposed_progress = entry.progress
        if media.media_type == MediaType.series:
            show = next((shows.get(child.show_id) for _, child in item["events"] if child.show_id), None)
            if show:
                released = (await db.execute(select(Media).where(
                    Media.show_id == show.id,
                    Media.media_type == MediaType.episode,
                    Media.season_number > 0,
                    Media.release_date.is_not(None),
                    Media.release_date <= date.today().isoformat(),
                ).order_by(Media.season_number, Media.episode_number))).scalars().all()
                watched_ids = set((await db.execute(select(WatchEvent.media_id).where(
                    WatchEvent.user_id == user_id,
                    WatchEvent.media_id.in_([episode.id for episode in released]),
                    WatchEvent.completed.is_(True),
                ))).scalars()) if released else set()
                last = max((index for index, episode in enumerate(released) if episode.id in watched_ids), default=-1)
                inferred_previous = [
                    episode for episode in released[:last + 1]
                    if episode.id not in watched_ids
                ]
                proposed_progress = len(watched_ids) + len(inferred_previous)
                complete = bool(
                    (media.tmdb_data or {}).get("tracking_catalogue_refreshed_at")
                    and released
                    and proposed_progress == len(released)
                )
                proposed_status = "completed" if complete else "watching"

        proposed_start = entry.start_date
        proposed_finish = entry.finish_date
        if earliest_at and media.media_type == MediaType.series:
            proposed_start = earliest_at.date()
        if latest_at and proposed_status == "completed":
            proposed_finish = latest_at.date()
        changes = _history_changes(
            entry,
            proposed_status=proposed_status,
            proposed_start=proposed_start,
            proposed_finish=proposed_finish,
            proposed_progress=proposed_progress,
        )

        if entry.status == "completed":
            # First release never reopens a completed title merely because it
            # was watched again or gained later episodes.
            for episode in inferred_previous:
                db.add(WatchEvent(
                    user_id=user_id,
                    media_id=episode.id,
                    completed=True,
                    provisional=True,
                    watched_at=None,
                ))
            if released:
                entry.progress = len(watched_ids) + len(inferred_previous)
            stats["preserved"] += 1
            continue
        reliably_newer = bool(latest_at and local_at and latest_at > local_at)
        reliably_older = bool(latest_at and local_at and latest_at < local_at)
        empty_local = (
            entry.status == "planning"
            and not entry.progress
            and entry.start_date is None
            and entry.finish_date is None
        )
        may_apply = reliably_newer or (initial_import and empty_local)
        if not may_apply:
            if reliably_older:
                stats["preserved"] += 1
            else:
                await _add_status_conflict(
                    db,
                    user_id=user_id,
                    provider=provider,
                    entry=entry,
                    proposed_status=proposed_status,
                    changes=changes,
                )
                stats["conflicts"] += 1
            continue

        previous_status = entry.status
        previous_progress = entry.progress
        changed = previous_status != proposed_status
        for episode in inferred_previous:
            db.add(WatchEvent(
                user_id=user_id,
                media_id=episode.id,
                completed=True,
                provisional=True,
                watched_at=None,
            ))
        if released:
            entry.progress = proposed_progress
        entry.status = proposed_status
        mark_status_change(entry, provider, latest_at)
        entry.start_date = proposed_start
        entry.finish_date = proposed_finish
        progress_changed = entry.progress != previous_progress
        if (changed or progress_changed) and not initial_import:
            from core.activity import record_daily_activity, series_activity_details
            position, finished = await series_activity_details(db, media, entry.progress)
            await record_daily_activity(
                db,user_id=user_id,media_id=root_id,status=entry.status,
                score=effective_score(entry.rating_mode,entry.manual_score,entry.season_scores),
                episodes_watched=max(0,entry.progress-previous_progress),progress=entry.progress,
                position=position,finished_seasons=finished,status_changed=changed,
            )
        if changed and not initial_import:
            if entry.status == "completed":
                await queue_sync_completion_rating(
                    db, user_id=user_id, media=media, entry=entry, source=provider,
                )
        if not initial_import:
            await _add_applied_notification(
                db,
                user_id=user_id,
                provider=provider,
                entry=entry,
                previous_status=previous_status,
                changes=changes,
            )
        stats["applied"] += 1
    await db.commit()
    return stats
