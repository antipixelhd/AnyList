import copy
import os
import time
import unittest
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY','games-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core import game_browse as browse
from core.catalogue_providers import ProviderError


def row(identifier,**extra):
    return dict(igdb_id=identifier,igdb_slug=f'game-{identifier}',title=f'Game {identifier}',type='game',game_class='game',
        year='2024',release_date='2024-01-01',genres=[{'id':'igdb.genre:5','name':'RPG'}],
        platform_ids=['igdb.platform:6'],**extra)


class MemoryCache:
    def __init__(self):self.rows={}
    async def get(self,provider,kind,key,loader,**kwargs):
        if key not in self.rows:self.rows[key]=await loader()
        return {'data':copy.deepcopy(self.rows[key]),'stale':False,'fetched_at':'2026-10-21T00:00:00'}
    async def peek(self,provider,kind,key,**kwargs):
        if key not in self.rows:raise ProviderError('cursor_expired')
        return copy.deepcopy(self.rows[key])


class BrowseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sources=AsyncMock()
        self.sources.cache=MemoryCache()
        self.sources.facets.return_value={'genres':[{'id':'igdb.genre:5','name':'RPG'}],
            'platform_groups':[{'id':'pc','devices':[]},{'id':'playstation','devices':[{'id':'igdb.platform:167'},{'id':'igdb.platform:48'}]},
                {'id':'xbox','devices':[{'id':'igdb.platform:169'}]}]}
        self.service=browse.GameBrowse(self.sources)
        self.query=await browse.filters(self.sources,category='trending')

    async def test_filter_namespaces_and_devices_replace_the_broad_family(self):
        query=await browse.filters(self.sources,category='popular',genres='igdb.genre:5',
            platforms='pc,playstation,igdb.platform:167,xbox')
        self.assertEqual(query['platforms'],[3,6,14,167,169])
        for arguments in ({'genres':'tmdb.genre:5'},{'platforms':'igdb.platform:6'},
            {'start':'20260201'},{'start':'2026-02-30'},{'start':'2026-02-01','end':'2020-01-01'},
            {'category':'newest','min_metacritic':80}):
            with self.assertRaises(ProviderError):await browse.filters(self.sources,**({'category':'trending'}|arguments))

    async def test_paging_keeps_unconsumed_rows_and_deduplicates_across_frames(self):
        async def attention(**kwargs):
            offset=kwargs['offset']
            return {'data':{'rows':[row(i) for i in ([1,2,3] if offset==0 else [2,4])],
                'has_more':offset==0,'next_offset':100,'source':'IGDB Visits','source_at':int(time.time())-100+offset},'stale':False}
        self.sources.attention.side_effect=attention
        pages=[];cursor=None
        for _ in range(3):
            page=await self.service.page(self.query,limit=2,cursor=cursor);pages.append(page)
            cursor=page['cursor']
            if not cursor:break
        self.assertEqual([[r['igdb_id'] for r in p['rows']] for p in pages],[[1,2],[3,4]])
        self.assertFalse(pages[-1]['has_more']);self.assertIsNone(cursor)
        self.assertEqual(self.sources.attention.await_count,2)
        self.assertFalse(pages[-1]['partial'])
        again=await self.service.page(self.query,limit=2)
        self.assertEqual(again,pages[0]);self.assertEqual(self.sources.attention.await_count,2)
        with self.assertRaisesRegex(ProviderError,'invalid_cursor'):
            await self.service.page({**self.query,'q':'other'},limit=2,cursor=pages[0]['cursor'])
        self.sources.cache.rows.clear()
        with self.assertRaisesRegex(ProviderError,'cursor_expired'):
            await self.service.page(self.query,limit=2,cursor=pages[0]['cursor'])

    async def test_sparse_filters_continue_past_first_global_slice_and_do_not_claim_exhaustion(self):
        self.sources.attention.return_value={'data':{'rows':[row(1)],'has_more':True,'next_offset':100,
            'source':'IGDB Visits'},'stale':False}
        query={**self.query,'platforms':[167]}
        first=await self.service.page(query,limit=6)
        self.assertEqual(first['rows'],[]);self.assertTrue(first['has_more']);self.assertIsNotNone(first['cursor'])
        self.assertEqual(self.sources.attention.await_count,2)

    async def test_old_projection_cursor_expires_instead_of_replaying_old_port_identities(self):
        self.sources.attention.return_value={'data':{'rows':[row(1),row(2)],'has_more':False,
            'source':'IGDB Visits'},'stale':False}
        first=await self.service.page(self.query,limit=1)
        key,_=browse.decode_cursor(first['cursor'],browse.digest(['v1',self.query,1]))
        self.sources.cache.rows[key].pop('projection')
        with self.assertRaisesRegex(ProviderError,'cursor_expired'):
            await self.service.page(self.query,limit=1,cursor=first['cursor'])

    async def test_critic_unknown_is_excluded_with_partial_coverage_and_exact_zero_is_preserved(self):
        self.sources.attention.return_value={'data':{'rows':[row(i) for i in range(1,6)],'has_more':False,
            'source':'IGDB Visits'},'stale':True}
        self.sources.critic_scores.return_value={1:90,2:0,3:79,5:80}
        result=await self.service.page({**self.query,'min_metacritic':80},limit=6)
        self.assertEqual([r['igdb_id'] for r in result['rows']],[1,5])
        self.assertTrue(result['partial']);self.assertTrue(result['stale']);self.assertFalse(result['has_more'])
        self.assertTrue(all('list_status' not in row for row in result['rows']))

    async def test_timeline_query_uses_its_finalized_source_and_binds_the_cursor_scope(self):
        query=await browse.filters(self.sources,category='newest',q='game',timeline=True)
        self.sources.timeline_search.return_value={'data':{'rows':[row(2),row(1)],'has_more':False,'source':'IGDB search by release date'},'stale':False}
        first=await self.service.page(query,limit=1)
        second=await self.service.page(query,limit=1,cursor=first['cursor'])
        self.assertEqual([first['rows'][0]['igdb_id'],second['rows'][0]['igdb_id']],[2,1])
        self.assertEqual(self.sources.timeline_search.await_count,1)
        with self.assertRaises(ProviderError):await self.service.page({**query,'timeline':False},limit=1,cursor=first['cursor'])

    async def test_critic_provider_failure_is_retryable_instead_of_an_empty_negative_page(self):
        self.sources.attention.return_value={'data':{'rows':[row(1)],'has_more':False,'source':'IGDB Visits'},'stale':False}
        self.sources.critic_scores.side_effect=ProviderError('unavailable')
        query={**self.query,'min_metacritic':80}
        with self.assertRaises(ProviderError):await self.service.page(query)
        self.sources.critic_scores.side_effect=None;self.sources.critic_scores.return_value={1:90}
        recovered=await self.service.page(query)
        self.assertEqual([r['igdb_id'] for r in recovered['rows']],[1])

    async def test_signed_cursor_rejects_tampering_and_expiry(self):
        scope=browse.digest(self.query);key='a'*64
        valid=browse.encode_cursor(scope,key,int(time.time()+100))
        self.assertEqual(browse.decode_cursor(valid,scope)[0],key)
        for value in [valid[:-1]+'x','bad','x'*513,browse.encode_cursor(scope,key,1)]:
            with self.assertRaises(ProviderError):browse.decode_cursor(value,scope)
