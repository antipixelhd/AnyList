from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from models import Media, Show
from models.base import MediaType
from models.tracking import TrackedEntry, TrackingActivity


def suppress_initial_import_rating(entry: TrackedEntry, now: datetime | None = None) -> bool:
    """Keep rating-only edits to an initially imported completion off the feed."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    imported_at = entry.initial_import_completed_at
    return bool(
        entry.status == "completed" and imported_at is not None
        and imported_at <= now < imported_at + timedelta(days=7)
    )


def merge_activity_payload(newer: dict | None, older: dict | None) -> dict:
    """Combine persisted activity details while retaining the newest position."""
    current = dict(newer or {})
    previous = dict(older or {})
    current["episodes_watched"] = int(current.get("episodes_watched", 0)) + int(previous.get("episodes_watched", 0))
    current["finished_seasons"] = sorted(set(current.get("finished_seasons", [])) | set(previous.get("finished_seasons", [])))
    if "status_start" not in current:
        current["status_changed"] = bool(current.get("status_changed") or previous.get("status_changed"))
    if "rating_start_score" not in current:
        current["rating_changed"] = bool(current.get("rating_changed") or previous.get("rating_changed"))
    if "rating_start_score" not in current and ("rating_first" in current or "rating_first" in previous):
        current["rating_first"] = bool(current.get("rating_first") or previous.get("rating_first"))
    # The day card describes the full change, from its earliest score to the
    # latest one, even when several writes or legacy rows were merged.
    if "rating_start_score" not in current and previous.get("previous_score") is not None:
        current["previous_score"] = previous["previous_score"]
    if current.get("progress") is None and previous.get("progress") is not None:
        current["progress"] = previous["progress"]
    if not current.get("position") and previous.get("position"):
        current["position"] = previous["position"]
    return current


def has_activity_event(status: str, details: dict) -> bool:
    """Only persist and present cards with a qualifying daily action."""
    status_event = bool(details.get("status_changed")) and (
        status != "watching" or details.get("started_watching") is True
    )
    return bool(status_event or details.get("rating_changed")
                or details.get("episodes_watched") or details.get("finished_seasons"))


def _record_status_details(details: dict, *, status: str, status_changed: bool,
                           previous_status: str | None, first_watching: bool | None) -> None:
    if not status_changed:
        details["status_changed"] = bool(details.get("status_changed"))
        return
    if "status_start" not in details:
        details["status_start"] = previous_status
    details["status_changed"] = details["status_start"] != status
    if status == "watching":
        details["started_watching"] = bool(
            details.get("started_watching") or
            (previous_status is None if first_watching is None else first_watching)
        )


def _record_rating_details(details: dict, *, rating_changed: bool,
                           previous_score: float | None, score: float | None) -> None:
    if not rating_changed:
        details["rating_changed"] = bool(details.get("rating_changed"))
        return
    if "rating_start_score" not in details:
        details["rating_start_score"] = (
            None if details.get("rating_first") else details.get("previous_score", previous_score)
        )
    start_score = details["rating_start_score"]
    if start_score is None and score is not None and "rating_first_score" not in details:
        details["rating_first_score"] = score
    details["rating_changed"] = start_score != score
    details["rating_first"] = start_score is None and score is not None
    comparison_score = details.get("rating_first_score") if start_score is None else start_score
    if details["rating_changed"] and comparison_score is not None and comparison_score != score:
        details["previous_score"] = comparison_score
    else:
        details.pop("previous_score", None)


async def series_activity_details(db, media: Media, progress: int) -> tuple[str | None, list[int]]:
    """Return the cumulative SxEy position and fully released, watched seasons."""
    if media.media_type != MediaType.series or progress <= 0:
        return None, []
    episodes, expected = await _series_activity_catalog(db, media)
    if not episodes:
        return None, []
    watched = episodes[:min(progress, len(episodes))]
    latest = watched[-1]
    return f"S{latest.season_number}E{latest.episode_number}", _finished_seasons(episodes, expected, progress)


async def _series_activity_catalog(db, media: Media):
    identities = []
    if media.tmdb_id:
        identities.append(Show.tmdb_id == media.tmdb_id)
    if media.tvdb_id:
        identities.append(Show.tvdb_id == media.tvdb_id)
    show = (await db.execute(select(Show).where(or_(*identities)))).scalars().first() if identities else None
    if not show:
        return [], {}
    imported_release = Media.tmdb_data["tracking_import_released"].as_boolean().is_(True)
    query = select(Media).where(
        Media.show_id == show.id,
        Media.media_type == MediaType.episode,
        Media.season_number > 0,
        or_(
            (Media.release_date.is_not(None) & (Media.release_date <= date.today().isoformat())),
            imported_release,
        ),
    )
    catalogue_ids = (media.tmdb_data or {}).get("tracking_episode_ids")
    if catalogue_ids is not None:
        provider = (media.tmdb_data or {}).get("tracking_catalogue_provider", "tmdb")
        identity = Media.tvdb_id if provider == "tvdb" else Media.tmdb_id
        query = query.where(or_(identity.in_(catalogue_ids), imported_release))
    episodes = (await db.execute(query.order_by(Media.season_number, Media.episode_number))).scalars().all()
    expected = {
        int(season.get("season_number", 0)): int(season.get("episode_count", 0))
        for season in (media.tmdb_data or {}).get("seasons", [])
        if isinstance(season, dict)
    }
    return episodes, expected


def _finished_seasons(episodes, expected: dict[int, int], progress: int) -> list[int]:
    watched = episodes[:min(progress, len(episodes))]
    finished = []
    for season_number in {episode.season_number for episode in watched if (episode.season_number or 0) > 0}:
        released_count = sum(1 for episode in episodes if episode.season_number == season_number)
        watched_count = sum(1 for episode in watched if episode.season_number == season_number)
        if expected.get(season_number, 0) > 0 and released_count == expected[season_number] == watched_count:
            finished.append(season_number)
    return sorted(finished)


async def series_activity_span(db, media: Media, start: int, end: int) -> tuple[str | None, str | None, list[int]]:
    """Describe only episodes watched between two cumulative progress values."""
    if media.media_type != MediaType.series or end <= start:
        return None, None, []
    episodes, expected = await _series_activity_catalog(db, media)
    if start >= len(episodes) or end > len(episodes):
        return None, None, []
    first, last = episodes[start], episodes[end - 1]
    beginning = f"S{first.season_number}E{first.episode_number}"
    ending = f"S{last.season_number}E{last.episode_number}"
    finished = sorted(set(_finished_seasons(episodes, expected, end)) - set(_finished_seasons(episodes, expected, start)))
    return beginning, ending, finished


async def record_progress_activity(db, *, user_id: int, media: Media, previous_progress: int,
                                   progress: int, status: str, score: float | None,
                                   status_changed: bool = False, rating_changed: bool = False,
                                   previous_status: str | None = None, first_watching: bool | None = None,
                                   previous_score: float | None = None,
                                   now: datetime | None = None) -> TrackingActivity | None:
    """Keep today's episode card equal to net forward progress after corrections."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    start = datetime(now.year, now.month, now.day)
    row = (await db.execute(select(TrackingActivity).where(
        TrackingActivity.user_id == user_id, TrackingActivity.media_id == media.id,
        TrackingActivity.created_at >= start, TrackingActivity.created_at < start + timedelta(days=1),
    ).order_by(TrackingActivity.created_at.desc()).limit(1))).scalar_one_or_none()
    details = dict(row.payload or {}) if row else {}
    baseline = details.get("progress_start")
    if baseline is None:
        prior_end = details.get("progress")
        prior_count = int(details.get("episodes_watched") or 0)
        baseline = max(0, int(prior_end) - prior_count) if prior_end is not None and prior_count else previous_progress
    baseline = int(baseline)
    watched = max(0, progress - baseline)
    details["episodes_watched"] = watched
    details["progress_start"] = baseline
    details["progress"] = progress
    details["position_start"], details["position"], details["finished_seasons"] = (
        await series_activity_span(db, media, baseline, progress)
    )
    _record_status_details(details, status=status, status_changed=status_changed,
                           previous_status=previous_status, first_watching=first_watching)
    _record_rating_details(details, rating_changed=rating_changed,
                           previous_score=previous_score, score=score)
    if not has_activity_event(status, details):
        if row:
            await db.delete(row)
        return None
    if row:
        row.status, row.score, row.payload, row.created_at = status, score, details, now
    else:
        row = TrackingActivity(user_id=user_id, media_id=media.id, status=status, score=score,
                               payload=details, created_at=now)
        db.add(row)
    if status_changed:
        await db.execute(update(TrackedEntry).where(
            TrackedEntry.user_id == user_id, TrackedEntry.media_id == media.id,
            TrackedEntry.initial_import_completed_at.is_not(None),
        ).values(initial_import_completed_at=None))
    return row


