"""Bounded, reusable release-event selections, independent of title search.

Each 180-day historical window is finalized before paging. Provider popularity
selects candidates; event dates order the selection. Paging never re-sorts an
already displayed window. No credits, images or filmographies are requested.
Shelves may take an early six-card preview; it never supplies a timeline cursor.
The same bounded public task completes the window independently of the viewer.
"""
import asyncio
import hashlib
from datetime import date, timedelta

from core import tmdb
from core.release_events import events, window
from core.release_snapshots import ReleaseSnapshots, FRESH_SECONDS
from core.config import settings

EVIDENCE_SECONDS = 2 * 3600
PROVIDER_PAGES = 6
WINDOW_DAYS = 180
CURSOR_STRIDE = 10_000
_cache = tmdb._TTLCache(maxsize=64)
_pending = {}
MAX_SELECTIONS = 8
_snapshots = ReleaseSnapshots(lambda: settings.data_dir / 'release-selections')


def selection_identity(media_type, filters, lower, upper, show_anime, term):
    # Shelf and View all spell defaults differently; they must share the same
    # public continuation rather than acquire the complete window twice.
    values = dict(genres=[], start=lower, end=upper, status='', provider=None,
        min_score=0, min_runtime=0, excluded_genres=[], min_votes=0, min_popularity=0,
        require_release=False, require_poster=False, cinema=False, audience_curation=True,
        include_returning=True, include_rereleases=False, cinema_country=None)
    values.update(filters, start=lower, end=upper)
    values['include_rereleases'] = bool(values['include_rereleases'])
    for name in ('genres', 'excluded_genres'):
        values[name] = tuple(sorted(values[name]))
    for name in ('min_score', 'min_runtime', 'min_votes', 'min_popularity'):
        values[name] = float(values[name])
    return (media_type, repr(sorted(values.items())), lower, upper, show_anime, term)


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


async def _selection(media_type, key, filters, lower, upper, show_anime, term='', *, publish=None):
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
        first = await tmdb._get(f'{tmdb.TMDB_BASE}/discover/{kind}', headers=tmdb.get_headers(key),
            params={**values, 'page': 1}, cache_ttl=EVIDENCE_SECONDS)
        count = min(per_feed, first.get('total_pages', 1))
        return first, [{**values, 'page': number} for number in range(2, count + 1)], first.get('total_pages', 1) > per_feed
    tails = None
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
        batches = [first for first, _, _ in feeds]
        partial = any(limited for _, _, limited in feeds)
        async def read_remaining():
            tasks = [asyncio.create_task(tmdb._get(f'{tmdb.TMDB_BASE}/discover/{kind}',
                headers=tmdb.get_headers(key), params=values, cache_ttl=EVIDENCE_SECONDS))
                for _, following, _ in feeds for values in following]
            try:
                return await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    predicate = {name: value for name, value in filters.items() if name not in ('sort', 'audience_curation', 'include_returning')}
    cheap = {**predicate, 'start': None, 'end': None, 'status': '', 'cinema': False, 'provider': None}
    def pool(groups):
        return {item['id']: item for batch in groups for item in batch.get('results', [])
                if matches(item, media_type=media_type, **cheap) and anime_visible(item, show_anime)}
    candidates = pool(batches)
    gate = asyncio.Semaphore(6)
    failed = False
    async def detail(item):
        nonlocal failed
        async with gate:
            try:
                raw = await tmdb._get(f'{tmdb.TMDB_BASE}/{kind}/{item["id"]}', headers=tmdb.get_headers(key),
                    params={'append_to_response': 'release_dates,watch/providers' if media_type == 'movie' else 'watch/providers'},
                    cache_ttl=EVIDENCE_SECONDS)
                return raw
            except Exception:
                failed = True
                # A failed evidence read cannot establish local eligibility.
                return {}
    rows = []
    def project(item):
        if not anime_visible(item, show_anime):
            return
        if filters.get('audience_curation', True) and not audience_relevant(item, filters.get('region')):
            return
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
    hydrated = {}
    published = False
    def ordered(values):
        return sorted(values, key=lambda item: item.get('first_air_date') or item.get('release_date') or '', reverse=not upcoming)
    def collect(values):
        rows.clear()
        for item in values:
            if item['id'] in hydrated:
                project({**item, **hydrated[item['id']]})
    def preview():
        nonlocal published
        if publish and not published and len(rows) >= 6:
            publish({'rows': sorted(rows, key=lambda item: (window(item['release_date'])[0], item['release_event']), reverse=not upcoming)[:6], 'partial': True})
            published = True
    async def hydrate(batch):
        for item, raw in zip(batch, await asyncio.gather(*(detail(item) for item in batch))):
            hydrated[item['id']] = raw
            project({**item, **raw})
    try:
        if not term:
            tails = asyncio.create_task(read_remaining())
        if tails is not None:
            # The first six checks overlap the remaining discover pages. Full
            # chronology still waits for the identical complete candidate pool.
            await hydrate(ordered(candidates.values())[:6])
            preview()
            following = await tails
            # Keep the original feed/page order: duplicate summaries previously
            # used their final occurrence, independent of network arrival order.
            groups=[];position=0
            for first, pending, _ in feeds:
                groups.extend([first, *following[position:position+len(pending)]])
                position += len(pending)
            candidates = pool(groups)
            collect(candidates.values())
        remaining = ordered(item for item in candidates.values() if item['id'] not in hydrated)
        for start in range(0, len(remaining), 6):
            await hydrate(remaining[start:start+6])
            preview()
    finally:
        if tails is not None:
            if not tails.done(): tails.cancel()
            await asyncio.gather(tails, return_exceptions=True)
    rows.sort(key=lambda item: (window(item['release_date'])[0], item['release_event']), reverse=not upcoming)
    return {'rows': rows, 'partial': partial or failed, 'incomplete': failed}


