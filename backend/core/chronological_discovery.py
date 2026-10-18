"""Bounded, reusable release-event selections, independent of title search.

Each 180-day historical window is finalized before paging. Provider popularity
selects candidates; event dates order the selection. Paging never re-sorts an
already displayed window. No credits, images or filmographies are requested.
"""
import asyncio
import hashlib
from datetime import date, timedelta

from core import tmdb
from core.release_events import events, window

PROVIDER_PAGES = 6
WINDOW_DAYS = 180
CURSOR_STRIDE = 10_000
_cache = tmdb._TTLCache(maxsize=64)
_pending = {}
MAX_SELECTIONS = 8


def audience_relevant(item, country, *, global_floor=60):
    """Positive local distribution/broadcast evidence or strong global interest.

    Original language and production origin are deliberately not exclusions.
    Missing territorial evidence is uncertainty, not a worldwide release.
    """
    if not country:
        return True
    offers = ((item.get('watch/providers') or {}).get('results') or item.get('watch_providers') or {})
    markets = {code for code, values in offers.items() if isinstance(values, dict)
        and any(values.get(kind) for kind in ('flatrate', 'free', 'ads', 'buy', 'rent'))}
    local = offers.get(country) or {}
    if any(local.get(kind) for kind in ('flatrate', 'free', 'ads', 'buy', 'rent')):
        return True
    if any(network.get('origin_country') == country for network in item.get('networks', []) if isinstance(network, dict)):
        return True
    releases = (item.get('release_dates') or {}).get('results', [])
    markets.update(entry.get('iso_3166_1') for entry in releases
        if any(release.get('type') in (2, 3, 4, 5, 6) for release in entry.get('release_dates', [])))
    markets.update(code for code, dates in (item.get('theatrical_release_dates_by_country') or {}).items() if dates)
    markets.discard(None)
    if country in markets:
        return True
    # A high score alone can describe a domestic hit (e.g. AIBOU with offers
    # only in Japan). Broad distribution supports the global exception. When
    # distribution is unknown, keep anticipated titles eligible on attention.
    return float(item.get('popularity') or 0) >= global_floor and (not markets or len(markets) >= 3)


async def _selection(media_type, key, filters, lower, upper, show_anime, term=''):
    from core.browse import anime_visible, matches, remote_params
    kind = 'movie' if media_type == 'movie' else 'tv'
    upcoming = filters.get('status') == 'upcoming' or filters['sort'] == 'upcoming'
    # Personal curation must not change which provider pages define the pool:
    # otherwise broadening genres can displace a previously visible title past
    # the budget boundary and make Show all *remove* results.
    params = remote_params(media_type, page=1, **{**filters, 'min_votes': 0,
        'min_runtime': 0, 'excluded_genres': ()})
    params['sort_by'] = 'popularity.desc'
    for name in list(params):
        if name.startswith(('primary_release_date.', 'first_air_date.', 'release_date.', 'air_date.')):
            del params[name]
    params['include_null_first_air_dates'] = 'false'
    if media_type == 'movie':
        params.pop('include_null_first_air_dates', None)
    fields = ['release_date'] if filters.get('cinema') else (
        ['primary_release_date', 'release_date'] if media_type == 'movie' else
        ['first_air_date', 'air_date'] if filters.get('include_returning', True) else ['first_air_date'])
    # Divide a fixed budget between new premieres and returns/regional dates.
    # Ongoing episodes must not consume every slot in an Upcoming TV pool.
    async def feed(field):
        per_feed = PROVIDER_PAGES if len(fields) == 1 else (2 if field in ('primary_release_date', 'first_air_date') else 4)
        values = {**params}
        if lower: values[f'{field}.gte'] = lower
        if upper: values[f'{field}.lte'] = upper
        if field == 'release_date' and media_type == 'movie':
            values['region'] = filters.get('cinema_country') or filters.get('region') or 'US'
        async def read(number):
            return await tmdb._get(f'{tmdb.TMDB_BASE}/discover/{kind}', headers=tmdb.get_headers(key), params={**values, 'page': number})
        first = await read(1)
        count = min(per_feed, first.get('total_pages', 1))
        batches = [first, *await asyncio.gather(*(read(number) for number in range(2, count + 1)))]
        return batches, first.get('total_pages', 1) > per_feed
    if term:
        from core.title_search import candidates as search_candidates
        first, search_rows = await search_candidates(term, media_type, key)
        count = min(first.get('total_pages', 1), 3)
        following = await asyncio.gather(*(search_candidates(term, media_type, key, number)
            for number in range(2, count + 1)))
        batches = [{'results': search_rows}, *({'results': rows} for _, rows in following)]
        partial = first.get('total_pages', 1) > 3
    else:
        feeds = await asyncio.gather(*(feed(field) for field in fields))
        batches = [batch for group, _ in feeds for batch in group]
        partial = any(partial for _, partial in feeds)
    predicate = {name: value for name, value in filters.items() if name not in ('sort', 'audience_curation', 'include_returning')}
    cheap = {**predicate, 'start': None, 'end': None, 'status': '', 'cinema': False, 'provider': None}
    candidates = {item['id']: item for batch in batches for item in batch.get('results', [])
                  if matches(item, media_type=media_type, **cheap) and anime_visible(item, show_anime)}
    gate = asyncio.Semaphore(6)
    async def detail(item):
        async with gate:
            try:
                raw = await tmdb._get(f'{tmdb.TMDB_BASE}/{kind}/{item["id"]}', headers=tmdb.get_headers(key),
                    params={'append_to_response': 'release_dates,watch/providers' if media_type == 'movie' else 'watch/providers'})
                return {**item, **raw}
            except Exception:
                # A failed evidence read cannot establish local eligibility.
                return item
    detailed = await asyncio.gather(*(detail(item) for item in candidates.values()))
    rows = []
    for item in detailed:
        if not anime_visible(item, show_anime):
            continue
        if filters.get('audience_curation', True) and not audience_relevant(item, filters.get('region')):
            continue
        for event in events(item, media_type, upcoming_only=upcoming,
                include_returning=filters.get('include_returning', True),
                country=filters.get('cinema_country') or filters.get('region'), cinema=filters.get('cinema', False),
                include_rereleases=filters.get('include_rereleases', False)):
            bounds = window(event['release_date'])
            if not bounds or (lower and bounds[1].isoformat() < lower) or (upper and bounds[0].isoformat() > upper):
                continue
            if not upcoming and bounds[1] > date.today():
                continue
            # Date precision eligibility was resolved by events, not string
            # comparisons against a fabricated first day of a month/year.
            check = {**predicate, 'start': None, 'end': None}
            if matches(event, media_type=media_type, **check):
                rows.append(event)
    rows.sort(key=lambda item: (window(item['release_date'])[0], item['release_event']), reverse=not upcoming)
    return {'rows': rows, 'partial': partial}


