from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select

from models import Media, Show
from models.base import MediaType
from models.tracking import TrackingActivity


def merge_activity_payload(newer: dict | None, older: dict | None) -> dict:
    """Combine persisted activity details while retaining the newest position."""
    current = dict(newer or {})
    previous = dict(older or {})
    current["episodes_watched"] = int(current.get("episodes_watched", 0)) + int(previous.get("episodes_watched", 0))
    current["finished_seasons"] = sorted(set(current.get("finished_seasons", [])) | set(previous.get("finished_seasons", [])))
    current["status_changed"] = bool(current.get("status_changed") or previous.get("status_changed"))
    current["rating_changed"] = bool(current.get("rating_changed") or previous.get("rating_changed"))
    if current.get("progress") is None and previous.get("progress") is not None:
        current["progress"] = previous["progress"]
    if not current.get("position") and previous.get("position"):
        current["position"] = previous["position"]
    return current


async def series_activity_details(db, media: Media, progress: int) -> tuple[str | None, list[int]]:
    """Return the cumulative SxEy position and fully released, watched seasons."""
    if media.media_type != MediaType.series or progress <= 0:
        return None, []
    identities = []
    if media.tmdb_id:
        identities.append(Show.tmdb_id == media.tmdb_id)
    if media.tvdb_id:
        identities.append(Show.tvdb_id == media.tvdb_id)
    show = (await db.execute(select(Show).where(or_(*identities)))).scalars().first() if identities else None
    if not show:
        return None, []
    episodes = (await db.execute(select(Media).where(
        Media.show_id == show.id,
        Media.media_type == MediaType.episode,
        Media.season_number > 0,
        Media.release_date.is_not(None),
        Media.release_date <= date.today().isoformat(),
    ).order_by(Media.season_number, Media.episode_number))).scalars().all()
    if not episodes:
        return None, []
    watched = episodes[:min(progress, len(episodes))]
    latest = watched[-1]
    expected = {
        int(season.get("season_number", 0)): int(season.get("episode_count", 0))
        for season in (media.tmdb_data or {}).get("seasons", [])
        if isinstance(season, dict)
    }
    finished = []
    for season_number in {episode.season_number for episode in watched if (episode.season_number or 0) > 0}:
        released_count = sum(1 for episode in episodes if episode.season_number == season_number)
        watched_count = sum(1 for episode in watched if episode.season_number == season_number)
        if expected.get(season_number, 0) > 0 and released_count == expected[season_number] == watched_count:
            finished.append(season_number)
    return f"S{latest.season_number}E{latest.episode_number}", sorted(finished)


async def record_daily_activity(db, *, user_id: int, media_id: int, status: str, score: float | None,
                                episodes_watched: int = 0, progress: int | None = None,
                                position: str | None = None,
                                finished_seasons: list[int] | None = None,
                                status_changed: bool = False, rating_changed: bool = False,
                                now: datetime | None = None) -> TrackingActivity:
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
    details["status_changed"] = bool(details.get("status_changed") or status_changed)
    details["rating_changed"] = bool(details.get("rating_changed") or rating_changed)
    if row:
        row.status, row.score, row.payload, row.created_at = status, score, details, now
    else:
        row = TrackingActivity(user_id=user_id, media_id=media_id, status=status, score=score,
                               payload=details, created_at=now)
        db.add(row)
    return row
