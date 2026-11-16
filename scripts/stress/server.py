"""Real AnyList app and a transport-level fixture for isolated load tests."""

import asyncio
import json
import os
import re
import time
from collections import Counter
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

import httpx
from fastapi import FastAPI, Request, Response
from fixture import GAME_BASE, TITLE_BASE, check_environment, game, screen_title

provider_requests = Counter()
igdb_games_returned = Counter()
sql = Counter()
provider_delay = int(os.environ.get('STRESS_PROVIDER_MS', '100')) / 1000
media_server_delay = int(os.environ.get('STRESS_JELLYFIN_MS', '60')) / 1000
original_transport = httpx.AsyncHTTPTransport.handle_async_request


def search_number(query):
    match = re.search(r'(\d+)$', query or '')
    return int(match[1]) if match else 0


def tmdb_payload(path, params):
    tokens = path.strip('/').split('/')[1:]
    if not tokens:
        return {}
    kind = tokens[-1] if tokens[0] in ('search', 'discover') else tokens[0]
    if len(tokens) > 1 and tokens[1].isdigit() and kind in ('movie', 'tv'):
        item = screen_title(int(tokens[1]), kind == 'tv')
        section = '/'.join(tokens[2:])
        return item.get(section, {'id': item['id'], 'results': [], 'episodes': []}) if section else item
    if kind == 'person':
        if len(tokens) == 1 or not tokens[1].isdigit():
            return {'page': 1, 'total_pages': 1, 'results': [
                {'id': i + 80_000, 'name': f'Atlas Performer {i:02}', 'popularity': 30-i,
                 'known_for_department': 'Acting', 'known_for': []} for i in range(3)]}
        if tokens[-1] in ('combined_credits', 'movie_credits', 'tv_credits'):
            return {'cast': [dict(screen_title(TITLE_BASE+i), media_type='movie',
                                  character=f'Explorer {i}') for i in range(1, 13)], 'crew': []}
        return {'id': int(tokens[1]), 'name': 'Atlas Performer', 'profile_path': '/atlas.jpg'}
    if kind in ('company', 'network'):
        return {'page': 1, 'total_pages': 1, 'total_results': 1,
                'results': [{'id': 401, 'name': 'Atlas Studio', 'origin_country': 'US'}]}
    if kind == 'genre':
        return {'genres': [{'id': 18, 'name': 'Drama'}]}
    if kind == 'find':
        return {'movie_results': [], 'tv_results': []}
    if kind in ('movie', 'tv', 'trending'):
        number = search_number(params.get('query'))
        first = (number - TITLE_BASE) % 5800 if number >= TITLE_BASE else 0
        return {'page': int(params.get('page', '1')), 'total_pages': 1, 'total_results': 12,
                'results': [screen_title(TITLE_BASE+first+i, kind == 'tv') for i in range(12)]}
    return {'results': [], 'total_pages': 1}


def igdb_rows(endpoint, query):
    if endpoint != 'games':
        return []
    ids = re.search(r'\bid\s*=\s*(?:\(([\d,\s]+)\)|(\d+))', query)
    search = re.search(r'\bsearch\s+"([^";]*)"', query)
    if ids:
        values = [int(x) for x in (ids[1] or ids[2]).split(',')]
    elif search:
        start = search_number(search[1]) or GAME_BASE
        values = range(start, start+8)
    elif re.search(r'\bid\s*>|first_release_date\s*>=', query):
        return []
    else:
        values = range(GAME_BASE, GAME_BASE+8)
    return [game(native) for native in values[:50]]


def imdb_payload(body):
    native = body.get('variables', {}).get('id', '')
    page = {'total': 0, 'edges': [], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}
    if native.startswith('tt') and native[2:].isdigit():
        people = [int(native[2:]) % 180 * 10 + i for i in range(6)]
        credits = [{'node': {'name': {'id': f'nm{8100000+i}', 'nameText': {'text': f'Expedition Performer {i}'}},
                    'category': {'id': 'actor', 'text': 'Actor'}, 'characters': [{'name': f'Explorer {i}'}],
                    'attributes': []}} for i in people]
        return {'data': {'title': {'id': native, 'titleText': {'text': f'Stress Expedition {native[2:]}'},
            'titleType': {'id': 'videoGame'}, 'releaseYear': {'year': 2024},
            'releaseDate': {'year': 2024, 'month': 6, 'day': 15},
            'credits': {**page, 'total': len(credits), 'edges': credits}}}}
    return {'data': {'name': {'id': native, 'nameText': {'text': f'Expedition Performer {native}'}, 'credits': page}}}


