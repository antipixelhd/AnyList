"""Project Browse season context without changing title ranking or event identity."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx

from core import tmdb
from core.release_events import window
from core.season_releases import current_airing_episode


def shows_seasons(media_type, filters):
    return (media_type == 'series' and filters.get('status') not in ('cancelled', 'finished')
            and filters.get('sort') not in ('score', 'oldest'))


def regular_seasons(data):
    seasons = data.get('seasons')
    if isinstance(seasons, list):
        yield from (season for season in seasons if isinstance(season, dict)
                    and type(season.get('season_number')) is int and season['season_number'] > 0)


def episode(data, key):
    value = data.get(key)
    if isinstance(value, dict) and type(value.get('season_number')) is int and value['season_number'] > 0:
        return value
    return {}


def known_date(value):
    if type(value) is int:
        value = str(value)
    return value if window(value) else None


def season_context(number, *evidence, release_date=None):
    name = None
    premiere = known_date(release_date)
    for data in evidence:
        season = next((entry for entry in regular_seasons(data) if entry['season_number'] == number), {})
        label = season.get('name')
        if not name and isinstance(label, str) and label.strip().casefold() not in ('', f'season {number}'):
            name = label.strip()
        if not premiere:
            premiere = known_date(season.get('air_date')) or known_date(season.get('year'))
            if not premiere and number == 1:
                premiere = known_date(data.get('first_air_date'))
            for key in ('last_episode_to_air', 'next_episode_to_air'):
                first = episode(data, key)
                if not premiere and first.get('season_number') == number and first.get('episode_number') == 1:
                    premiere = known_date(first.get('air_date'))
    return {'number': number, **({'name': name} if name else {}), **({'date': premiere} if premiere else {})}


def ranked_season(*evidence, today):
    for data in evidence:
        sources = (data, *(other for other in evidence if other is not data))
        _, upcoming_episode = current_airing_episode(data, (), today=today)
        if upcoming_episode:
            return season_context(upcoming_episode['season_number'], *sources)
        started = set()
        for season in regular_seasons(data):
            number = season['season_number']
            value = known_date(season.get('air_date')) or known_date(season.get('year'))
            if not value and number == 1:
                value = known_date(data.get('first_air_date'))
            if value and window(value)[1] <= today:
                started.add(number)
        last = episode(data, 'last_episode_to_air')
        if (value := known_date(last.get('air_date'))) and window(value)[1] <= today:
            started.add(last['season_number'])
        if not started and data.get('number_of_seasons') == 1:
            if (value := known_date(data.get('first_air_date'))) and window(value)[1] <= today:
                started.add(1)
        if started:
            return season_context(max(started), *sources)
    return None


def apply_event_season(row, show_season, *evidence):
    number = row['release_season']
    if number:
        row['series_title'] = row['title'].removesuffix(f' · Season {number}')
    if show_season:
        row['series_season'] = season_context(number or 1, *evidence, release_date=row['release_date'])


async def attach_ranked_seasons(db, rows, local_by_id, remote_by_tmdb, key):
    today = date.today()
    fresh_since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    pending = []
    for row in rows:
        native = row.get('tmdb_id')
        media = local_by_id.get(row.get('id'))
        stored = {'first_air_date': media.release_date, **(media.tmdb_data or {})} if media is not None else {}
        incoming = remote_by_tmdb.get(native, {})
        context = ranked_season(incoming, stored, today=today)
        if context:
            row['series_season'] = context
        if not key or not native or isinstance(incoming.get('seasons'), list):
            continue
        if context and context.get('date') and media is not None and (
                (stored.get('status') or media.status) in ('Ended', 'Canceled')
                or media.updated_at and media.updated_at >= fresh_since):
            continue
        pending.append((row, stored))

    if not pending:
        return
    # Optional context never holds a database connection or blocks discovery on
    # a failed provider. Use the same light response cache as title details.
    await db.commit()
    gate = asyncio.Semaphore(6)

    async def enrich(row, stored):
        async with gate:
            try:
                detail = await tmdb.get_show_light(row['tmdb_id'], api_key=key)
            except (tmdb.TMDBUnavailable, httpx.HTTPError, ValueError):
                return
            if not isinstance(detail, dict):
                return
            context = ranked_season(detail, stored, today=today)
            if context:
                row['series_season'] = context

    try:
        async with asyncio.timeout(1.2):
            await asyncio.gather(*(enrich(row, stored) for row, stored in pending[:24]))
    except TimeoutError:
        pass
