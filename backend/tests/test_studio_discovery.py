import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'search-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core import studio_discovery as discovery, contributor_search as search, tmdb
from core.studio_groups import studio_root, studio_members


def candidate(native, name, **kwargs):
    return {'key': f'tmdb:{native}', 'name': name, 'url': f'/studio/tmdb:{native}',
        'image': None, 'roles': [], '_native': native, **kwargs}


class StudioDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        discovery._cache = tmdb._TTLCache(maxsize=512)
        discovery._slots = asyncio.Semaphore(4)
        discovery._inflight.clear()
        self.filters = {'min_votes': 250, 'min_runtime': 30, 'excluded_genres': []}
        self.db = SimpleNamespace(get=AsyncMock(return_value=None))
        self.viewer = SimpleNamespace(id=1)
        filters = patch.object(discovery, 'result_filters', AsyncMock(return_value=self.filters))
        filters.start(); self.addCleanup(filters.stop)
        self.movies = AsyncMock(return_value={'total_results': 1, 'results': [
            {'id': 1, 'title': 'Known Film', 'release_date': '2025-01-01', 'vote_count': 500, 'runtime': 90}]})
        self.series = AsyncMock(return_value={'total_results': 0, 'results': []})
        for name, mock in [('discover_movies', self.movies), ('discover_shows', self.series)]:
            patcher = patch.object(tmdb, name, mock); patcher.start(); self.addCleanup(patcher.stop)

    async def test_empty_and_filtered_companies_do_not_enter_search(self):
        self.movies.return_value = {'results': [{'id':1,'release_date':'2025-01-01','vote_count':1}], 'total_results':1}
        rows, partial = await discovery.verified_studios(self.db,self.viewer,[candidate(123,'Odd Name')],'key')
        self.assertEqual(rows, [])
        self.assertFalse(partial)
        self.assertEqual(self.movies.call_args.kwargs['vote_count_min'],250)
        self.assertEqual(self.movies.call_args.kwargs['minimum_runtime'],30)
        self.assertIn('released_before', self.movies.call_args.kwargs)

    async def test_verified_branches_share_one_result_but_historical_studios_remain_separate(self):
        rows, partial = await discovery.verified_studios(self.db,self.viewer,[
            candidate(429,'DC'),candidate(9993,'DC Entertainment'),candidate(165407,'DC Vertigo'),
            candidate(128064,'DC Films'),candidate(184898,'DC Studios')],'key')
        self.assertFalse(partial)
        self.assertEqual([r['key'] for r in rows],['tmdb:429','tmdb:128064','tmdb:184898'])
        self.assertEqual(rows[0]['name'],'DC Entertainment')
        self.assertIn('429|9993|165407',[call.kwargs['with_companies'] for call in self.movies.call_args_list])
        self.assertEqual(self.movies.await_count,3)
        self.assertEqual(studio_root(128064),128064)
        self.assertEqual(studio_members(184898),(184898,))

    async def test_names_and_prefixes_never_establish_membership(self):
        rows, _ = await discovery.verified_studios(self.db,self.viewer,[candidate(23,'DC'),candidate(24,'DC Films')],'key')
        self.assertEqual([r['key'] for r in rows],['tmdb:23','tmdb:24'])

    async def test_latest_released_works_rank_above_larger_historical_catalogues(self):
        groups={k:[] for k in search.KINDS}
        groups['studio']=[candidate(1,'DC Films',_works=100,_latest='2023-12-20'),
            candidate(2,'DC Studios',_works=7,_latest='2025-07-09')]
        result=search.ranked_results(groups,'dc')['studio']
        self.assertEqual([r['key'] for r in result],['tmdb:2','tmdb:1'])
        self.assertFalse(any(key.startswith('_') for row in result for key in row))

    async def test_concurrent_queries_share_work_and_cancelling_a_view_does_not_cancel_evidence(self):
        started, release = asyncio.Event(), asyncio.Event()
        response=self.movies.return_value
        async def movie(**kwargs):
            started.set(); await release.wait(); return response
        self.movies.side_effect=movie
        first=asyncio.create_task(discovery.studio_evidence(429,self.filters,True,'key'))
        await started.wait()
        second=asyncio.create_task(discovery.studio_evidence(9993,self.filters,True,'other-key'))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError): await first
        release.set()
        self.assertEqual((await second)['works'],1)
        await discovery.studio_evidence(165407,self.filters,True,'key')
        self.assertEqual(self.movies.await_count,1)
        self.assertEqual(self.series.await_count,1)

    async def test_provider_failure_keeps_known_local_studios_and_omits_unverified_matches(self):
        self.movies.side_effect=RuntimeError('private provider failure')
        rows, partial=await discovery.verified_studios(self.db,self.viewer,[candidate(1,'Known',_works=4),candidate(2,'Unknown')],'key')
        self.assertTrue(partial)
        self.assertEqual([r['name'] for r in rows],['Known'])

    async def test_candidate_and_shared_work_limits_are_bounded(self):
        await discovery.verified_studios(self.db,self.viewer,[candidate(i,f'Studio {i}') for i in range(1,30)],'key')
        self.assertEqual(self.movies.await_count,discovery.MAX_CANDIDATES)
        discovery._inflight.update({('occupied',i):None for i in range(discovery.MAX_INFLIGHT)})
        try:
            self.assertIsNone(await discovery.studio_evidence(999999,self.filters,True,'key'))
        finally:
            discovery._inflight.clear()

    async def test_evidence_is_partitioned_by_visibility_filters_but_not_credentials(self):
        await discovery.studio_evidence(123,self.filters,True,'first-key')
        await discovery.studio_evidence(123,self.filters,True,'second-key')
        self.assertEqual(self.movies.await_count,1)
        await discovery.studio_evidence(123,{**self.filters,'min_votes':1000},True,'key')
        self.assertEqual(self.movies.await_count,2)

    async def test_permissive_personal_filters_keep_the_studio_recognition_floor(self):
        self.filters['min_votes']=0
        await discovery.verified_studios(self.db,self.viewer,[candidate(123,'Known Studio')],'key')
        self.assertEqual(self.movies.call_args.kwargs['vote_count_min'],250)
