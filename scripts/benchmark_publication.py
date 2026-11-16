"""Measure public identity writes and empty season receipts in disposable PostgreSQL."""
import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, patch


async def measure(engine, operation, rounds):
    from sqlalchemy import event

    statements = Counter()
    def executed(conn, cursor, statement, params, context, executemany):
        statements[statement.split(None, 1)[0].upper()] += 1
    event.listen(engine.sync_engine, 'before_cursor_execute', executed)
    samples = []
    cpu = time.process_time()
    try:
        for index in range(rounds):
            start = time.perf_counter()
            await operation(index)
            samples.append((time.perf_counter()-start)*1000)
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', executed)
    return {'rounds': rounds, 'sql': dict(statements), 'cpu_seconds': time.process_time()-cpu,
            'median_ms': statistics.median(samples), 'p95_ms': sorted(samples)[min(len(samples)-1, int(len(samples)*.95))]}


async def identities(rounds):
    import test_catalogue_pg as fixture
    from core import catalogue
    from models.catalogue import CatalogueEntity, CatalogueIdentity
    from sqlalchemy import func, select

    if not fixture.URL:
        raise RuntimeError('Set CATALOGUE_TEST_DATABASE_URL to the explicit disposable catalogue database')
    case = fixture.CatalogueDatabaseTests('runTest')
    await case.asyncSetUp()
    try:
        def values(number):
            return {'kind': 'game', 'name': f'Publication {number}', 'attributes': {'game_class': 'game'},
                    'identities': [{'namespace': namespace, 'external_id': str(number*10+offset)}
                        for offset, namespace in enumerate(('igdb.game', 'steam.app', 'rawg.game'))]}

        async def publish(number):
            async with case.Session() as db:
                await catalogue.write_lock(db)
                await catalogue.upsert_entity(db, values(number), 'igdb')
                await db.commit()

        cold = await measure(case.engine, lambda index: publish(90000+index), rounds)
        warm = await measure(case.engine, lambda index: publish(90000), rounds)
        async with case.Session() as db:
            counts = {'entities': await db.scalar(select(func.count()).select_from(CatalogueEntity)),
                      'identities': await db.scalar(select(func.count()).select_from(CatalogueIdentity))}
            rows = (await db.execute(select(CatalogueIdentity.namespace, CatalogueIdentity.external_id,
                CatalogueEntity.kind, CatalogueEntity.name, CatalogueEntity.attributes,
                CatalogueEntity.field_sources, CatalogueEntity.protected_fields).join(
                    CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id)
                .order_by(CatalogueIdentity.namespace, CatalogueIdentity.external_id))).all()
            counts['fingerprint'] = hashlib.sha256(json.dumps([tuple(row) for row in rows], sort_keys=True).encode()).hexdigest()
        return {'cold': cold, 'warm': warm, 'stored': counts, 'provider_requests': 0}
    finally:
        await case.asyncTearDown()


async def empty_seasons():
    import test_season_completion as fixture
    from core.season_completion import run_batch
    from models import Media, TrackedEntry
    from models.base import MediaType
    from models.seasons import SeasonReconcileJob
    from sqlalchemy import delete, func, select

    if not fixture.fixture.URL:
        raise RuntimeError('Set STATISTICS_TEST_DATABASE_URL to an explicit disposable database')
    case = fixture.SeasonDatabaseTests('runTest')
    await case.asyncSetUp()
    try:
        async with case.Session() as db:
            await db.execute(delete(SeasonReconcileJob))
            titles = [Media(title=f'Unhydrated {i}', tmdb_id=980000+i, media_type=MediaType.series) for i in range(20)]
            db.add_all(titles)
            await db.flush()
            db.add_all([TrackedEntry(user_id=case.user_id, media_id=title.id, status='planning') for title in titles])
            await db.commit()
        completed = []
        async def process(_):
            completed.append(await run_batch(case.Session, limit=20))
        result = await measure(case.engine, process, 1)
        async with case.Session() as db:
            remaining = await db.scalar(select(func.count()).select_from(SeasonReconcileJob))
        return {**result, 'completed': completed[0], 'remaining': remaining, 'provider_requests': 0}
    finally:
        await case.asyncTearDown()
        case.doCleanups()


async def cached_sync():
    import test_statistics_pg as fixture
    from core import server_sync
    from models import Collection, CollectionFile, Media, MediaServerConnection
    from models.base import CollectionSource, MediaType
    from sqlalchemy import select

    case = fixture.StatisticsDatabaseTests('runTest')
    await case.asyncSetUp()
    try:
        async with case.Session() as db:
            connection = MediaServerConnection(user_id=case.user_id, type='jellyfin', name='Synthetic',
                url='http://fixture', token='synthetic', server_user_id='fixture')
            titles = [Media(title=f'Cached {i}', tmdb_id=970000+i, media_type=MediaType.movie) for i in range(100)]
            db.add_all([connection, *titles])
            await db.commit()
            connection_id = connection.id
        items = [{'Id': f'file-{i}', 'Name': title.title, 'ProviderIds': {'Tmdb': str(title.tmdb_id)},
                  'DateCreated': '2020-01-01T00:00:00Z', 'Path': f'/fixture/{i}.mkv'} for i, title in enumerate(titles)]
        stats = []
        async def pull(_):
            result = {'movies': 0, 'episodes': 0, 'skipped': 0, 'errors': 0}
            async with case.Session() as db:
                with patch.object(server_sync.tmdb, 'get_movie', AsyncMock(side_effect=AssertionError('Unexpected provider call'))), contextlib.redirect_stdout(io.StringIO()):
                    await server_sync.sync_items(items, MediaType.movie, CollectionSource.jellyfin, db,
                        result, case.user_id, connection_id=connection_id, sync_watched=False, sync_ratings=False)
                await db.commit()
            stats.append(result)
        cold = await measure(case.engine, pull, 1)
        warm = await measure(case.engine, pull, 1)
        async with case.Session() as db:
            rows = (await db.execute(select(Media.tmdb_id, Collection.added_at, CollectionFile.source_id,
                CollectionFile.file_path, CollectionFile.added_at).select_from(CollectionFile).join(Collection).join(Media)
                .where(Collection.user_id == case.user_id).order_by(Media.tmdb_id, CollectionFile.source_id))).all()
        return {'cold_memberships': cold, 'warm_memberships': warm, 'stats': stats,
                'files': len(rows), 'fingerprint': hashlib.sha256(json.dumps([list(row) for row in rows], default=str).encode()).hexdigest(),
                'provider_requests': 0}
    finally:
        await case.asyncTearDown()
        case.doCleanups()


async def main(args):
    result = {'environment': 'disposable PostgreSQL; direct publication and scheduler calls',
              'backend': str(args.backend), 'identities': await identities(args.rounds),
              'empty_seasons': await empty_seasons(), 'cached_sync': await cached_sync()}
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', type=Path, default=Path(__file__).resolve().parents[1]/'backend')
    parser.add_argument('--rounds', type=int, default=100)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 1000:
        parser.error('Use 1–1000 rounds')
    args.backend = args.backend.resolve()
    sys.path[:0] = [str(args.backend), str(args.backend/'tests')]
    os.environ.setdefault('SECRET_KEY', 'publication-benchmark-only')
    os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@127.0.0.1:1/test')
    asyncio.run(main(args))