async def benchmark_transport(self, request):
    host = request.url.host
    if host == '127.0.0.1':
        return await original_transport(self, request)
    path = request.url.path
    classification = '/'.join(part for part in path.strip('/').split('/')[:3] if not part.isdigit())
    provider_requests[f'{host}/{classification}'] += 1
    await asyncio.sleep(provider_delay)
    if host == 'api.themoviedb.org':
        return httpx.Response(200, json=tmdb_payload(path, request.url.params))
    if host == 'id.twitch.tv':
        return httpx.Response(200, json={'access_token': 'synthetic', 'expires_in': 12000})
    if host == 'api.igdb.com':
        query = (await request.aread()).decode()
        if path.endswith('/multiquery'):
            data = [{'name': name, 'result': igdb_rows(endpoint, body)} for endpoint, name, body in
                    re.findall(r'query\s+(\w+)\s+"([^"]+)"\s*\{([^}]+)\}', query)]
            igdb_games_returned.update({row['id'] for group in data for row in group['result']})
        else:
            data = igdb_rows(path.rsplit('/', 1)[-1], query)
            igdb_games_returned.update({row['id'] for row in data})
        return httpx.Response(200, json=data)
    if host == 'query.wikidata.org':
        return httpx.Response(200, json={'head': {'vars': ['item', 'id']}, 'results': {'bindings': []}})
    if host == 'www.wikidata.org':
        return httpx.Response(200, json={'entities': {}})
    if host == 'caching.graphql.imdb.com':
        return httpx.Response(200, json=imdb_payload(json.loads(await request.aread())))
    provider_requests[f'unsupported:{host}'] += 1
    return httpx.Response(404, json={'message': 'No synthetic response for this provider'})


jellyfin = FastAPI()


