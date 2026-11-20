"""Cache TheTVDB's dedicated seasonal backgrounds during the metadata sweep."""

import asyncio
from collections import defaultdict
from datetime import timedelta

import httpx
from sqlalchemy import cast, func, update
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.orm.attributes import set_committed_value

from core import tvdb
from core.identity import external_ids_from_tmdb
from models import Media


CACHE_KEY = 'tracking_tvdb_season_artwork'
REFRESH_AFTER = timedelta(days=30)


def stored_backdrop(data, order, number):
    if order == 'tmdb:aired':
        return ((data.get(CACHE_KEY) or {}).get(str(number)) or {}).get('backdrop')
    return None


async def refresh_tracked_season_artwork(rows, api_key, now, limit=48):
    """Give unseen seasons priority and cap season reads across the daily sweep."""
    if not api_key:
        return []
    due = []
    cutoff = (now - REFRESH_AFTER).isoformat()
    for media, show in rows:
        data = media.tmdb_data or {}
        tvdb_id = (media.tvdb_id or (show.tvdb_id if show else None) or external_ids_from_tmdb(data)[0]
                   or (media.tvdb_air_schedule or {}).get('tvdb_id'))
        if not tvdb_id:
            continue
        cached = data.get(CACHE_KEY) or {}
        numbers = sorted({season['season_number'] for season in data.get('seasons') or []
                          if isinstance(season, dict) and (season.get('season_number') or 0) > 0}, reverse=True)
        for rank, number in enumerate(numbers):
            checked = (cached.get(str(number)) or {}).get('checked_at') or ''
            if checked < cutoff:
                due.append((checked, rank, media.id, number, tvdb_id, media))
    if not due:
        return []
    try:
        async with asyncio.timeout(8):
            background_types = await tvdb.season_background_types(api_key)
    except (httpx.HTTPError, TimeoutError, ValueError):
        return []
    if not background_types:
        return []

    by_title = defaultdict(list)
    for _, _, _, number, tvdb_id, media in sorted(due, key=lambda candidate: candidate[:3])[:limit]:
        by_title[media.id].append((media, tvdb_id, number))
    semaphore = asyncio.Semaphore(4)
    retry_stamp = (now - REFRESH_AFTER + timedelta(days=1)).isoformat()

    async def refresh(candidates):
        media, tvdb_id, _ = candidates[0]
        async with semaphore:
            data = media.tmdb_data or {}
            saved = data.get(CACHE_KEY) or {}
            changes = {}
            try:
                async with asyncio.timeout(8):
                    series = await tvdb.get_series_airtime(tvdb_id, api_key)
                seasons = series.get('seasons') if isinstance(series, dict) else None
                if not isinstance(seasons, list) or any(not isinstance(season, dict)
                        or not isinstance(season.get('number'), int) or not isinstance(season.get('id'), int)
                        or not isinstance(season.get('type'), dict) for season in seasons):
                    raise ValueError('Invalid TVDB seasons')
                ids = {season['number']: season['id'] for season in seasons
                       if season['type'].get('type') == 'official'}
            except (httpx.HTTPError, TimeoutError, ValueError, AttributeError, TypeError):
                ids = None
            for _, _, number in candidates:
                key = str(number)
                previous = saved.get(key) or {}
                if ids is None:
                    changes[key] = {**previous, 'checked_at': retry_stamp}
                    continue
                backdrop = None
                if season_id := ids.get(number):
                    try:
                        async with asyncio.timeout(8):
                            season = await tvdb.get_season(season_id, api_key)
                        backdrop = tvdb.season_background(season, background_types)
                    except (httpx.HTTPError, TimeoutError, ValueError, AttributeError, TypeError):
                        changes[key] = {**previous, 'checked_at': retry_stamp}
                        continue
                changes[key] = {'backdrop': backdrop, 'checked_at': now.isoformat()}
            return media, changes

    return await asyncio.gather(*(refresh(candidates) for candidates in by_title.values()))


async def publish_season_artwork(db, changes):
    for media, seasons in sorted(changes, key=lambda item: item[0].id):
        current = func.coalesce(Media.tmdb_data[CACHE_KEY], cast({}, JSONB))
        data = func.jsonb_set(Media.tmdb_data, array([CACHE_KEY]), current.op('||')(cast(seasons, JSONB)))
        result = await db.execute(update(Media).where(Media.id == media.id).values(tmdb_data=data)
                                  .returning(Media.tmdb_data).execution_options(synchronize_session=False))
        if (metadata := result.scalar_one_or_none()) is not None:
            set_committed_value(media, 'tmdb_data', metadata)
