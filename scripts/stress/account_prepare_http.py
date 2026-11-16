"""Real HTTP account-preparation probe; only for the private synthetic lab."""
import ast
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fixture import check_environment


def fingerprint(state):
    data = {'before': sorted(state.library_before), 'local': sorted(state.library_local.items()),
            'entries': sorted(state.entries_before.items())}
    return hashlib.sha256(json.dumps(data, default=str, sort_keys=True).encode()).hexdigest()


def install(app):
    """Benchmark-only endpoint: both arms use current auth, SQL and schemas."""
    from contextlib import asynccontextmanager

    from core import scheduler, sync_reconciliation
    from db import get_db
    from dependencies import get_current_user
    from fastapi import Depends, Query
    from models import User
    from sqlalchemy.ext.asyncio import AsyncSession

    check_environment()
    baseline = Path(__file__).resolve().parents[2]/'baseline-reconciliation.py'
    tree = ast.parse(baseline.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'initialize')
    namespace = dict(vars(sync_reconciliation))
    # Execute only a trusted, pinned local initializer, never request-provided code.
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(baseline), 'exec'), namespace)  # noqa: S102
    original = namespace['initialize']

    @asynccontextmanager
    async def no_periodic_jobs():
        # Isolate the account algorithm and its effect on the primary API loop.
        # This probe is not a mixed-load capacity comparison.
        yield
    scheduler.background_jobs = no_periodic_jobs

    @app.post('/__stress/account-prepare')
    async def prepare(db: Annotated[AsyncSession, Depends(get_db, scope='function')],
                      viewer: Annotated[User, Depends(get_current_user)],
                      implementation: Annotated[Literal['baseline', 'indexed'], Query()]):
        state = sync_reconciliation.Reconciliation(viewer.id)
        wall, cpu = time.perf_counter(), time.process_time()
        await (original if implementation == 'baseline' else sync_reconciliation.initialize)(db, state)
        elapsed, cpu = time.perf_counter()-wall, time.process_time()-cpu
        return {'implementation': implementation, 'wall_seconds': elapsed, 'cpu_seconds': cpu,
                'memberships': len(state.library_before), 'fingerprint': fingerprint(state)}


async def seed(sizes):
    from core.security import create_session_token
    from db import AsyncSessionLocal
    from models import (
        Collection,
        CollectionFile,
        Media,
        MediaServerConnection,
        User,
        UserSettings,
    )
    from models.base import CollectionSource, MediaType
    from models.profile import UserProfileData
    from models.tracking import StreamBaseline, TrackedEntry
    from sqlalchemy import insert, select

    async with AsyncSessionLocal() as db:
        existing = {user.username: user for user in await db.scalars(select(User).where(
            User.username.in_([f'prepare-{size}' for size in sizes])))}
        if existing:
            if len(existing) != len(sizes):
                raise RuntimeError('Incomplete preparation fixture; use a fresh disposable state')
            return [(size, create_session_token(existing[f'prepare-{size}'])) for size in sizes]
        media = list(await db.scalars(insert(Media).returning(Media.id, sort_by_parameter_order=True),
            [{'title': f'Prepare {i}', 'tmdb_id': 90000000+i, 'media_type': MediaType.movie} for i in range(max(sizes))]))
        actors = []
        for size in sizes:
            user = User(username=f'prepare-{size}', email=f'prepare-{size}@example.test', api_key=os.urandom(20).hex())
            db.add(user)
            await db.flush()
            db.add_all([UserSettings(user_id=user.id), UserProfileData(user_id=user.id)])
            connection = MediaServerConnection(user_id=user.id, type='stremio', name='Prepare fixture',
                url='http://fixture', token='synthetic', server_user_id='1')
            db.add(connection)
            await db.flush()
            collections = list(await db.scalars(insert(Collection).returning(Collection.id, sort_by_parameter_order=True),
                [{'user_id': user.id, 'media_id': identifier} for identifier in media[:size]]))
            await db.execute(insert(CollectionFile), [{'collection_id': identifier, 'connection_id': connection.id,
                'source': CollectionSource.stremio, 'source_id': str(i)} for i, identifier in enumerate(collections)])
            await db.execute(insert(TrackedEntry), [{'user_id': user.id, 'media_id': identifier, 'status': 'planning'}
                for identifier in media[:size]])
            db.add(StreamBaseline(user_id=user.id, connection_id=connection.id, approved=True, snapshot={
                'library': [str(i) for i in range(size)], 'mappings': {str(i): 90000000+i for i in range(size)},
                'records': {'library': [{'content_id': str(i), 'content_type': 'movie'} for i in range(size)]}}))
            actors.append((size, create_session_token(user)))
        await db.commit()
        return actors


async def main(output):
    check_environment()
    actors = await seed([1000, 5000, 10000])
    results = []
    async with httpx.AsyncClient(base_url='http://127.0.0.1:57640', trust_env=False, timeout=180) as client:
        for size, token in actors:
            for implementation in ('baseline', 'indexed', 'indexed', 'baseline'):
                await client.get('/health')
                started = time.perf_counter()
                pending = asyncio.create_task(client.post('/__stress/account-prepare',
                    params={'implementation': implementation}, headers={'Authorization': 'Bearer '+token}))
                health = []
                await asyncio.sleep(.02)
                while not pending.done():
                    probe = time.perf_counter()
                    response = await client.get('/health')
                    response.raise_for_status()
                    health.append(time.perf_counter()-probe)
                    await asyncio.sleep(.02)
                response = await pending
                response.raise_for_status()
                result = {**response.json(), 'items': size, 'http_seconds': time.perf_counter()-started,
                    'health_max_seconds': max(health, default=0), 'health_samples': len(health)}
                results.append(result)
                print(json.dumps(result), flush=True)
    for size, _ in actors:
        if len({result['fingerprint'] for result in results if result['items'] == size}) != 1:
            raise AssertionError('Prepared membership differs between implementations')
    output.write_text(json.dumps({'environment': 'real authenticated HTTP; disposable PostgreSQL; periodic jobs disabled; actual initialize() body',
        'provider_requests_during_preparation': 0, 'results': results}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    import sys
    asyncio.run(main(Path(sys.argv[1])))