async def close():
    tasks = [entry[0] for identity, entry in list(_pending.items()) if identity[0] is asyncio.get_running_loop()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def page(media_type, key, filters, *, offset=0, show_anime=True, size=24, term='', preview=False):
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
    cache_key = selection_identity(media_type, filters, lower, upper, show_anime, term)
    selected = _cache.get(cache_key)
    stale = None
    if selected is None:
        saved = await _snapshots.read(cache_key)
        if saved:
            result, age = saved
            if age < FRESH_SECONDS:
                selected = result
                _cache.set(cache_key, result, FRESH_SECONDS - age)
            elif preview:
                stale = result
    if selected is None:
        loop = asyncio.get_running_loop()
        pending_key = (loop, cache_key, hashlib.sha256(str(key).encode()).digest())
        entry = _pending.get(pending_key)
        if entry is None and sum(identity[0] is loop for identity in _pending) >= MAX_SELECTIONS:
            if stale is None:
                raise tmdb.TMDBUnavailable('Discovery selection capacity reached')
            selected = stale  # Serving public evidence needs no refresh admission.
        if selected is None:
            if entry is None:
                first = loop.create_future()
                def publish(result):
                    if not first.done():
                        first.set_result(result)
                async def load():
                    async with asyncio.timeout(90):
                        result = await _selection(media_type, key, filters, lower, upper, show_anime, term,
                            publish=publish)
                        _cache.set(cache_key, result, 15 if result.get('incomplete') else FRESH_SECONDS)
                        if entry[4] and not result.get('incomplete'):
                            await _snapshots.write(cache_key, result)
                        return result
                entry = [asyncio.create_task(load()), 0, first, False, preview]
                _pending[pending_key] = entry
                def finished(task):
                    if _pending.get(pending_key) is entry:
                        _pending.pop(pending_key, None)
                    if not first.done():
                        if task.cancelled(): first.cancel()
                        elif task.exception(): first.set_exception(task.exception())
                        else: first.set_result(task.result())
                    if not task.cancelled(): task.exception()
                    if first.done() and not first.cancelled(): first.exception()
                entry[0].add_done_callback(finished)
            entry[4] = entry[4] or preview
            entry[1] += 1
            try:
                selected = stale if stale is not None else await asyncio.shield(entry[2] if preview else entry[0])
                if preview and not entry[0].done():
                    entry[3] = True  # Bounded public continuation; no retained DB/viewer.
            finally:
                entry[1] -= 1
                if not entry[1] and not entry[3]:
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
            'event_keys': [row['release_event'] for row in selected['rows']],
            'window_has_more': more_rows, 'more_windows': more_windows,
            'window_lower': lower, 'window_upper': upper,
            'notice': None}, rows
