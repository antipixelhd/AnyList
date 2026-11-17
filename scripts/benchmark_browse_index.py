"""Paired SQL plans/timings on an explicitly disposable 6,000-title catalogue."""
import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


async def run():
    from models import Media
    from models.base import MediaType
    from sqlalchemy import Float, insert, text, update
    from test_browse_metrics import (
        BrowseMetricsTests,
        explain_plan,
        previous_filters,
        query,
    )
    fixture = BrowseMetricsTests()
    await fixture.asyncSetUp()
    samples = []
    writes = {}
    try:
        ids = list(await fixture.db.scalars(insert(Media).returning(Media.id), [{'title': f'Index probe {i}',
            'media_type': MediaType.movie, 'runtime': 90, 'tmdb_data': {'vote_count':500, 'popularity':30, 'overview':'x'*1500}}
            for i in range(6000)]))
        for dataset in ('empty', 'ranked'):
            if dataset == 'ranked':
                await fixture.db.execute(update(Media).where(Media.id.in_(ids)).values(tmdb_data={
                    'vote_count':500, 'popularity':30, 'imdb':{'votes':25000, 'popularity_rank':10}, 'overview':'x'*1500}))
            await fixture.db.execute(text('ANALYZE media'))
            for sort in ('popular', 'trending'):
                fingerprints = set()
                for mode in ('reference', 'indexed'):
                    if mode == 'reference':
                        with patch('core.browse_metrics.ImdbVotes', lambda data:data['imdb']['votes'].astext.cast(Float)), \
                             patch('core.browse_metrics.ImdbRank', lambda data:data['imdb']['popularity_rank'].astext.cast(Float)), \
                             patch('core.browse.apply_result_filters', previous_filters):
                            statement = query(sort)
                    else:
                        statement = query(sort)
                    plan = await explain_plan(fixture.db, fixture.engine, statement)
                    for iteration in range(11):
                        started = time.perf_counter()
                        rows = list(await fixture.db.scalars(statement))
                        elapsed = time.perf_counter()-started
                        fingerprint = hashlib.sha256(json.dumps([row.id for row in rows]).encode()).hexdigest()
                        fingerprints.add(fingerprint)
                        if iteration:
                            samples.append({'dataset': dataset, 'sort': sort, 'mode': mode, 'seconds': elapsed,
                                'fingerprint': fingerprint, 'rows': len(rows), 'plan': plan if iteration==1 else None})
                if len(fingerprints) != 1:
                    raise AssertionError('Ranking identities/order differ')
        # Each schema is disposable. Compare identical bounded metadata updates
        # before/after removing only the two candidate ranking indexes. Savepoint
        # rollback avoids changing fixture contents; this excludes commit/fsync.
        for mode in ('indexed', 'without_ranking_indexes'):
            if mode == 'without_ranking_indexes':
                await fixture.db.execute(text('DROP INDEX idx_media_imdb_votes'))
                await fixture.db.execute(text('DROP INDEX idx_media_imdb_rank'))
            elapsed = []
            for turn in range(11):
                transaction = await fixture.db.begin_nested()
                started = time.perf_counter()
                await fixture.db.execute(update(Media).where(Media.id.in_(ids[:100])).values(tmdb_data={
                    'vote_count':500, 'popularity':30, 'imdb':{'votes':25000+turn, 'popularity_rank':10}, 'overview':'updated'*220}))
                seconds = time.perf_counter()-started
                await transaction.rollback()
                if turn:
                    elapsed.append(seconds)
            writes[mode] = {'rows_per_update':100, 'samples_seconds':elapsed, 'median_seconds':statistics.median(elapsed)}
    finally:
        await fixture.asyncTearDown()
    return {'environment':'controlled SQL; disposable PostgreSQL; 6000 titles; zero provider calls',
            'samples':samples, 'metadata_updates_without_commit':writes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    url = os.environ.get('TRACKING_TEST_DATABASE_URL','')
    if not urlsplit(url).path.startswith('/anylist_test'):
        parser.error('Set an explicit disposable TRACKING_TEST_DATABASE_URL named anylist_test...')
    os.environ['DATABASE_URL'] = url
    os.environ.setdefault('SECRET_KEY', 'disposable-browse-benchmark')
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root/'backend'),str(root/'backend/tests')]
    args.output.write_text(json.dumps(asyncio.run(run()),indent=2),encoding='utf-8')
    print('Saved paired ranking plans and fingerprints')
