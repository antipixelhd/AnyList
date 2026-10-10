"""Bounded, retryable recovery of durations for completed watch history."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from core import tmdb, tvdb
from core.statistics_facts import positive_number
from models import Media, Show, WatchEvent
from models.base import MediaType
from models.tracking import TrackedEntry
from models.catalogue import CatalogueIdentity

RETRY_AFTER = timedelta(days=7)
BATCH_SIZE = 100
VERSION = 2


def due(data, now):
    try:
        attempted = datetime.fromisoformat((data or {}).get("runtime_backfill_attempted_at", ""))
        if attempted.tzinfo is None:
            attempted = attempted.replace(tzinfo=timezone.utc)
        return now - attempted >= RETRY_AFTER
    except (ValueError, TypeError):
        return True


async def backfill_history_runtimes(db, api_key=None, *, tvdb_api_key=None, limit=BATCH_SIZE, force=False):
    """Recover absent history and planning durations without rewriting watches.

    Season responses are shared within a batch. Episode IDs must match before
    copying their duration, since providers can disagree about episode order.
    Attempts live alongside retained metadata so unavailable data is retried
    weekly rather than requested on every scheduler tick.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - RETRY_AFTER).isoformat()
    attempted = Media.tmdb_data["runtime_backfill_attempted_at"].astext
    planned_title = aliased(Media)
    planning = select(TrackedEntry.id).join(planned_title, TrackedEntry.media_id == planned_title.id).where(
        TrackedEntry.status == 'planning', or_(
            planned_title.id == Media.id,
            and_(Media.media_type == MediaType.episode, Media.season_number > 0,
                 or_(Media.release_date <= now.date().isoformat(), Media.tmdb_data['tracking_import_released'].as_boolean().is_(True)),
                 planned_title.media_type == MediaType.series,
                 or_(and_(planned_title.tmdb_id.isnot(None), planned_title.tmdb_id == Show.tmdb_id),
                     and_(planned_title.tvdb_id.isnot(None), planned_title.tvdb_id == Show.tvdb_id))),
        )).correlate(Media, Show).exists()
    rows = (await db.execute(
        select(Media, Show).outerjoin(Show, Show.id == Media.show_id)
        .where(
            Media.media_type.in_([MediaType.movie, MediaType.episode]),
            or_(Media.runtime.is_(None), Media.runtime <= 0),
            or_(planning, select(WatchEvent.id).where(
                WatchEvent.media_id == Media.id, WatchEvent.completed.is_(True),
            ).exists()),
            True if force else or_(attempted.is_(None), attempted <= cutoff,
                Media.tmdb_data['runtime_metadata_version'].astext.is_(None),
                Media.tmdb_data['runtime_metadata_version'].astext != str(VERSION)),
        ).order_by(attempted.asc().nullsfirst(), Media.id).limit(limit)
    )).all()
    result = {"examined": 0, "recovered": 0, "unresolved": 0, "failed": 0, "estimated_shows": 0}
    seasons = {}
    tvdb_shows = {}
    tvdb_movies = {}
    estimated_shows = set()
    for media, show in rows:
        if not force and (media.tmdb_data or {}).get('runtime_metadata_version') == VERSION and not due(media.tmdb_data, now):
            continue
        result["examined"] += 1
        data = dict(media.tmdb_data or {})
        runtime = positive_number(data.get("runtime"))
        failed = False
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
        except Exception:
            failed = True
        # Each provider can recover independently when another one fails.
        try:
            if not runtime and tvdb_api_key and media.media_type == MediaType.movie:
                native = getattr(media, 'tvdb_id', None)
                if not native and media.tmdb_id:
                    alias = aliased(CatalogueIdentity)
                    native = await db.scalar(select(alias.external_id).join(CatalogueIdentity,
                        CatalogueIdentity.entity_id == alias.entity_id).where(alias.namespace == 'tvdb.movie',
                        CatalogueIdentity.namespace == 'tmdb.movie', CatalogueIdentity.external_id == str(media.tmdb_id)))
                    native = int(native) if native and native.isdecimal() else None
                if native:
                    if native not in tvdb_movies:
                        tvdb_movies[native] = {}
                        tvdb_movies[native] = await tvdb.get_movie(native, tvdb_api_key)
                    response = tvdb_movies[native]
                    if response.get('id') == native:
                        runtime = positive_number(response.get('runtime'))
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
            failed = True
        try:
            if not runtime and tvdb_api_key and show and show.tvdb_id and show.tvdb_id not in estimated_shows:
                estimated_shows.add(show.tvdb_id)
                show_data = dict(getattr(show, "tmdb_data", None) or {})
                values = show_data.get("episode_run_time") or []
                if not isinstance(values, list):
                    values = [values]
                if not any(positive_number(v) for v in values):
                    summary = await tvdb.get_series(show.tvdb_id, tvdb_api_key, cache_ttl=None)
                    average = positive_number(summary.get("averageRuntime"))
                    if summary.get("id") == show.tvdb_id and average:
                        show.tmdb_data = {**show_data, "episode_run_time": [average],
                                          "episode_run_time_source": "tvdb_average"}
                        result["estimated_shows"] += 1
        except Exception:
            # Do not log provider exceptions: URLs may contain credentials.
            failed = True
        result["failed"] += int(failed)
        if runtime and runtime.is_integer():
            media.runtime = int(runtime)
            data["runtime"] = media.runtime
            result["recovered"] += 1
        else:
            result["unresolved"] += 1
        data["runtime_backfill_attempted_at"] = now.isoformat()
        data['runtime_metadata_version'] = VERSION
        media.tmdb_data = data
    await db.commit()
    return result
