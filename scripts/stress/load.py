"""Closed-loop authenticated journeys through the compressed Astro frontend."""

import argparse
import asyncio
import json
import math
import random
import time
from collections import Counter, defaultdict
from contextlib import AsyncExitStack
from pathlib import Path

import asyncpg
import httpx
from fixture import GAME_BASE, HOT, TITLE_BASE, check_environment

FRONTEND = 'http://127.0.0.1:57641'
BACKEND = 'http://127.0.0.1:57640'
API = '/api/proxy'
GAMES = '/tracking/games'
LIMITS = {'screen_page': 1.5, 'game_page': 1.5, 'screen_detail_cold': 2.0,
          'game_search_cold': 2.0, 'screen_search_cold': 2.0, 'screen_write': 1.0,
          'game_write': 1.0, 'browse': 1.5, 'contributors': 1.5, 'game_primary_cold': 2.0,
          'screen_people': 1.5}
JOURNEYS = ('screen', 'game', 'screen_search', 'game_search', 'contributors', 'browse', 'list', 'screen_edit', 'game_edit', 'connections')
WEIGHTS = (18, 10, 13, 6, 8, 8, 10, 12, 5, 10)


def percentiles(values):
    if not values:
        return {}
    values = sorted(values)
    return {name: round(values[math.ceil(p*len(values))-1], 4)
            for name, p in [('p50', .5), ('p95', .95), ('p99', .99), ('max', 1)]}


