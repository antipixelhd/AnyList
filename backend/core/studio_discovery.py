"""Bounded, coalesced public work evidence for studio discovery.

No company profile/complete filmography fan-out and no catalogue writes on search.
The cache contains public provider evidence keyed by result filters, never lists.
"""
import asyncio
from datetime import date

from core import tmdb
from core.browse import anime_visible
from core.catalog_search import DEFAULT_MIN_VOTES, matches_result_filters, result_filters
from core.studio_groups import studio_root, studio_companies, studio_display_name
from models import GlobalSettings

MAX_CANDIDATES = 12
MAX_INFLIGHT = 24
_slots = asyncio.Semaphore(4)
_cache = tmdb._TTLCache(maxsize=512)
_inflight = {}


async def _load(native, filters, show_anime, today, api_key):
    async def discover(kind):
        async with _slots:
            return await (tmdb.discover_movies if kind == 'movie' else tmdb.discover_shows)(
                with_companies=studio_companies(native), vote_count_min=filters['min_votes'],
                minimum_runtime=filters['min_runtime'] if kind == 'movie' else 0,
                excluded_genre_ids=filters['excluded_genres'], released_before=today,
                sort_by='primary_release_date.desc' if kind == 'movie' else 'first_air_date.desc', api_key=api_key)
    async with asyncio.timeout(6):
        async with asyncio.TaskGroup() as group:
            movie_task = group.create_task(discover('movie'))
            series_task = group.create_task(discover('series'))
        movies, series = movie_task.result(), series_task.result()
    dates, count = [], 0
    for kind, response in [('movie', movies), ('series', series)]:
        visible = [row for row in response.get('results') or [] if row.get('id') and not row.get('adult')
            and matches_result_filters(row, kind, **filters) and anime_visible(row, show_anime)
            and '0000' < (row.get('release_date') or row.get('first_air_date') or '') <= today]
        if visible:
            dates.extend(row.get('release_date') or row.get('first_air_date') for row in visible)
            count += max(len(visible), response.get('total_results') or 0)
    return {'works': count, 'latest': max(dates, default='')}


async def studio_evidence(native, filters, show_anime, api_key):
    today = date.today().isoformat()
    native = studio_root(native)
    key = (native, filters['min_votes'], filters['min_runtime'], tuple(sorted(filters['excluded_genres'])), show_anime, today)
    cached = _cache.get(key)
    if cached is not None:
        return None if cached.get('unavailable') else cached
    pending = _inflight.get(key)
    if pending is None:
        if len(_inflight) >= MAX_INFLIGHT:
            return None
        async def load():
            try:
                value = await _load(native, filters, show_anime, today, api_key)
                _cache.set(key, value, 1800 if value['works'] else 300)
                return value
            except Exception:
                _cache.set(key, {'unavailable': True}, 15)
                return None
            finally:
                _inflight.pop(key, None)
        pending = _inflight[key] = asyncio.create_task(load())
    return await asyncio.shield(pending)


async def verified_studios(db, viewer, rows, api_key):
    if not rows:
        return [], False
    # Prefer known contributors, then provider-branded matches; retain provider
    # order among equal candidates rather than sorting unverified names A-Z.
    rows = sorted(rows, key=lambda row: (-row.get('_works', 0), not bool(row.get('image'))))
    groups = {}
    for row in rows:
        native = row.get('_native')
        root = studio_root(native) if native else row['key']
        if root not in groups:
            groups[root] = {**row}
        if native and native != root:
            groups[root].update(key=f'tmdb:{root}', url=f'/studio/tmdb:{root}')
        if native:
            groups[root].update(name=studio_display_name(native, groups[root]['name']), _native=root)
        if native == root:
            groups[root].update(key=row['key'], url=row['url'], image=row.get('image'))
    for row in groups.values():
        native = row.get('_native')
        if native and studio_companies(native) != native:
            row.update(key=f'tmdb:{native}', url=f'/studio/tmdb:{native}')
    if not api_key:
        return list(groups.values()), False
    filters = await result_filters(db, viewer)
    # A permissive personal title filter must not turn company-name lookup back
    # into a directory of one-off/unrecognized companies.
    filters = {**filters, 'min_votes': max(DEFAULT_MIN_VOTES, filters['min_votes'])}
    settings = await db.get(GlobalSettings, 1)
    show_anime = settings is None or settings.show_anime
    candidates = list(groups.values())[:MAX_CANDIDATES]
    evidence = await asyncio.gather(*(studio_evidence(row['_native'], filters, show_anime, api_key)
        if row.get('_native') else asyncio.sleep(0, result=None) for row in candidates))
    output, partial = [], False
    for row, value in zip(candidates, evidence):
        if value is None:
            partial = True
            # Retain established local results during provider failures.
            if row.get('_works', 0):
                output.append(row)
        elif value['works']:
            output.append({**row, '_works': value['works'], '_latest': value['latest']})
    return output, partial
