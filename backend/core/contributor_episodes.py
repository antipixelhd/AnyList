"""Exact crew credits from the season responses already used for episode data."""

import asyncio

from fastapi import HTTPException

from core import settings_store, tmdb
from core.browse import anime_visible
from core.catalog_search import matches_result_filters, result_filters
from core.contributor_details import positive_id, resolve_contributor
from core.credit_roles import work_credit
from core.statistics_staff import ACTING, role_order
from models import GlobalSettings

SEASONS_PER_PAGE = 6
PARALLEL_SEASONS = 3


def season_credits(data, season, person_id, expected_id=None):
    if (data.get('season_number') != season or not isinstance(data.get('episodes'), list)
            or expected_id and positive_id(data.get('id')) != expected_id):
        raise ValueError('Incomplete season data')
    credits = []
    for episode in data['episodes']:
        number = positive_id(episode.get('episode_number'))
        crew = episode.get('crew')
        if not number or not isinstance(crew, list):
            raise ValueError('Incomplete episode data')
        roles = {
            job.strip() for member in crew
            if positive_id(member.get('id')) == person_id
            and isinstance(job := member.get('job'), str)
            and work_credit(job)
            and job.strip().casefold() not in ACTING
        }
        if roles:
            credits.append({'season': season, 'episode': number, 'title': episode.get('name') or '',
                            'roles': sorted(roles, key=lambda label: (role_order(label), label))})
    return credits


async def episode_credits(db, viewer, *, key, series_id, after_season):
    resolved = await resolve_contributor(db, 'person', key)
    if not resolved or not resolved[1]:
        raise HTTPException(404, 'Person credits not found')
    _, person_id = resolved
    api_key = await settings_store.get_user_tmdb_key(db, viewer.id if viewer else -1)
    if not api_key:
        raise HTTPException(503, 'Episode credits are temporarily unavailable')
    settings = await db.get(GlobalSettings, 1)
    show_anime = settings is None or settings.show_anime
    filters = await result_filters(db, viewer)
    # Release the read transaction before waiting on provider requests.
    await db.rollback()
    try:
        async with asyncio.timeout(12):
            series = await tmdb.get_show_light(series_id, api_key=api_key)
            if positive_id(series.get('id')) != series_id or series.get('adult') or not anime_visible(series, show_anime):
                raise HTTPException(404, 'Series not found')
            if not matches_result_filters(series, 'series', **filters):
                raise HTTPException(404, 'Series not found')
            if not isinstance(series.get('seasons'), list):
                raise ValueError('Incomplete series data')
            season_ids = {number: positive_id(item.get('id')) for item in series['seasons']
                          if type(number := item.get('season_number')) is int and number >= 0
                          and (after_season is None or number > after_season)}
            seasons = sorted(season_ids)
            window = seasons[:SEASONS_PER_PAGE]
            credits = {}
            for start in range(0, len(window), PARALLEL_SEASONS):
                batch = window[start:start + PARALLEL_SEASONS]
                async with asyncio.TaskGroup() as tasks:
                    reads = {number: tasks.create_task(tmdb.get_season(series_id, number, api_key=api_key))
                             for number in batch}
                for number, task in reads.items():
                    for row in season_credits(task.result(), number, person_id, season_ids[number]):
                        credits[(row['season'], row['episode'])] = row
            return {'episodes': [credits[identity] for identity in sorted(credits)],
                    'next_season': window[-1] if len(seasons) > len(window) else None}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(502, 'Unable to load episode credits') from error
