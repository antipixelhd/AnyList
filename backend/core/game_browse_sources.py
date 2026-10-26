"""Small ranked game projections; source metrics never become work identity."""
import asyncio
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from core.catalogue_providers import CatalogueProviders, ProviderHTTP, ProviderError
from core.config import settings
from core.game_source_cache import GameSourceCache
from core.game_search import broader_query, matches_partial
from core.game_release_dates import FIELDS as RELEASE_FIELDS, in_range
from core.game_types import INDEPENDENT, platform_group, search_candidate, canonical_game, candidate_fields, game_platforms
from db import AsyncSessionLocal

_slots = asyncio.Semaphore(2)
BASE_FIELDS = 'id,slug,name,cover.url,first_release_date,game_type.type,genres.name,platforms.name,platforms.platform_family.name,rating_count,ports.game_type.type,ports.platforms.name,ports.platforms.platform_family.name,'+RELEASE_FIELDS
CARD_FIELDS = candidate_fields(BASE_FIELDS)
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,199}\Z')


def positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def card(raw, *, expansions=False):
    candidate = search_candidate(raw)
    if not candidate or candidate['game_class'] not in INDEPENDENT and not (expansions and candidate['game_class'] == 'expansion'):
        return None
    work = canonical_game(raw)
    platforms, seen = [], set()
    available = [p for p in game_platforms(work) if positive(p.get('id'))]
    for platform in available:
        group = platform_group(platform)
        identifier = 'pc' if group == 'PC' else f'igdb.platform:{platform["id"]}'
        if identifier not in seen:
            platforms.append({'id':identifier, 'name':'PC' if group == 'PC' else platform.get('name'), 'group':group})
            seen.add(identifier)
    return dict(igdb_id=int(candidate['external_id']), type='game', title=candidate['name'],
        igdb_slug=work.get('slug'),
        poster=candidate['image_url'], release_date=candidate['release_date'],
        release_date_precision=candidate['release_date_precision'], release_quarter=candidate['release_quarter'],
        year=(candidate['release_date'] or '')[:4] or None, game_class=candidate['game_class'],
        genres=[{'id': f'igdb.genre:{g["id"]}', 'name': g['name']} for g in work.get('genres') or []
                if positive(g.get('id')) and isinstance(g.get('name'), str)],
        platforms=platforms, platform_ids=[f'igdb.platform:{p["id"]}' for p in available])


def metacritic_id(url):
    parsed = urlparse(url or '')
    parts = parsed.path.strip('/').split('/')
    if parsed.scheme != 'https' or parsed.hostname not in {'metacritic.com', 'www.metacritic.com'}:
        return None
    # RAWG still supplies platform-specific legacy URLs. The final game slug
    # is the current P12054 identifier; review/person/search URLs are rejected.
    if len(parts) not in (2, 3) or parts[0] != 'game' or not SLUG.fullmatch(parts[-1]):
        return None
    return parts[-1]


def concurrent_chart(payload, now=None):
    now = now or datetime.now(timezone.utc).timestamp()
    body = payload.get('response') if isinstance(payload, dict) else None
    observed = body.get('last_update') if isinstance(body, dict) else None
    rows = body.get('ranks') if isinstance(body, dict) else None
    if not positive(observed) or not now-86400 <= observed <= now+300 or not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ProviderError('bad_response')
    ids, ranks, result = set(), set(), []
    for row in rows:
        if not isinstance(row, dict) or not positive(row.get('appid')) or not positive(row.get('rank')) or row['rank'] > 100:
            raise ProviderError('bad_response')
        count = row.get('concurrent_in_game')
        if isinstance(count, bool) or not isinstance(count, int) or count < 0 or row['appid'] in ids or row['rank'] in ranks:
            raise ProviderError('bad_response')
        ids.add(row['appid']); ranks.add(row['rank'])
        result.append({'appid': row['appid'], 'rank': row['rank']})
    result.sort(key=lambda row: row['rank'])
    return {'rows': result, 'source_at': observed, 'source': 'Steam concurrent players', 'universe': 'Steam top 100'}


