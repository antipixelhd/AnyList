"""Compare old page-shell calls with one context using disposable PostgreSQL/ASGI."""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


async def run(rounds):
    from models.base import CollectionSource
    from models.sync import SyncJob, SyncStatus
    from test_page_context import PageContextTests

    fixture = PageContextTests()
    await fixture.asyncSetUp()
    samples = []
    try:
        for history in (0, 200):
            if history:
                fixture.db.add_all([SyncJob(user_id=fixture.owner.id, source=CollectionSource.trakt,
                    status=SyncStatus.completed, stats={'items': 'x'*1024}) for _ in range(history)])
                await fixture.db.commit()
            fingerprints = set()
            for iteration in range(rounds+1):
                for mode in (('legacy', 'context') if iteration % 2 else ('context', 'legacy')):
                    fixture.statements.clear()
                    wall, cpu = time.perf_counter(), time.process_time()
                    if mode == 'legacy':
                        value = {name: await fixture.get(path) for name, path in (
                            ('user', '/auth/me'), ('settings', '/auth/settings'),
                            ('preferences', '/tracking/preferences'), ('profile', '/profile/me'),
                            ('notifications', '/tracking/recent-events'))}
                    else:
                        value = await fixture.get('/auth/page-context')
                    elapsed, cpu = time.perf_counter()-wall, time.process_time()-cpu
                    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
                    fingerprint = hashlib.sha256(data).hexdigest()
                    fingerprints.add(fingerprint)
                    if iteration:
                        samples.append({'history': history, 'mode': mode, 'wall_seconds': elapsed, 'cpu_seconds': cpu,
                            'sql': sum(not sql.startswith(('SAVEPOINT', 'RELEASE SAVEPOINT')) for sql in fixture.statements),
                            'payload_bytes': len(data), 'fingerprint': fingerprint})
            if len(fingerprints) != 1:
                raise AssertionError('Page-context and legacy response values differ')
    finally:
        await fixture.asyncTearDown()
    return {'environment': 'sequential real ASGI handlers; disposable PostgreSQL; excludes browser/network/worker CPU',
        'provider_requests': 0, 'samples': samples}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=10, choices=range(1, 101))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    url = os.environ.get('TRACKING_TEST_DATABASE_URL', '')
    if not urlsplit(url).path.startswith('/anylist_test'):
        parser.error('Set TRACKING_TEST_DATABASE_URL to an explicit disposable anylist_test database')
    os.environ['DATABASE_URL'] = url
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root/'backend'), str(root/'backend/tests')]
    args.output.write_text(json.dumps(asyncio.run(run(args.rounds)), indent=2), encoding='utf-8')
    print('Saved paired page-context measurements')