async def page(media_type, key, filters, *, offset=0, show_anime=True, size=24, term=''):
    upcoming = filters.get('status') == 'upcoming' or filters['sort'] == 'upcoming'
    window_index, position = divmod(max(offset, 0), CURSOR_STRIDE)
    upper = filters.get('end')
    lower = filters.get('start')
    more_windows = False
    if not upcoming and not term:
        ceiling = date.fromisoformat(upper) if upper else date.today()
        ceiling -= timedelta(days=window_index * WINDOW_DAYS)
        floor = ceiling - timedelta(days=WINDOW_DAYS - 1)
        floor = max(floor, date.fromisoformat(lower) if lower else date(1880, 1, 1))
        upper, lower = ceiling.isoformat(), floor.isoformat()
        more_windows = floor > (date.fromisoformat(filters['start']) if filters.get('start') else date(1880, 1, 1))
    elif upcoming:
        lower = max(lower or '', (date.today() + timedelta(days=1)).isoformat())
    cache_key = (media_type, repr(sorted(filters.items())), lower, upper, show_anime, term)
    selected = _cache.get(cache_key)
    if selected is None:
        loop = asyncio.get_running_loop()
        pending_key = (loop, cache_key, hashlib.sha256(str(key).encode()).digest())
        entry = _pending.get(pending_key)
        if entry is None:
            if sum(identity[0] is loop for identity in _pending) >= MAX_SELECTIONS:
                raise tmdb.TMDBUnavailable('Discovery selection capacity reached')
            async def load():
                result = await _selection(media_type, key, filters, lower, upper, show_anime, term)
                _cache.set(cache_key, result, 1800)
                return result
            entry = [asyncio.create_task(load()), 0]
            _pending[pending_key] = entry
        entry[1] += 1
        try:
            selected = await asyncio.shield(entry[0])
        finally:
            entry[1] -= 1
            if not entry[1]:
                _pending.pop(pending_key, None)
                if not entry[0].done():
                    entry[0].cancel()
                entry[0].add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
    rows = selected['rows'][position:position + size]
    next_position = position + len(rows)
    more_rows = next_position < len(selected['rows'])
    has_more = more_rows or more_windows
    next_offset = window_index * CURSOR_STRIDE + next_position if more_rows else (window_index + 1) * CURSOR_STRIDE
    return {'has_more': has_more, 'scan_next': next_offset, 'partial': selected['partial'],
            'window_has_more': more_rows, 'more_windows': more_windows,
            'window_lower': lower, 'window_upper': upper,
            'notice': None}, rows