async def record_daily_activity(db, *, user_id: int, media_id: int, status: str, score: float | None,
                                episodes_watched: int = 0, progress: int | None = None,
                                position: str | None = None,
                                finished_seasons: list[int] | None = None,
                                status_changed: bool = False, rating_changed: bool = False,
                                previous_status: str | None = None, first_watching: bool | None = None,
                                previous_score: float | None = None,
                                now: datetime | None = None) -> TrackingActivity | None:
    """Merge one title's updates into its UTC-day activity card."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    start = datetime(now.year, now.month, now.day)
    end = start + timedelta(days=1)
    row = (await db.execute(select(TrackingActivity).where(
        TrackingActivity.user_id == user_id, TrackingActivity.media_id == media_id,
        TrackingActivity.created_at >= start, TrackingActivity.created_at < end,
    ).order_by(TrackingActivity.created_at.desc()).limit(1))).scalar_one_or_none()
    details = merge_activity_payload(row.payload if row else None, {"episodes_watched": max(0, episodes_watched)})
    if progress is not None: details["progress"] = progress
    if position is not None: details["position"] = position
    details["finished_seasons"] = sorted(set(details.get("finished_seasons", [])) | set(finished_seasons or []))
    _record_status_details(details, status=status, status_changed=status_changed,
                           previous_status=previous_status, first_watching=first_watching)
    _record_rating_details(details, rating_changed=rating_changed,
                           previous_score=previous_score, score=score)
    if not has_activity_event(status, details):
        if row:
            await db.delete(row)
        return None
    if row:
        row.status, row.score, row.payload, row.created_at = status, score, details, now
    else:
        row = TrackingActivity(user_id=user_id, media_id=media_id, status=status, score=score,
                               payload=details, created_at=now)
        db.add(row)
    if status_changed:
        # Once the imported status changes, future ratings belong on its
        # ordinary activity card even if the seven-day window has not elapsed.
        await db.execute(update(TrackedEntry).where(
            TrackedEntry.user_id == user_id, TrackedEntry.media_id == media_id,
            TrackedEntry.initial_import_completed_at.is_not(None),
        ).values(initial_import_completed_at=None))
    return row