def bridge_rows(bindings):
    """A conflicting external identity stays unresolved, including qualifiers."""
    grouped = {}
    if not isinstance(bindings, list) or len(bindings) >= 500:
        raise ProviderError('bad_response')
    for row in bindings:
        key = (row.get('key') or {}).get('value', '')
        slug = (row.get('slug') or {}).get('value', '')
        item = (row.get('item') or {}).get('value', '').rsplit('/', 1)[-1]
        identifier = (row.get('id') or {}).get('value', '')
        if not SLUG.fullmatch(key) or not SLUG.fullmatch(slug) or not re.fullmatch(r'Q[1-9]\d*', item):
            continue
        if identifier and (not identifier.isdigit() or int(identifier) <= 0):
            continue
        grouped.setdefault(key, set()).add((item, slug, int(identifier) if identifier else None))
    result = {}
    for key, values in grouped.items():
        identities = {(item, slug) for item, slug, _ in values}
        ids = {identifier for _, _, identifier in values if identifier is not None}
        if len(identities) != 1 or len(ids) > 1:
            result[key] = None  # Conflict is distinct from missing evidence.
            continue
        item, slug = next(iter(identities))
        result[key] = {'wikidata_id':item, 'igdb_slug':slug, 'igdb_id':next(iter(ids), None)}
    return result