@jellyfin.api_route('/{path:path}', methods=['GET', 'POST', 'DELETE'])
async def jellyfin_request(path: str, request: Request):
    await asyncio.sleep(media_server_delay)
    if request.method != 'GET':
        return Response(status_code=204)
    if path.endswith('/Views'):
        return {'Items': [{'Id': 'fixture-movies', 'Name': 'Movies', 'CollectionType': 'movies'}]}
    if path.endswith('/Items'):
        user = path.split('/')[1] if path.startswith('Users/') else 'stress-0000'
        account = search_number(user)
        items = []
        for i in range(48):
            native = TITLE_BASE + 1000 + (account * 13 + (i//4)*5 + i%4) % 4900
            items.append({'Id': f'atlas-{native}', 'Name': f'Stress Atlas {native}',
                          'ProviderIds': {'Tmdb': str(native)}, 'PremiereDate': '2024-06-15',
                          'ProductionYear': 2024, 'RunTimeTicks': 63_000_000_000,
                          'UserData': {'Played': i % 3 == 0, 'PlayCount': int(i % 3 == 0),
                                       'LastPlayedDate': '2026-09-01T11:00:00Z'}})
        return {'Items': items, 'TotalRecordCount': len(items)}
    return {'Id': path.rsplit('/', 1)[-1], 'ServerName': 'Synthetic Jellyfin', 'Version': '10.11.0'}


def observe_database(engine):
    from sqlalchemy import event
    lock_times = WeakKeyDictionary()

    def lock_origin():
        task = asyncio.current_task()
        coroutine = task.get_coro() if task else None
        names = []
        for _ in range(100):
            code = getattr(coroutine, 'cr_code', None)
            if code and '/backend/' in code.co_filename.replace('\\', '/'):
                if code.co_name == 'write_lock':
                    break
                names.append(code.co_filename.replace('\\', '/').split('/backend/')[-1]+':'+code.co_name)
            coroutine = getattr(coroutine, 'cr_await', None)
            if coroutine is None:
                break
        return ' > '.join(names[-2:]) or 'unattributed'

    @event.listens_for(engine.sync_engine, 'before_cursor_execute')
    def statement_started(conn, cursor, statement, params, context, executemany):
        context.stress_start = time.perf_counter()

    @event.listens_for(engine.sync_engine, 'after_cursor_execute')
    def statement_ended(conn, cursor, statement, params, context, executemany):
        sql['count'] += 1
        sql['ms'] += (time.perf_counter() - context.stress_start) * 1000
        if os.getenv('STRESS_PROFILE') == '1':
            from cpu_profile import task_origin
            sql['origin:'+task_origin()] += 1
        if statement == 'SELECT pg_advisory_xact_lock(735903601)':
            origin = lock_origin()
            waited = (time.perf_counter()-context.stress_start)*1000
            sql['catalogue_lock_count'] += 1
            sql['catalogue_lock_wait_ms'] += waited
            sql['catalogue_lock_wait_ms:'+origin] += waited
            lock_times.setdefault(conn, (time.perf_counter(), origin))

    @event.listens_for(engine.sync_engine, 'commit')
    @event.listens_for(engine.sync_engine, 'rollback')
    def transaction_ended(conn):
        acquired = lock_times.pop(conn, None)
        if acquired is not None:
            held = (time.perf_counter()-acquired[0])*1000
            sql['catalogue_lock_hold_ms'] += held
            sql['catalogue_lock_max_hold_ms'] = max(sql['catalogue_lock_max_hold_ms'], held)
            sql['catalogue_lock_hold_ms:'+acquired[1]] += held

    @event.listens_for(engine.sync_engine, 'checkout')
    def observe_pool(dbapi_connection, connection_record, connection_proxy):
        sql['peak_pool'] = max(engine.pool.checkedout(), sql['peak_pool'])


def configure_app():
    check_environment()
    httpx.AsyncHTTPTransport.handle_async_request = benchmark_transport
    from cpu_profile import ProcessTelemetry, record_process
    from db import engine
    from main import app
    if os.getenv('STRESS_ACCOUNT_PREPARE') == '1':
        from account_prepare_http import install
        install(app)
    app.add_middleware(ProcessTelemetry, finish=record_process(
        'api', lambda: {'providers': dict(provider_requests), 'sql': dict(sql)}))
    observe_database(engine)
    app.state.read_worker_factory = 'server:configure_app'
    original_lifespan = app.router.lifespan_context
    loop_lag = []

    async def sample_loop():
        while True:
            deadline = time.perf_counter() + .1
            await asyncio.sleep(.1)
            if len(loop_lag) < 500:
                loop_lag.append(max(0, round((time.perf_counter()-deadline)*1000, 2)))

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            probe = asyncio.create_task(sample_loop())
            try:
                yield
            finally:
                probe.cancel()
                await asyncio.gather(probe, return_exceptions=True)

    app.router.lifespan_context = lifespan

    @app.get('/__stress/metrics')
    async def metrics():
        data = {'providers': dict(provider_requests), 'sql': dict(sql),
                'pool_in_use': engine.pool.checkedout(), 'loop_lag_ms': list(loop_lag)}
        readers = getattr(app.state, 'read_workers', None)
        if readers:
            data['read_workers'] = [{'pid': reader.process.pid, 'origin': reader.origin}
                                    for reader in readers.readers]
        sql['peak_pool'] = data['pool_in_use']
        loop_lag.clear()
        return data

    @app.get('/__stress/igdb/{native}')
    async def game_provider_calls(native: int):
        return {'calls': igdb_games_returned[native]}

    @app.get('/__stress/tasks')
    async def task_stacks():
        stacks = Counter()
        for task in asyncio.all_tasks():
            coroutine = task.get_coro()
            stack = []
            for _ in range(80):
                code = getattr(coroutine, 'cr_code', None) or getattr(coroutine, 'gi_code', None)
                frame = getattr(coroutine, 'cr_frame', None) or getattr(coroutine, 'gi_frame', None)
                if code is not None and frame is not None:
                    stack.append(f'{code.co_name}:{frame.f_lineno}')
                coroutine = getattr(coroutine, 'cr_await', None) or getattr(coroutine, 'gi_yieldfrom', None)
                if coroutine is None:
                    break
            stacks[' / '.join(stack)] += 1
        return dict(stacks)

    return app


if __name__ == '__main__':
    import sys

    import uvicorn

    check_environment()
    if sys.argv[1:] == ['catalogue']:
        import logging
        from pathlib import Path

        import uvloop
        from core.catalogue_worker import serve
        from cpu_profile import record_process
        from db import engine

        logging.basicConfig(level=logging.INFO)
        record_process('catalogue', lambda: {'providers': dict(provider_requests), 'sql': dict(sql)})
        observe_database(engine)
        httpx.AsyncHTTPTransport.handle_async_request = benchmark_transport
        try:
            uvloop.run(serve())
        finally:
            metrics_path = Path(os.environ['DATA_DIR']).parent / os.getenv(
                'STRESS_CATALOGUE_METRICS', 'catalogue-providers.json')
            metrics_path.write_text(json.dumps(dict(provider_requests)))
        raise SystemExit
    serving_jellyfin = sys.argv[1:] == ['jellyfin']
    uvicorn.run(jellyfin if serving_jellyfin else configure_app(), host='127.0.0.1',
                port=57642 if serving_jellyfin else 57640, access_log=False, log_level='warning',
                timeout_graceful_shutdown=5)
