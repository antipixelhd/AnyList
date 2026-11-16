"""Verify ownership handoff and warm HTTP reads in the isolated test instance."""
import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import asyncpg
import httpx
from fixture import check_environment


async def eventually(check, seconds, label):
    try:
        async with asyncio.timeout(seconds):
            while True:
                value = await check()
                if value:
                    return value
                await asyncio.sleep(.2)
    except TimeoutError:
        raise TimeoutError(f'Timed out waiting for {label}') from None


async def verify(state, catalogue_pid):
    check_environment()
    from core.catalogue_worker import LOCK_KEY

    fixture = json.loads((state/'fixture.json').read_text())
    token = fixture['users'][0]['token']
    dsn = os.environ['DATABASE_URL'].replace('postgresql+asyncpg:', 'postgresql:', 1)
    db = await asyncpg.connect(dsn)

    async def owner(excluding=None):
        pids = await db.fetch("""
            SELECT pid FROM pg_locks
            WHERE locktype = 'advisory' AND granted AND mode = 'ExclusiveLock'
              AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND classid = ($1::bigint >> 32)::oid AND objid = ($1 & 4294967295)::oid
              AND objsubid = 1
        """, LOCK_KEY)
        assert len(pids) <= 1
        return next((row['pid'] for row in pids if row['pid'] != excluding), None)

    try:
        primary = await eventually(lambda: owner(), 10, 'primary owner')
        with (state/'catalogue-standby.log').open('wb') as output:
            standby = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).with_name('server.py')), 'catalogue',
                stdout=output, stderr=output, env={**os.environ, 'STRESS_CATALOGUE_METRICS': 'standby-providers.json'})
            try:
                await asyncio.sleep(3)
                assert standby.returncode is None and await owner() == primary
                os.kill(catalogue_pid, signal.SIGTERM)
                await eventually(lambda: owner(excluding=primary), 15, 'standby promotion')

                # Admit one known stub directly to the durable queue. No API
                # call may start the flight before we observe its publication.
                job = await db.fetchrow("""
                    INSERT INTO catalogue_metadata_snapshots
                        (provider, kind, external_id, entity_id, next_attempt_at, coverage, attempts)
                    SELECT 'igdb', 'game', ci.external_id, ci.entity_id, now() AT TIME ZONE 'UTC',
                        jsonb_build_object('priority', 'interactive', 'requested_at',
                            to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')), 0
                    FROM catalogue_identities ci
                    WHERE ci.namespace = 'igdb.game' AND NOT EXISTS (
                        SELECT 1 FROM catalogue_metadata_snapshots s WHERE s.provider = 'igdb'
                            AND s.kind = 'game' AND s.external_id = ci.external_id)
                    ORDER BY ci.id DESC LIMIT 1
                    ON CONFLICT DO NOTHING RETURNING id, entity_id, external_id
                """)
                assert job is not None, 'No untouched fixture game could be queued'

                async def published():
                    return await db.fetchval("""
                        SELECT fetched_at FROM catalogue_metadata_snapshots
                        WHERE id=$1 AND lease_token IS NULL AND error_code IS NULL
                    """, job['id'])

                await eventually(published, 35, 'durable publication')
                publication = await db.fetchrow(
                    'SELECT fetched_at, expires_at, attempts FROM catalogue_metadata_snapshots WHERE id=$1', job['id'])
                base = f'http://127.0.0.1:{os.environ["BACKEND_PORT"]}'
                path = f'/tracking/games/works/{job["entity_id"]}/metadata'
                async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=10) as client:
                    assert (await client.get(path, params={'primary': 'true'})).status_code in (401, 403)
                    counter = f'/__stress/igdb/{job["external_id"]}'
                    before = (await client.get(counter)).json()['calls']
                    headers = {'Authorization': f'Bearer {token}'}
                    revisions = set()
                    for _ in range(3):
                        reply = await client.get(path, params={'primary': 'true'}, headers=headers)
                        reply.raise_for_status()
                        data = reply.json()
                        assert data['primary_ready'] and data['entity_id'] == job['entity_id']
                        assert data['primary']['title'].startswith('Stress Expedition')
                        revisions.add(data['primary_revision'])
                    after = (await client.get(counter)).json()['calls']
                    assert len(revisions) == 1
                    assert before == after
                    unchanged = await db.fetchrow(
                        'SELECT fetched_at, expires_at, attempts FROM catalogue_metadata_snapshots WHERE id=$1', job['id'])
                    assert publication == unchanged
                    # A title outside the traffic fixture exercises a cold
                    # process cache with a complete public database snapshot.
                    movie = 8855002
                    title = 'Stored public movie fixture'
                    await db.execute("""
                        INSERT INTO catalogue_metadata_snapshots
                            (provider, kind, external_id, payload, fetched_at, expires_at)
                        VALUES ('tmdb', 'movie', $1, $2::jsonb, now() AT TIME ZONE 'UTC',
                            (now() AT TIME ZONE 'UTC') + interval '1 hour')
                        ON CONFLICT (provider, kind, external_id) DO UPDATE SET
                            payload=excluded.payload, fetched_at=excluded.fetched_at, expires_at=excluded.expires_at
                    """, str(movie), json.dumps({
                        'id': movie, 'title': title, 'credits': {}, 'release_dates': {},
                        'recommendations': {}, 'external_ids': {}, 'keywords': {}, 'watch/providers': {},
                    }))
                    reply = await client.get(f'/media/movie/{movie}', headers=headers)
                    reply.raise_for_status()
                    assert reply.json()['title'] == title
                return {'standby_excluded_while_primary_alive': True, 'standby_took_over': True,
                        'published_queued_game': True, 'authenticated_warm_replies': 3,
                        'api_igdb_game_requests_during_warm_replay': 0,
                        'movie_from_database_with_empty_process_cache': True}
            finally:
                if standby.returncode is None:
                    standby.terminate()
                    async with asyncio.timeout(15):
                        await standby.wait()
    finally:
        await db.close()


if __name__ == '__main__':
    print(json.dumps(asyncio.run(verify(Path(sys.argv[1]), int(sys.argv[2])))))
