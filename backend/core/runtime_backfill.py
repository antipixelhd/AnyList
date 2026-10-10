"""Bounded, retryable recovery of durations for completed watch history."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from core import tmdb, tvdb
from core.statistics_facts import positive_number
from models import Media, Show, WatchEvent
from models.base import MediaType

RETRY_AFTER = timedelta(days=7)
BATCH_SIZE = 100


def due(data, now):
    try:
        attempted = datetime.fromisoformat((data or {}).get("runtime_backfill_attempted_at", ""))
        if attempted.tzinfo is None:
            attempted = attempted.replace(tzinfo=timezone.utc)
        return now - attempted >= RETRY_AFTER
    except (ValueError, TypeError):
        return True


async def backfill_history_runtimes(db, api_key=None, *, tvdb_api_key=None, limit=BATCH_SIZE):
    """Only fill absent durations; never rewrite history or infer exact runtimes.

    Season responses are shared within a batch. Episode IDs must match before
    copying their duration, since providers can disagree about episode order.
    Attempts live alongside retained metadata so unavailable data is retried
    weekly rather than requested on every scheduler tick.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - RETRY_AFTER).isoformat()
    attempted = Media.tmdb_data["runtime_backfill_attempted_at"].astext
    rows = (await db.execute(
        select(Media, Show).outerjoin(Show, Show.id == Media.show_id)
        .where(
            Media.media_type.in_([MediaType.movie, MediaType.episode]),
            or_(Media.runtime.is_(None), Media.runtime <= 0),
            select(WatchEvent.id).where(
                WatchEvent.media_id == Media.id, WatchEvent.completed.is_(True),
            ).exists(),
            or_(attempted.is_(None), attempted <= cutoff),
        ).order_by(attempted.asc().nullsfirst(), Media.id).limit(limit)
    )).all()
    result = {"examined": 0, "recovered": 0, "unresolved": 0, "failed": 0}
    seasons = {}
    tvdb_shows = {}
    for media, show in rows:
        if not due(media.tmdb_data, now):
            continue
        result["examined"] += 1
        data = dict(media.tmdb_data or {})
        runtime = positive_number(data.get("runtime"))
        try:
            if not runtime and api_key and media.media_type == MediaType.movie and media.tmdb_id:
                response = await tmdb.get_movie(media.tmdb_id, api_key=api_key, cache_ttl=None)
                if response.get("id") == media.tmdb_id:
                    runtime = positive_number(response.get("runtime"))
            elif not runtime and api_key and show and show.tmdb_id and media.tmdb_id:
                if media.season_number is not None:
                    key = (show.tmdb_id, media.season_number)
                    if key not in seasons:
                        seasons[key] = {}  # Failed seasons are not requested once per episode.
                        seasons[key] = await tmdb.get_season(*key, api_key=api_key, cache_ttl=None)
                    episode = next((e for e in seasons[key].get("episodes", [])
                                    if e.get("id") == media.tmdb_id), {})
                    runtime = positive_number(episode.get("runtime"))
            if not runtime and tvdb_api_key and show and show.tvdb_id and media.tvdb_id:
                if show.tvdb_id not in tvdb_shows:
                    tvdb_shows[show.tvdb_id] = []
                    tvdb_shows[show.tvdb_id] = await tvdb.get_series_episodes(
                        show.tvdb_id, None, tvdb_api_key, cache_ttl=None,
                    )
                episode = next((e for e in tvdb_shows[show.tvdb_id]
                                if e.get("id") == media.tvdb_id), {})
                runtime = positive_number(episode.get("runtime"))
        except Exception:
            # Do not log provider exceptions: URLs may contain credentials.
            result["failed"] += 1
        if runtime and runtime.is_integer():
            media.runtime = int(runtime)
            data["runtime"] = media.runtime
            result["recovered"] += 1
        else:
            result["unresolved"] += 1
        data["runtime_backfill_attempted_at"] = now.isoformat()
        media.tmdb_data = data
    await db.commit()
    return result