class GameBrowseSources:
    def __init__(self, session_factory=AsyncSessionLocal):
        self.cache = GameSourceCache(session_factory)
        self.api = CatalogueProviders(ProviderHTTP(session_factory=session_factory))

    async def igdb(self, endpoint, query):
        self.api.validate('igdb', 'game')
        async with _slots:
            return await self.api.igdb(endpoint, query)

    async def request(self, provider, url, **kwargs):
        if provider == 'wikidata':
            kwargs['headers'] = {'User-Agent':'AnyList catalogue/1 (https://github.com/antipixelhd/AnyList)'}
        async with _slots:
            return await self.api.http.request(provider, 'GET', url, **kwargs)

    async def defer(self,provider,seconds):
        await self.api.http.defer(provider,seconds)

    async def reference(self, endpoint, fields):
        async def load():
            rows = await self.igdb(endpoint, f'fields {fields}; limit 500;')
            if not isinstance(rows, list) or len(rows) >= 500 or any(not positive(row.get('id')) or not isinstance(row.get('name'), str) for row in rows):
                raise ProviderError('bad_response')
            return {'rows': rows}
        return await self.cache.get('igdb', 'game_browse_refs', endpoint, load,
            ttl=timedelta(days=1), max_stale=timedelta(days=30))

    async def facets(self):
        genres, platforms = await asyncio.gather(self.reference('genres', 'id,name'),
            self.reference('platforms', 'id,name,platform_family.name'))
        groups = {name: [] for name in ('PC', 'PlayStation', 'Xbox', 'Nintendo', 'Other devices')}
        for row in platforms['data']['rows']:
            group = platform_group(row)
            label = group if group in groups else 'Other devices'
            # PC's three source platforms are never separate UI devices.
            if label != 'PC':
                groups[label].append({'id': f'igdb.platform:{row["id"]}', 'name': row['name']})
        return {'genres': sorted([{'id': f'igdb.genre:{row["id"]}', 'name': row['name']}
                for row in genres['data']['rows']], key=lambda row: row['name'].casefold()),
            'platform_groups': [{'id': {'PC':'pc','PlayStation':'playstation','Xbox':'xbox','Nintendo':'nintendo'}.get(name, 'other'),
                'name': name, 'devices': sorted(devices, key=lambda row: row['name'].casefold())} for name, devices in groups.items()],
            'stale': genres['stale'] or platforms['stale']}

    async def primitive(self, name, source_name, offset=0):
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
            raise ProviderError('invalid_request')
        sources, types = await asyncio.gather(self.reference('external_game_sources', 'id,name'),
            self.reference('popularity_types', 'id,name,external_popularity_source'))
        source_ids = [row['id'] for row in sources['data']['rows'] if row.get('name') == source_name]
        matches = [row['id'] for row in types['data']['rows']
                   if row.get('name') == name and row.get('external_popularity_source') in source_ids]
        if len(matches) != 1:
            raise ProviderError('unsupported')
        async def load():
            rows = await self.igdb('popularity_primitives',
                f'fields game_id,value,calculated_at; where popularity_type={matches[0]}; sort value desc; limit 100; offset {offset};')
            if not isinstance(rows, list) or any(not positive(row.get('game_id')) or not positive(row.get('calculated_at'))
                    or isinstance(row.get('value'), bool) or not isinstance(row.get('value'), (int, float))
                    or not math.isfinite(row['value']) or row['value'] < 0 for row in rows):
                raise ProviderError('bad_response')
            for row in rows:
                self.require_source_time(row['calculated_at'], 3*86400)
            return {'rows': rows, 'has_more': len(rows) == 100, 'offset': offset,
                    'source': f'{source_name} {name}', 'source_at': min((row['calculated_at'] for row in rows), default=None)}
        result = await self.cache.get('igdb', 'game_browse_ranks', f'{matches[0]}:{offset}', load)
        self.require_source_time(result['data'].get('source_at'), 3*86400, empty=not result['data']['rows'])
        return result

    @staticmethod
    def require_source_time(observed, maximum_age, *, empty=False):
        now = datetime.now(timezone.utc).timestamp()
        if not empty and (not positive(observed) or not now-maximum_age <= observed <= now+300):
            raise ProviderError('unavailable')

    async def games(self, identifiers=(), slugs=()):
        clauses = []
        if identifiers:
            if any(not positive(identifier) for identifier in identifiers):
                raise ProviderError('invalid_request')
            clauses.append('id=('+','.join(map(str, sorted(set(identifiers))))+')')
        if slugs:
            if any(not SLUG.fullmatch(slug) for slug in slugs):
                raise ProviderError('invalid_request')
            clauses.append('slug=('+','.join(json.dumps(slug) for slug in sorted(set(slugs)))+')')
        if not clauses:
            return []
        if len(identifiers)+len(slugs) > 100:
            raise ProviderError('invalid_request')
        return await self.igdb('games', f'fields {CARD_FIELDS}; where ({" | ".join(clauses)}); limit 200;')

    async def catalogue_feed(self, sort, *, offset=0, genres=(), platforms=(), start=None, end=None, q='', search_mode=0):
        if sort not in {'newest','popular'} or isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
            raise ProviderError('invalid_request')
        if len(genres)+len(platforms) > 250 or any(not positive(identifier) for identifier in [*genres,*platforms]) or len(q) > 100:
            raise ProviderError('invalid_request')
        if type(search_mode) is not int or search_mode not in (0,1) or search_mode and not broader_query(q):
            raise ProviderError('invalid_request')
        try:
            first = date.fromisoformat(start) if start else None
            last = date.fromisoformat(end) if end else None
            if first and last and first > last or any(d and not 1900 <= d.year <= 2200 for d in (first,last)):
                raise ValueError()
        except (ValueError, TypeError):
            raise ProviderError('invalid_request') from None
        conditions = ['version_parent=null', 'game_type=(0,4,8,9,10,11)']
        if genres:
            conditions.append('genres=('+','.join(map(str, sorted(set(genres))))+')')
        if platforms:
            choices='('+','.join(map(str, sorted(set(platforms))))+')'
            conditions.append(f'(platforms={choices} | ports.platforms={choices})')
        if sort == 'popular':
            conditions.append('rating_count>0')
        if sort == 'newest' and not last and (not first or first<=date.today()):
            last = date.today()
        if first:
            exact=int(datetime(first.year,first.month,first.day,tzinfo=timezone.utc).timestamp())
            year_start=int(datetime(first.year,1,1,tzinfo=timezone.utc).timestamp())
            conditions.append(f'(first_release_date>={exact} | (first_release_date>={year_start} & release_dates.date_format=(1,2,3,4,5,6)))')
        if last:
            stamp = datetime(last.year,last.month,last.day,tzinfo=timezone.utc)+timedelta(days=1)
            exact=int(stamp.timestamp())
            if end:
                year_end=int(datetime(last.year+1,1,1,tzinfo=timezone.utc).timestamp())
                conditions.append(f'(first_release_date<{exact} | (first_release_date<{year_end} & release_dates.date_format=(1,2,3,4,5,6)))')
            else:
                conditions.append(f'first_release_date<{exact}')
        version='release-precision-v1'
        key = hashlib.sha256(json.dumps([version,sort,offset,sorted(genres),sorted(platforms),start,end]).encode()).hexdigest()
        async def load():
            # Search resolves editions first, then filters canonical metadata.
            # Non-search queries push globally supported filters to IGDB.
            ordering = '' if q else f'sort {"rating_count" if sort == "popular" else "first_release_date"} desc;'
            async def fetch(query):
                prefix=f'search {json.dumps(query)};' if query else 'where '+' & '.join(conditions)+';'
                raw=await self.igdb('games',f'{prefix} fields {CARD_FIELDS}; {ordering} limit 100; offset {offset};')
                if not isinstance(raw,list) or len(raw)>100:raise ProviderError('bad_response')
                return raw
            mode=search_mode
            raw=await fetch(broader_query(q) if mode else q)
            if q and offset==0 and not mode and (broader:=broader_query(q)) and not any(search_candidate(item) for item in raw):
                raw=await fetch(broader);mode=1
            rows, seen = [], set()
            for index, item in enumerate(raw, start=offset+1):
                value = card(item, expansions=bool(q))
                if not value or value['igdb_id'] in seen:
                    continue
                if mode and not matches_partial(q,value['title']):
                    continue
                if q and (genres and not {f'igdb.genre:{g}' for g in genres}.intersection(g['id'] for g in value['genres'])
                    or platforms and not {f'igdb.platform:{p}' for p in platforms}.intersection(value['platform_ids'])):
                    continue
                if not in_range(value,start,end,released_before=date.today().isoformat()
                        if sort=='newest' and last and not end else None):
                    continue
                seen.add(value['igdb_id'])
                rows.append({**value,'source_rank':index})
            return {'rows':rows,'has_more':len(raw)==100,'next_offset':offset+len(raw),
                'search_mode':mode,'source':'IGDB search' if q else 'IGDB accumulated user votes' if sort=='popular' else 'IGDB release dates'}
        if q:
            # Arbitrary search strings stay in the existing bounded HTTP cache,
            # rather than creating unbounded durable query records.
            return {'data':await load(),'stale':False,'error':None,'fetched_at':datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}
        return await self.cache.get('igdb','game_browse_catalogue',key,load)

    async def steam(self):
        async def load():
            return concurrent_chart(await self.request('steam',
                'https://api.steampowered.com/ISteamChartsService/GetGamesByConcurrentPlayers/v1/'))
        result = await self.cache.get('steam', 'game_browse_ranks', 'concurrent', load,
            ttl=timedelta(hours=4), max_stale=timedelta(hours=24))
        self.require_source_time(result['data'].get('source_at'), 86400)
        return result

    async def timeline_search(self, *, snapshot, offset=0, genres=(), platforms=(), start=None, end=None, q=''):
        """Finalize a bounded canonical search window before chronological paging."""
        if not q or not isinstance(snapshot,int) or isinstance(snapshot,bool) or snapshot<0 or not 0<=offset<=1000:
            raise ProviderError('invalid_request')
        key=hashlib.sha256(json.dumps(['release-precision-v1',snapshot,q,sorted(genres),sorted(platforms),start,end]).encode()).hexdigest()
        async def load():
            rows,seen,more=[],set(),True
            position=0;search_mode=0
            for _ in range(10):
                result=await self.catalogue_feed('newest',offset=position,genres=genres,platforms=platforms,start=start,end=end,q=q,search_mode=search_mode)
                data=result['data']
                search_mode=data.get('search_mode',0)
                for value in data['rows']:
                    if value['igdb_id'] not in seen:
                        seen.add(value['igdb_id']);rows.append(value)
                more=bool(data['has_more'])
                if not more:break
                following=data['next_offset']
                if following<=position:raise ProviderError('bad_response')
                position=following
                if position>=1000:break
            rows.sort(key=lambda row:(row.get('release_date') or '',-row['igdb_id']),reverse=True)
            return {'rows':[{**row,'search_rank':row.get('source_rank'),'source_rank':index} for index,row in enumerate(rows,1)],
                'partial':more,'observed_at':datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}
        if offset:
            # A continuation must use its finalized window. Eviction cannot
            # silently reconstruct a different order beneath existing pages.
            data=await self.cache.peek('igdb','game_timeline_windows',key,max_age=timedelta(hours=4))
        else:
            data=(await self.cache.get('igdb','game_timeline_windows',key,load,
                ttl=timedelta(hours=4),max_stale=timedelta(hours=4)))['data']
        rows=data['rows'][offset:offset+100]
        return {'data':{'rows':rows,'has_more':offset+len(rows)<len(data['rows']),'next_offset':offset+len(rows),
            'source':'IGDB search by release date','partial':data['partial'],'coverage_limited':data['partial']},
            'stale':False,'fetched_at':data['observed_at']}

    async def steam_games(self, appids):
        sources = await self.reference('external_game_sources', 'id,name')
        ids = [row['id'] for row in sources['data']['rows'] if row.get('name') == 'Steam']
        if len(ids) != 1 or len(appids) > 100 or any(not positive(appid) for appid in appids):
            raise ProviderError('invalid_request')
        if not appids:
            return []
        fields = 'uid,game.'+',game.'.join(CARD_FIELDS.split(','))
        values = ','.join(json.dumps(str(appid)) for appid in appids)
        return await self.igdb('external_games', f'fields {fields}; where external_game_source={ids[0]} & uid=({values}); limit 500;')

    async def rawg_page(self, page=1):
        if not positive(page) or page > 250:
            raise ProviderError('invalid_request')
        async def load():
            data = await self.request('rawg', 'https://api.rawg.io/api/games',
                params={'key': settings.rawg_api_key, 'ordering':'-metacritic', 'page_size':40, 'page':page})
            if not isinstance(data, dict) or not isinstance(data.get('results'), list):
                raise ProviderError('bad_response')
            rows = [{key: row.get(key) for key in ('id','slug','released','metacritic')} for row in data['results']
                    if positive(row.get('id')) and SLUG.fullmatch(row.get('slug') or '')
                    and isinstance(row.get('metacritic'), int) and not isinstance(row.get('metacritic'), bool)
                    and 0 <= row['metacritic'] <= 100]
            return {'rows': rows, 'has_more':bool(data.get('next')), 'page':page, 'source':'Metacritic via RAWG'}
        return await self.cache.get('rawg', 'game_browse_ranks', f'metacritic:{page}', load,
            ttl=timedelta(days=1), max_stale=timedelta(days=7))

    async def rawg_reference(self, identifier):
        if not positive(identifier):
            raise ProviderError('invalid_request')
        async def load():
            data = await self.request('rawg', f'https://api.rawg.io/api/games/{identifier}', params={'key':settings.rawg_api_key})
            if not isinstance(data, dict) or data.get('id') != identifier:
                raise ProviderError('bad_response')
            return {'metacritic_id':metacritic_id(data.get('metacritic_url')), 'released':data.get('released')}
        return await self.cache.get('rawg', 'game_browse_bridge', str(identifier), load,
            ttl=timedelta(days=7), max_stale=timedelta(days=30))

    async def critic_scores(self, games, *, on_progress=None):
        """Exact IGDB→Wikidata→RAWG identities; missing evidence is not score zero."""
        slugs = sorted({row['igdb_slug'] for row in games if SLUG.fullmatch(row.get('igdb_slug') or '')})
        if len(games) > 24:
            raise ProviderError('invalid_request')
        if not slugs:
            return {}
        async def mappings():
            values = ' '.join(json.dumps(slug) for slug in slugs)
            query = ('SELECT ?key ?item ?slug ?id WHERE { VALUES ?slug { '+values+' } '
                '?item <http://www.wikidata.org/prop/direct/P9968> ?key; <http://www.wikidata.org/prop/P5794> ?statement. '
                '?statement <http://www.wikidata.org/prop/statement/P5794> ?slug; <http://wikiba.se/ontology#rank> ?rank. '
                'FILTER(?rank != <http://wikiba.se/ontology#DeprecatedRank>) '
                'OPTIONAL { ?statement <http://www.wikidata.org/prop/qualifier/P9043> ?id } } LIMIT 500')
            data = await self.request('wikidata', 'https://query.wikidata.org/sparql', params={'query':query,'format':'json'})
            raw = (data.get('results') or {}).get('bindings')
            if not isinstance(raw, list) or len(raw) >= 500:
                raise ProviderError('bad_response')
            forward = bridge_rows(raw)
            by_slug = {}
            for rawg_slug, value in forward.items():
                if value:
                    by_slug.setdefault(value['igdb_slug'], []).append({**value,'rawg_slug':rawg_slug})
            # A contradictory RAWG identity cannot be silently repaired by
            # another binding on the same IGDB slug.
            conflicted = {(row.get('slug') or {}).get('value') for row in raw
                if (row.get('key') or {}).get('value') in forward and forward[(row.get('key') or {}).get('value')] is None}
            return {'mappings':{slug:rows[0] for slug,rows in by_slug.items() if len(rows)==1 and slug not in conflicted}}
        key = hashlib.sha256(json.dumps(slugs).encode()).hexdigest()
        bridges = await self.cache.get('wikidata','game_critic_bridge',key,mappings,
            ttl=timedelta(days=7),max_stale=timedelta(days=14))
        async def score(game):
            if not str(game.get('year') or '').isdigit():
                return None
            binding = bridges['data']['mappings'].get(game.get('igdb_slug'))
            if not binding or binding['igdb_id'] is not None and binding['igdb_id'] != game['igdb_id']:
                return None
            slug = binding['rawg_slug']
            async def load():
                try:
                    raw = await self.request('rawg',f'https://api.rawg.io/api/games/{slug}',params={'key':settings.rawg_api_key})
                except ProviderError as cause:
                    if cause.code=='not_found':
                        return {'score':None,'released':None,'rawg_id':None}
                    raise
                if not isinstance(raw,dict) or raw.get('slug') != slug or not positive(raw.get('id')):
                    raise ProviderError('bad_response')
                value = raw.get('metacritic')
                return {'score':value if isinstance(value,int) and not isinstance(value,bool) and 0<=value<=100 else None,
                    'released':raw.get('released'),'rawg_id':raw['id']}
            result = await self.cache.get('rawg','game_browse_critic',slug,load,
                ttl=timedelta(days=7),max_stale=timedelta(days=30))
            year = str(result['data'].get('released') or '')[:4]
            if not year.isdigit() or not game.get('year') or abs(int(year)-int(game['year']))>2:
                return None
            return result['data']['score']
        async def one(game):
            try:return game['igdb_id'],await score(game)
            except Exception as error:return game['igdb_id'],error
        values={};failed=False
        tasks=[asyncio.create_task(one(game)) for game in games]
        try:
            for finished in asyncio.as_completed(tasks):
                identifier,value=await finished
                if isinstance(value,Exception):failed=True
                else:
                    values[identifier]=value
                    if on_progress:await on_progress(values)
        finally:
            for task in tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
        if failed:
            # An outage must remain retryable, not consume these candidates as
            # if they simply lacked critic evidence for the next two hours.
            raise ProviderError('unavailable')
        return {identifier:value for identifier,value in values.items() if isinstance(value,int) and not isinstance(value,bool)}

    async def bridges(self, keys, *, metacritic=False):
        keys = sorted(set(keys))
        if not keys:
            return {}
        if len(keys) > 40 or any(not SLUG.fullmatch(key) for key in keys):
            raise ProviderError('invalid_request')
        prop = 'P12054' if metacritic else 'P9968'
        key = hashlib.sha256(json.dumps([prop, keys]).encode()).hexdigest()
        async def load():
            values = ' '.join(json.dumps(key) for key in keys)
            query = ('SELECT ?key ?item ?slug ?id WHERE { VALUES ?key { '+values+' } '
                f'?item <http://www.wikidata.org/prop/direct/{prop}> ?key; <http://www.wikidata.org/prop/P5794> ?statement. '
                '?statement <http://www.wikidata.org/prop/statement/P5794> ?slug; <http://wikiba.se/ontology#rank> ?rank. '
                'FILTER(?rank != <http://wikiba.se/ontology#DeprecatedRank>) '
                'OPTIONAL { ?statement <http://www.wikidata.org/prop/qualifier/P9043> ?id } } LIMIT 500')
            data = await self.request('wikidata', 'https://query.wikidata.org/sparql',
                params={'query':query, 'format':'json'})
            return {'mappings':bridge_rows((data.get('results') or {}).get('bindings'))}
        result = await self.cache.get('wikidata', 'game_browse_bridge', key, load,
            ttl=timedelta(days=7), max_stale=timedelta(days=14))
        return result['data']['mappings']

    async def most_played(self):
        ranking = await self.steam()
        observed = ranking['data']['source_at']
        async def load():
            positions = ranking['data']['rows']
            native = await self.steam_games([row['appid'] for row in positions])
            mapped = {}
            for row in native:
                uid = row.get('uid')
                game = row.get('game')
                if not isinstance(uid, str) or not uid.isdigit() or not isinstance(game, dict):
                    continue
                value = card(game)
                if value:
                    mapped.setdefault(int(uid), {})[value['igdb_id']] = value
            rows, seen, unresolved = [], set(), 0
            for position in positions:
                choices = mapped.get(position['appid'], {})
                if len(choices) != 1:
                    unresolved += 1
                    continue
                value = next(iter(choices.values()))
                if value['igdb_id'] in seen:
                    continue
                seen.add(value['igdb_id'])
                rows.append({**value, 'source_rank':position['rank'], 'steam_app_id':position['appid']})
            return {'rows':rows, 'source':ranking['data']['source'], 'source_at':observed,
                'universe':'Steam top 100', 'partial':unresolved > 0 or len(native) >= 500}
        result = await self.cache.get('steam', 'game_browse_cards', f'precision-v1:{observed}', load,
            ttl=timedelta(hours=4), max_stale=timedelta(hours=24))
        result['stale'] = result['stale'] or ranking['stale']
        self.require_source_time(result['data']['source_at'], 86400)
        return result

    async def attention(self, *, sellers=False, offset=0):
        ranking = await self.primitive('Global Top Sellers' if sellers else 'Visits', 'Steam' if sellers else 'IGDB', offset)
        observed = ranking['data']['source_at']
        async def load():
            positions = ranking['data']['rows']
            games = await self.games([row['game_id'] for row in positions])
            mapped = {row['id']:card(row) for row in games}
            rows, seen = [], set()
            for index, position in enumerate(positions, start=offset+1):
                value = mapped.get(position['game_id'])
                if not value or value['igdb_id'] in seen:
                    continue
                seen.add(value['igdb_id'])
                rows.append({**value, 'source_rank':index})
            return {'rows':rows, 'source':ranking['data']['source'], 'source_at':observed,
                'has_more':ranking['data']['has_more'], 'next_offset':offset+len(positions)}
        result = await self.cache.get('igdb', 'game_browse_cards', f'precision-v1:{"sellers" if sellers else "attention"}:{observed}:{offset}', load)
        result['stale'] = result['stale'] or ranking['stale']
        self.require_source_time(observed, 3*86400, empty=not ranking['data']['rows'])
        return result

    async def highest_rated(self, *, page=1, start=0):
        if not isinstance(start, int) or isinstance(start, bool) or not 0 <= start <= 30 or start % 10:
            raise ProviderError('invalid_request')
        ranking = await self.rawg_page(page)
        # Identity fallback is bounded to this segment, not a permanent storage
        # truncation: the response cursor advances through all source segments.
        source = ranking['data']['rows'][start:start+10]
        digest = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()[:24]
        async def load():
            direct = await self.bridges([row['slug'] for row in source])
            missing = [row for row in source if row['slug'] not in direct]
            details = await asyncio.gather(*(self.rawg_reference(row['id']) for row in missing), return_exceptions=True)
            meta = {row['slug']:detail['data']['metacritic_id'] for row, detail in zip(missing, details)
                    if isinstance(detail, dict) and detail['data'].get('metacritic_id')}
            fallback = await self.bridges(list(meta.values()), metacritic=True)
            mappings = {row['id']: direct.get(row['slug']) or fallback.get(meta.get(row['slug'])) for row in source}
            ids = [value['igdb_id'] for value in mappings.values() if value and value['igdb_id']]
            slugs = [value['igdb_slug'] for value in mappings.values() if value and not value['igdb_id']]
            native = await self.games(ids, slugs)
            by_id = {row['id']:row for row in native}
            by_slug = {row['slug']:row for row in native if row.get('slug')}
            rows, seen, unresolved = [], set(), 0
            for index, row in enumerate(source, start=(page-1)*40+start+1):
                binding = mappings[row['id']]
                game = (by_id.get(binding['igdb_id']) if binding['igdb_id'] else by_slug.get(binding['igdb_slug'])) if binding else None
                value = card(game) if game else None
                source_year = str(row.get('released') or '')[:4]
                # External IDs can be wrong or reused. This catches an observed
                # Metacritic Perfect Dark bridge to a different release. A name
                # match is never used to repair or override conflicting evidence.
                if value and (not source_year.isdigit() or not value['year'] or abs(int(source_year)-int(value['year'])) > 2):
                    value = None
                if not value:
                    unresolved += 1
                    continue
                if value['igdb_id'] in seen:
                    continue
                seen.add(value['igdb_id'])
                rows.append({**value, 'source_rank':index, 'metacritic':row['metacritic'], 'rawg_id':row['id'],
                    'identity_evidence':{**binding, 'rawg_slug':row['slug'], 'metacritic_id':meta.get(row['slug'])}})
            next_start = start+len(source)
            return {'rows':rows, 'source':'Metacritic via RAWG', 'partial':unresolved > 0,
                'has_more':next_start < len(ranking['data']['rows']) or ranking['data']['has_more'],
                'next_page':page if next_start < len(ranking['data']['rows']) else page+1,
                'next_start':next_start if next_start < len(ranking['data']['rows']) else 0}
        result = await self.cache.get('rawg', 'game_browse_cards', f'precision-v1:{page}:{start}:{digest}', load,
            ttl=timedelta(days=1), max_stale=timedelta(days=7))
        result['stale'] = result['stale'] or ranking['stale']
        return result
