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
        self.filters = {'min_votes':250, 'min_runtime':30, 'excluded_genres':[]}
        self.db = SimpleNamespace(get=AsyncMock(return_value=None))
        self.viewer = SimpleNamespace(id=1)
        self.cached = AsyncMock(return_value=({}, True))
        for name, mock in [('result_filters', AsyncMock(return_value=self.filters)),
                           ('local_evidence', AsyncMock(return_value={})), ('cached_evidence', self.cached)]:
            patcher=patch.object(discovery,name,mock);patcher.start();self.addCleanup(patcher.stop)
        self.movies=AsyncMock(side_effect=AssertionError('Search must never discover works inline'))
        patcher=patch.object(tmdb,'discover_movies',self.movies);patcher.start();self.addCleanup(patcher.stop)

    async def test_unknown_studios_are_queued_without_provider_work(self):
        rows,pending=await discovery.verified_studios(self.db,self.viewer,[candidate(123,'Odd Name')],'key')
        self.assertEqual(rows,[])
        self.assertTrue(pending)
        self.movies.assert_not_called()
        self.assertEqual(len(self.cached.call_args.args[1]),1)

    async def test_verified_branches_share_one_result_but_historical_studios_remain_separate(self):
        from core.contributor_evidence import evidence_key
        jobs={evidence_key('studio',native,self.filters,True)[0]:{'complete':True,'works':[
            {'id':1,'media_type':'movie','release_date':'2025-01-01','vote_count':500}]}
            for native in (429,128064,184898)}
        self.cached.return_value=(jobs,False)
        rows,pending=await discovery.verified_studios(self.db,self.viewer,[candidate(429,'DC'),
            candidate(9993,'DC Entertainment'),candidate(165407,'DC Vertigo'),
            candidate(128064,'DC Films'),candidate(184898,'DC Studios')],'key')
        self.assertFalse(pending)
        self.assertEqual([r['key'] for r in rows],['tmdb:429','tmdb:128064','tmdb:184898'])
        self.assertEqual(rows[0]['name'],'DC Entertainment')
        self.assertEqual(len(self.cached.call_args.args[1]),3)
        self.assertEqual(studio_root(128064),128064)
        self.assertEqual(studio_members(184898),(184898,))

    async def test_only_filtered_local_evidence_can_establish_a_result(self):
        with patch.object(discovery,'local_evidence',AsyncMock(return_value={'catalogue:1':{'works':1,'latest':'2025-01-01'}})):
            rows,_=await discovery.verified_studios(self.db,self.viewer,[
                {**candidate(12,'Known'),'key':'catalogue:1'},candidate(23,'Unknown',_works=100)],None)
        self.assertEqual([r['name'] for r in rows],['Known'])

    async def test_candidate_limits_and_recognition_floor_are_preserved(self):
        self.filters['min_votes']=0
        await discovery.verified_studios(self.db,self.viewer,[candidate(i,f'Studio {i}') for i in range(1,30)],'key')
        jobs=self.cached.call_args.args[1]
        self.assertEqual(len(jobs),discovery.MAX_CANDIDATES)
        self.assertTrue(all(config['filters']['min_votes']==250 for config in jobs.values()))

    async def test_latest_released_works_rank_above_larger_historical_catalogues(self):
        groups={k:[] for k in search.KINDS}
        groups['studio']=[candidate(1,'DC Films',_works=100,_latest='2023-12-20'),
            candidate(2,'DC Studios',_works=7,_latest='2025-07-09')]
        result=search.ranked_results(groups,'dc')['studio']
        self.assertEqual([r['key'] for r in result],['tmdb:2','tmdb:1'])
        self.assertFalse(any(key.startswith('_') for row in result for key in row))
