"""Measure real account reconciliation preparation using disposable PostgreSQL."""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path


async def main(args):
    import test_sync_concurrency_pg as fixture
    from core.sync_reconciliation import Reconciliation, initialize
    from models import Collection, CollectionFile, Media
    from models.base import CollectionSource, MediaType
    from models.tracking import StreamBaseline, TrackedEntry
    from sqlalchemy import event, insert

    if not fixture.URL:
        raise RuntimeError('Set SYNC_TEST_DATABASE_URL to explicit disposable PostgreSQL')
    results = []
    for size in args.sizes:
        case = fixture.SyncConcurrencyTests('runTest')
        await case.asyncSetUp()
        try:
            user, connection = case.actors[0]
            async with case.sessions() as db:
                media = list(await db.scalars(insert(Media).returning(Media.id, sort_by_parameter_order=True),
                    [{'title': f'Library {i}', 'tmdb_id': 8000000+i, 'media_type': MediaType.movie,
                      'tmdb_data': {'fixture_description': 'x'*args.metadata_bytes}} for i in range(size)]))
                collections = list(await db.scalars(insert(Collection).returning(Collection.id, sort_by_parameter_order=True),
                    [{'user_id': user, 'media_id': identifier} for identifier in media]))
                await db.execute(insert(CollectionFile), [{'collection_id': identifier,
                    'connection_id': connection, 'source': CollectionSource.jellyfin, 'source_id': str(i)}
                    for i, identifier in enumerate(collections)])
                await db.execute(insert(TrackedEntry), [{'user_id': user,'media_id': identifier,'status': 'planning',
                    'notes': 'n'*args.notes_bytes} for identifier in media])
                db.add(StreamBaseline(user_id=user, connection_id=connection, snapshot={
                    'library': [str(i) for i in range(size)],
                    'mappings': {str(i): 8000000+i for i in range(size)},
                    'records': {'library': [{'content_id': str(i), 'content_type': 'movie'} for i in range(size)]}}))
                await db.commit()
            statements = []
            def executed(conn, cursor, statement, params, context, executemany, statements=statements):
                statements.append(statement)
            event.listen(case.engine.sync_engine, 'before_cursor_execute', executed)
            try:
                state = Reconciliation(user)
                started, cpu = time.perf_counter(), time.process_time()
                async with case.sessions() as db:
                    await initialize(db, state)
                elapsed, cpu = time.perf_counter()-started, time.process_time()-cpu
            finally:
                event.remove(case.engine.sync_engine, 'before_cursor_execute', executed)
            expected = {(connection, identifier) for identifier in media}
            if set(state.library_before) != expected:
                raise AssertionError('Source-library membership changed')
            semantic = {'before': sorted(state.library_before), 'local': sorted(state.library_local.items()),
                        'entries': sorted(state.entries_before.items())}
            result = {'items': size, 'wall_seconds': elapsed, 'cpu_seconds': cpu,
                      'metadata_bytes_per_item': args.metadata_bytes, 'notes_bytes_per_item': args.notes_bytes,
                      'sql_statements': len(statements), 'memberships': len(state.library_before),
                      'metadata_column_selected': any('media.tmdb_data' in statement for statement in statements),
                      'notes_column_selected': any('tracked_entries.notes' in statement for statement in statements),
                      'fingerprint': hashlib.sha256(json.dumps(semantic, default=str, sort_keys=True).encode()).hexdigest(),
                      'provider_requests': 0}
            results.append(result)
            print(json.dumps(result), flush=True)
        finally:
            await case.asyncTearDown()
    args.output.write_text(json.dumps({'environment': 'disposable PostgreSQL; actual initialize() with complete local and source memberships',
        'backend': str(args.backend), 'results': results}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', type=Path, default=Path(__file__).resolve().parents[1]/'backend')
    parser.add_argument('--sizes', nargs='+', type=int, default=[100, 1000, 5000, 10000])
    parser.add_argument('--metadata-bytes', type=int, default=0)
    parser.add_argument('--notes-bytes', type=int, default=0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not all(1 <= n <= 20000 for n in args.sizes):
        parser.error('Use 1..20000 items')
    if not 0 <= args.metadata_bytes <= 16384 or not 0 <= args.notes_bytes <= 4000:
        parser.error('Use 0..16384 metadata bytes and 0..4000 note bytes')
    args.backend = args.backend.resolve()
    sys.path[:0] = [str(args.backend), str(args.backend/'tests')]
    os.environ.setdefault('SECRET_KEY', 'account-prepare-benchmark')
    os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@127.0.0.1:1/test')
    asyncio.run(main(args))