class Stage:
    def __init__(self, client, db, fixture, number, users, duration, think, calibration=False, *, cold_fraction=.23, clients=None):
        self.client, self.db, self.fixture = client, db, fixture
        self.number, self.users, self.duration, self.think = number, users, duration, think
        self.calibration = calibration
        self.cold_fraction = cold_fraction
        self.latency = defaultdict(list)
        self.errors = defaultdict(Counter)
        self.examples = {}
        self.bytes = Counter()
        self.journeys = Counter()
        self.primary_outcomes = defaultdict(Counter)
        self.cold_search = 0
        self.cold_work = (number * 137) % 780
        self.started = None
        self.finished = None
        self.sync_started_ids = []
        self.samples = []
        self.clients = clients

    async def call(self, user, label, path, *, method='GET', body=None, params=None, verify=None):
        started = time.perf_counter()
        try:
            client = self.clients[int(user['username'].rsplit('-', 1)[1]) % len(self.clients)] if self.clients else self.client
            response = await client.request(method, path, params=params, json=body, headers={
                'Cookie': f'token={user["token"]}', 'Origin': FRONTEND,
                'Referer': FRONTEND + '/browse', 'Accept-Encoding': 'gzip',
            })
            self.bytes[label] += len(response.content)
            if not response.is_success:
                raise ValueError(f'HTTP {response.status_code}', response.text[:180])
            if verify and not verify(response):
                raise ValueError('unexpected content', response.text[:180])
            return response
        except (httpx.HTTPError, ValueError) as error:
            name = str(error.args[0]) if isinstance(error, ValueError) and error.args else type(error).__name__
            self.errors[label][name] += 1
            self.examples.setdefault(label, {'path': path, 'error': str(error)[:240]})
            if self.calibration:
                raise RuntimeError(f'{label} {path}: {error}') from error
            return None
        finally:
            self.latency[label].append(time.perf_counter()-started)

    async def api(self, user, label, path, **options):
        return await self.call(user, label, API + path, **options)

    async def journey(self, user, action, rng, cold=False, hot_index=None):
        self.journeys[action] += 1
        suffix = 'cold' if cold else 'warm'
        if hot_index is None:
            hot_index = rng.randrange(HOT)
        screen_index = hot_index
        if cold:
            self.cold_work += 1
            screen_index = 40 + self.cold_work % 900
        media_id = self.fixture['media'][screen_index]
        game_index = 40 + self.cold_work % 1400 if cold else hot_index
        game_id = self.fixture['games'][game_index]
        username = user['username']
        check = self.calibration
        if action == 'screen':
            page = await self.call(user, 'screen_page', f'/title/{media_id}',
                                  verify=lambda r: b'Stress Atlas' in r.content)
            if page:
                native = TITLE_BASE + screen_index
                upstream = f'/shows/{native}' if screen_index % 5 == 4 else f'/media/movie/{native}'
                await asyncio.gather(
                    self.api(user, 'screen_people', f'/tracking/title/{media_id}/people'),
                    self.api(user, f'screen_detail_{suffix}', upstream,
                             verify=(lambda r: bool(r.json())) if check else None),
                )
        elif action == 'game':
            page = await self.call(user, 'game_page', f'/games/{game_id}',
                                  verify=lambda r: b'Stress Expedition' in r.content)
            if page:
                _, primary = await asyncio.gather(
                    self.api(user, 'game_credits', f'{GAMES}/works/{game_id}/credits'),
                    self.api(user, f'game_primary_{suffix}', f'{GAMES}/works/{game_id}/metadata?primary=true',
                             verify=(lambda r: r.json().get('primary_ready') is True) if check else None),
                )
                if primary:
                    data = primary.json()
                    self.primary_outcomes[suffix]['ready' if data.get('primary_ready') else data.get('state', 'unknown')] += 1
        elif action == 'screen_search':
            self.cold_search += int(cold)
            native = TITLE_BASE + 50 + (self.number * 157 + self.cold_search) % 4500 if cold else TITLE_BASE + hot_index
            await self.api(user, f'screen_search_{suffix}', '/media/search',
                           params={'q': f'Stress Atlas {native}'}, verify=lambda r: bool(r.json().get('results')))
        elif action == 'game_search':
            self.cold_search += int(cold)
            native = GAME_BASE + 30 + (self.number * 137 + self.cold_search) % 1500 if cold else GAME_BASE + hot_index
            await self.api(user, f'game_search_{suffix}', GAMES+'/search',
                           params={'q': f'Stress Expedition {native}'}, verify=lambda r: bool(r.json().get('results')))
        elif action == 'contributors':
            await self.api(user, 'contributors', '/tracking/quick-search/contributors',
                           params={'q': 'Atlas Performer' if not cold else f'Atlas Performer {hot_index:02}',
                                   'kind': 'all', 'work_domain': 'movies_series'},
                           verify=(lambda r: 'results' in r.json()) if check else None)
        elif action == 'browse':
            await self.api(user, 'browse', '/tracking/browse', params={'media_type': 'movie',
                           'sort': 'popular', 'page': 1 + hot_index%3, 'region': 'US'})
        elif action == 'list':
            await self.api(user, 'profile_list', f'/tracking/profile/{username}/movie/list?limit=36',
                           verify=lambda r: bool(r.json().get('results')))
        elif action == 'screen_edit':
            index = 1000 + (int(username[-4:])*13 + rng.randrange(40)//4*5) % 4900
            media_id = self.fixture['media'][index]
            await self.api(user, 'screen_write', f'/tracking/entry/{media_id}', method='PATCH',
                           body={'manual_score': rng.choice([7.0, 7.5, 8.0, 8.5]), 'notes': f'Load test edit {rng.randrange(999999)}'})
        elif action == 'game_edit':
            state = await self.api(user, 'game_editor', f'{GAMES}/works/{game_id}')
            if state:
                await self.api(user, 'game_write', f'{GAMES}/entries/{game_id}', method='PATCH',
                               body={'status': 'playing', 'score': rng.choice([7, 8, 9]),
                                     'notes': 'Synthetic edit', 'revision': state.json().get('revision')})
        elif action == 'connections':
            await asyncio.gather(self.api(user, 'connections', '/auth/connections'),
                                 self.api(user, 'sync_status', '/sync/status'))

    async def start_sync(self, user):
        response = await self.api(user, 'sync_admission', f'/sync/connection/{user["connection"]}?full=true', method='POST',
                                  verify=lambda r: bool(r.json().get('job_id')))
        if response:
            self.sync_started_ids.append(response.json()['job_id'])

    async def virtual_user(self, index, user):
        rng = random.Random(3599+self.number*997+index)
        await asyncio.sleep(rng.uniform(0, min(self.think, self.duration/3)))
        windows = min(3, math.ceil(self.duration/32)) if self.duration >= 64 else 1
        window = self.duration/windows
        cohort = (index//4 - self.number) % 3
        sync_time = self.started + window * (cohort + .18 + .48*rng.random())
        wants_sync = user['connection'] and cohort < windows
        while time.perf_counter() < self.finished:
            now = time.perf_counter()
            if wants_sync and now >= sync_time:
                await self.start_sync(user)
                wants_sync = False
            action = rng.choices(JOURNEYS, WEIGHTS)[0]
            await self.journey(user, action, rng, cold=rng.random() < self.cold_fraction)
            await asyncio.sleep(min(rng.uniform(.45, 1.55)*self.think, max(self.finished-time.perf_counter(), 0)))

    async def telemetry(self):
        while time.perf_counter() < self.finished:
            stamp = time.perf_counter()
            try:
                response = await self.client.get(BACKEND + '/__stress/metrics', timeout=3)
                app = response.json()
                pg = await self.db.fetchrow("""
                    SELECT count(*) FILTER (WHERE state = 'active') AS active,
                           count(*) FILTER (WHERE wait_event_type = 'Lock') AS lock_wait,
                           count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_transaction
                    FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()
                """, timeout=3)
                mapper = await self.db.fetchrow("""
                    SELECT sum(quanta) AS turns, sum(processed) AS processed,
                           count(*) FILTER (WHERE error IS NOT NULL) AS errors,
                           count(*) FILTER (WHERE weight > 0 AND next_at <= now() AT TIME ZONE 'UTC') AS due
                    FROM game_mapper_items
                """, timeout=3)
                queues = await self.db.fetchrow("""
                    SELECT count(*) FILTER (WHERE status = 'pending') AS pending,
                           count(*) FILTER (WHERE status = 'running') AS running,
                           count(*) FILTER (WHERE status = 'completed') AS completed,
                           count(*) FILTER (WHERE status = 'failed') AS failed
                    FROM sync_jobs
                """, timeout=3)
                self.samples.append({'elapsed': round(stamp-self.started, 2), 'ts': time.time(),
                                     'app': app, 'pg': dict(pg), 'mapper': dict(mapper), 'sync': dict(queues)})
            except (httpx.HTTPError, asyncpg.PostgresError, ValueError, TimeoutError) as error:
                self.samples.append({'elapsed': round(stamp-self.started, 2), 'telemetry_error': type(error).__name__})
            await asyncio.sleep(max(1-(time.perf_counter()-stamp), .05))

    async def calibration_run(self):
        user = self.fixture['users'][0]
        for action in JOURNEYS:
            await self.journey(user, action, random.Random(52), cold=False)
        for action in ('screen', 'game', 'screen_search', 'game_search'):
            await self.journey(user, action, random.Random(27), cold=True)
        await self.start_sync(user)
        job = self.sync_started_ids[0]
        for _ in range(60):
            row = await self.db.fetchrow('SELECT status, total_items, stats, error_message FROM sync_jobs WHERE id=$1', job)
            if row['status'] in ('completed', 'failed'):
                if row['status'] != 'completed' or not row['total_items'] or json.loads(row['stats']).get('errors'):
                    raise RuntimeError(f'Synthetic Jellyfin pull failed validation: {dict(row)}')
                print(json.dumps({'sync_smoke': dict(row)}), flush=True)
                return
            await asyncio.sleep(.5)
        raise RuntimeError('Smoke sync failed to complete within 30 seconds.')

    async def prime_warm_set(self):
        user = self.fixture['users'][0]
        for hot_index in range(HOT):
            for action in ('screen', 'game', 'screen_search', 'game_search'):
                await self.journey(user, action, random.Random(20), hot_index=hot_index)
        print(json.dumps({'warm_keys_per_operation': HOT}), flush=True)

    async def latency_probe(self, rounds):
        """One user at a time; identical hot keys and real page/follow-up HTTP."""
        for turn in range(rounds):
            for action in ('screen', 'game', 'list', 'connections', 'browse'):
                await self.journey(self.fixture['users'][0], action, random.Random(turn), hot_index=turn % HOT)
        return {'rounds': rounds, 'environment': 'single-user sequential journeys; normal workers remain active',
            'errors': {key: dict(value) for key, value in self.errors.items() if value},
            'labels': {key: {'requests': len(values), **percentiles(values)} for key, values in self.latency.items()}}

    async def run(self):
        self.started = time.perf_counter()
        start_unix = time.time()
        self.finished = self.started + self.duration
        print(json.dumps({'stage_started': self.users, 'unix': start_unix}), flush=True)
        async with asyncio.TaskGroup() as jobs:
            jobs.create_task(self.telemetry())
            for index, user in enumerate(self.fixture['users'][:self.users]):
                jobs.create_task(self.virtual_user(index, user))
        jobs = await self.db.fetch("""
            SELECT status, total_items, extract(epoch FROM updated_at-created_at)::float AS seconds,
                   stats, error_message, updated_at > to_timestamp($2::double precision) AT TIME ZONE 'UTC' AS after_stage
            FROM sync_jobs WHERE id = ANY($1::int[]) ORDER BY id
        """, self.sync_started_ids, start_unix+self.duration)
        request_count = sum(map(len, self.latency.values()))
        errors = sum(sum(values.values()) for values in self.errors.values())
        labels = {key: {'requests': len(values), **percentiles(values), 'errors': dict(self.errors[key]),
                        'response_bytes': self.bytes[key]} for key, values in sorted(self.latency.items())}
        return {'users': self.users, 'stage_start_unix': start_unix, 'duration': self.duration,
                'drain_seconds': round(max(time.perf_counter()-self.finished, 0), 2),
                'journeys': dict(self.journeys), 'journeys_per_sec': round(sum(self.journeys.values())/self.duration, 2),
                'rps': round(request_count/self.duration, 2), 'request_count': request_count, 'errors': errors,
                'samples': self.samples, 'labels': labels, 'error_examples': self.examples,
                'game_primary_outcomes': {k: dict(v) for k, v in self.primary_outcomes.items()},
                'limits_exceeded': {k: v for k, v in labels.items() if v['p95'] > LIMITS.get(k, .75)},
                'sync_jobs': [dict(j) for j in jobs],
                'sync_completed': sum(j['status']=='completed' and not j['after_stage'] for j in jobs),
                'sync_end_to_end_seconds': percentiles([j['seconds'] for j in jobs if j['status']=='completed']),
                'sync_started': len(self.sync_started_ids),
                'p95': percentiles([v for values in self.latency.values() for v in values]).get('p95')}


async def main(args):
    check_environment()
    fixture = json.loads(args.fixture.read_text(encoding='utf-8'))
    if not args.smoke and (not 1 <= max(args.users) <= len(fixture['users']) or min(args.users) < 1
                           or args.seconds < 5 or args.think < 1 or not 0 <= args.cold_fraction <= 1):
        raise SystemExit('Invalid stage settings or insufficient synthetic accounts.')
    db = await asyncpg.connect(host='127.0.0.1', port=57639, user='anylist_stress', database='anylist_test_stress')
    limits = httpx.Limits(max_connections=max(args.users)*3+32, max_keepalive_connections=max(args.users)*2+32)
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(httpx.AsyncClient(base_url=FRONTEND, trust_env=False, timeout=15, limits=limits))
        clients = None
        if args.client_shards > 1:
            shared_limit = math.ceil(max(args.users)/args.client_shards)
            shard_limits = httpx.Limits(max_connections=shared_limit*3+8, max_keepalive_connections=shared_limit*2+8)
            clients = [await stack.enter_async_context(httpx.AsyncClient(base_url=FRONTEND, trust_env=False,
                timeout=15, limits=shard_limits)) for _ in range(args.client_shards)]
        if args.smoke:
            await Stage(client, db, fixture, 0, 1, 30, args.think, calibration=True, clients=clients).calibration_run()
        else:
            if args.prime:
                await Stage(client, db, fixture, 0, 1, 30, args.think, calibration=True, clients=clients).prime_warm_set()
            if args.probe_rounds:
                probe = await Stage(client, db, fixture, 0, 1, 30, args.think, calibration=True, clients=clients).latency_probe(args.probe_rounds)
                args.output.with_name('latency-probe.json').write_text(json.dumps(probe, indent=2), encoding='utf-8')
            with args.output.open('x', encoding='utf-8') as output:
                for number, users in enumerate(args.users):
                    result = await Stage(client, db, fixture, number+args.offset, users, args.seconds, args.think,
                                         cold_fraction=args.cold_fraction, clients=clients).run()
                    result['client_shards'] = args.client_shards
                    print(json.dumps(result), file=output, flush=True)
                    print(json.dumps({k: result[k] for k in ('users', 'rps', 'p95', 'errors', 'sync_completed', 'sync_started')}), flush=True)
                    await asyncio.sleep(3)
    await db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--users', type=int, nargs='+', default=[4, 12, 24, 48, 80, 120, 180, 260, 360])
    parser.add_argument('--seconds', type=int, default=40)
    parser.add_argument('--think', type=float, default=3.5)
    parser.add_argument('--cold-fraction', type=float, default=.23,
                        help='Fraction of journeys using an unprimed title or search key.')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--prime', action='store_true', help='Read all hot search and detail keys before timing stages.')
    parser.add_argument('--probe-rounds', type=int, choices=range(1, 101), default=0,
                        help='Before timed levels, measure serial single-user journeys on primed keys.')
    parser.add_argument('--client-shards', type=int, choices=range(1, 33), default=1,
                        help='Partition traffic connection pools to bound driver overhead at high concurrency.')
    parser.add_argument('--smoke', action='store_true')
    asyncio.run(main(parser.parse_args()))
