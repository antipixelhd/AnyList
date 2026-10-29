import copy
import os
import time
import unittest
from datetime import date
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

    async def test_most_played_shelf_resolves_six_and_grid_reuses_them_with_complete_paging(self):
        from core.game_browse_sources import GameBrowseSources
        sources=GameBrowseSources()
        sources.cache=MemoryCache()
        sources.steam=AsyncMock(return_value={'data':{'rows':[{'appid':i,'rank':i} for i in range(1,101)],
            'source':'Steam concurrent players','source_at':int(time.time())},'stale':False})
        async def games(appids):
            return [{'uid':str(i),'game':{'id':i,'name':f'Game {i}','game_type':{'id':0}}} for i in appids]
        sources.steam_games=AsyncMock(side_effect=games)
        service=browse.GameBrowse(sources)
        query={**self.query,'category':'most_played'}
        preview=await service.page(query,limit=6)
        self.assertEqual([r['igdb_id'] for r in preview['rows']],list(range(1,7)))
        sources.steam_games.assert_awaited_once_with(list(range(1,7)))
        self.assertTrue(preview['has_more']);self.assertFalse(preview['partial'])
        combined=[];cursor=None
        for _ in range(5):
            page=await service.page(query,limit=24,cursor=cursor)
            combined.extend(r['igdb_id'] for r in page['rows']);cursor=page['cursor']
            if not cursor:break
        self.assertEqual(combined,list(range(1,101)))
        self.assertIsNone(cursor)
        self.assertEqual(sources.steam_games.await_count,2)
        self.assertEqual(sources.steam_games.await_args.args[0],list(range(7,101)))
        # A later Steam update must not change the earlier preview cursor.
        calls=sources.steam.await_count
        sources.steam.return_value={'data':{'rows':[{'appid':200+i,'rank':i} for i in range(1,101)],
            'source':'Steam concurrent players','source_at':int(time.time())+1},'stale':False}
        following=await service.page(query,limit=6,cursor=preview['cursor'])
        self.assertEqual([r['igdb_id'] for r in following['rows']],list(range(7,13)))
        self.assertEqual(sources.steam.await_count,calls)

    async def test_most_played_continues_past_excluded_duplicate_and_unmapped_leading_entries(self):
        from core.game_browse_sources import GameBrowseSources
        sources=GameBrowseSources();sources.cache=MemoryCache()
        sources.steam=AsyncMock(return_value={'data':{'rows':[{'appid':i,'rank':i} for i in range(1,13)],
            'source':'Steam concurrent players','source_at':int(time.time())},'stale':False})
        async def games(appids):
            return [{'uid':str(i),'game':{'id':3 if i==7 else i,'name':f'Game {i}','game_type':{'id':0},
                'genres':[{'id':5 if i>2 else 9,'name':'Genre'}]}} for i in appids if i!=2]
        sources.steam_games=AsyncMock(side_effect=games)
        query={**self.query,'category':'most_played','genres':['igdb.genre:5']}
        service=browse.GameBrowse(sources)
        first=await service.page(query,limit=6)
        self.assertEqual([r['igdb_id'] for r in first['rows']],[3,4,5,6,8,9])
        self.assertTrue(first['partial']);self.assertTrue(first['has_more'])
        second=await service.page(query,limit=6,cursor=first['cursor'])
        self.assertEqual([r['igdb_id'] for r in second['rows']],[10,11,12])
        self.assertFalse(second['has_more']);self.assertEqual(sources.steam_games.await_count,2)

    async def test_future_range_does_not_reuse_the_previous_clamped_empty_page(self):
        first=f'{date.today().year+1}-01-01'
        query=await browse.filters(self.sources,category='newest',start=first)
        old_scope=browse.digest(['v1',query,6]);old_key=browse.digest([2,old_scope,int(time.time()//7200)])
        self.sources.cache.rows[old_key]={'rows':[],'expires':int(time.time()+7200),'has_more':False,
            'partial':False,'coverage_limited':False,'stale':False,'source':'IGDB release dates',
            'source_at':None,'fetched_at':'2026-10-25T00:00:00'}
        self.sources.catalogue_feed.return_value={'data':{'rows':[{**row(1),'release_date':first}],
            'has_more':False,'next_offset':1,'source':'IGDB release dates'},'stale':False}
        page=await self.service.page(query,limit=6)
        self.assertEqual([item['igdb_id'] for item in page['rows']],[1])
        self.sources.catalogue_feed.assert_awaited_once()

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
        key,_=browse.decode_cursor(first['cursor'],browse.digest([browse.RELEASE_POLICY,self.query,1]))
        self.sources.cache.rows[key].pop('projection')
        with self.assertRaisesRegex(ProviderError,'cursor_expired'):
            await self.service.page(self.query,limit=1,cursor=first['cursor'])

    async def test_old_release_policy_cannot_reuse_game_offsets_in_release_event_feed(self):
        old=browse.encode_cursor(browse.digest(['release-precision-v1',self.query,1]),'a'*64,int(time.time()+100))
        with self.assertRaisesRegex(ProviderError,'invalid_cursor'):
            await self.service.page(self.query,limit=1,cursor=old)

    async def test_erotic_filter_precedes_critic_work_and_binds_cursor_visibility(self):
        self.sources.attention.return_value={'data':{'rows':[row(1,is_erotic=True),row(2),row(3)],
            'has_more':False,'source':'IGDB Visits'},'stale':False}
        self.sources.critic_scores.return_value={1:90,2:90,3:95}
        filtered=await self.service.page({**self.query,'min_metacritic':80},limit=1)
        self.assertEqual([r['igdb_id'] for r in filtered['rows']],[2])
        self.assertEqual([r['igdb_id'] for r in self.sources.critic_scores.call_args.args[0]],[2,3])
        visible={**self.query,'min_metacritic':80,'hide_erotic_games':False}
        unfiltered=await self.service.page(visible,limit=1)
        self.assertEqual([r['igdb_id'] for r in unfiltered['rows']],[1])
        with self.assertRaisesRegex(ProviderError,'invalid_cursor'):
            await self.service.page(visible,limit=1,cursor=filtered['cursor'])
        second=await self.service.page({**self.query,'min_metacritic':80},limit=1,cursor=filtered['cursor'])
        self.assertEqual([r['igdb_id'] for r in second['rows']],[3])

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

    async def test_partial_query_mode_stays_in_continuations_after_buffered_rows_are_consumed(self):
        async def feed(*args,offset=0,search_mode=0,**kwargs):
            if offset:
                self.assertEqual((offset,search_mode),(100,1))
            return {'data':{'rows':[row(1),row(2)] if not offset else [row(3)],
                'has_more':not offset,'next_offset':100 if not offset else 101,'search_mode':1,'source':'IGDB search'}}
        self.sources.catalogue_feed.side_effect=feed
        query={**self.query,'q':'Game P'};pages=[];cursor=None
        for _ in range(3):
            page=await self.service.page(query,limit=1,cursor=cursor);pages.extend(row['igdb_id'] for row in page['rows']);cursor=page['cursor']
        self.assertEqual(pages,[1,2,3]);self.assertIsNone(cursor)
        self.assertEqual(self.sources.catalogue_feed.await_count,2)

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
