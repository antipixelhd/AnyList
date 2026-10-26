import asyncio
import os
import unittest
from datetime import date,datetime,timedelta,timezone
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY', 'games-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core import game_browse_sources as sources
from core.catalogue_providers import ProviderError


def binding(key, item='Q1', slug='game', identifier='10'):
    return {name:{'value':value} for name,value in {'key':key,'item':'http://www.wikidata.org/entity/'+item,
        'slug':slug, **({'id':identifier} if identifier is not None else {})}.items()}


class SourceProjectionsTests(unittest.TestCase):
    def test_ports_share_original_identity_and_platforms_but_remasters_remain_separate(self):
        port={'id':201090,'name':'The New Nightmare','game_type':{'id':11},'platforms':[{'id':22,'name':'Game Boy Color'}]}
        base={'id':1960,'name':'The New Nightmare','game_type':{'id':0},'ports':[port],
              'platforms':[{'id':7,'name':'PlayStation'}]}
        resolved={**port,'parent_game':base}
        self.assertEqual(sources.card(resolved)['igdb_id'],1960)
        self.assertEqual(sources.card({'id':9,'version_parent':resolved})['igdb_id'],1960)
        self.assertEqual(sources.card(resolved)['platform_ids'],['igdb.platform:7','igdb.platform:22'])
        self.assertEqual(sources.card({**resolved,'id':10,'game_type':{'id':9}})['igdb_id'],10)
        for parent in (None,1960,{}, {**base,'id':201090}, {**base,'game_type':{'id':13}}):
            self.assertIsNone(sources.card({**port,'parent_game':parent}))

    def test_pc_has_one_ui_platform_and_namespaced_filter_identities(self):
        raw = {'id':1,'name':'Game','game_type':{'id':0}, 'platforms':[
            {'id':6,'name':'PC (Microsoft Windows)'},{'id':14,'name':'Mac'},{'id':3,'name':'Linux'},
            {'id':167,'name':'PlayStation 5','platform_family':{'name':'PlayStation'}}]}
        value=sources.card(raw)
        self.assertEqual([p['name'] for p in value['platforms']], ['PC','PlayStation 5'])
        self.assertEqual(value['platform_ids'], ['igdb.platform:6','igdb.platform:14','igdb.platform:3','igdb.platform:167'])
        self.assertEqual(sources.card({'id':2,'version_parent':raw})['igdb_id'],1)
        self.assertIsNone(sources.card({**raw,'game_type':{'id':2}}))

    def test_concurrent_chart_rejects_stale_future_duplicate_or_changed_payloads(self):
        now=int(datetime.now(timezone.utc).timestamp())
        payload={'response':{'last_update':now,'ranks':[{'rank':1,'appid':730,'concurrent_in_game':100}]}}
        value=sources.concurrent_chart(payload,now)
        self.assertEqual(value['rows'],[{'rank':1,'appid':730}])
        self.assertNotIn('concurrent_in_game',str(value))
        for body in ({}, {'last_update':now-86401,'ranks':payload['response']['ranks']},
                     {'last_update':now+301,'ranks':payload['response']['ranks']},
                     {'last_update':now,'ranks':payload['response']['ranks']*2},
                     {'last_update':now,'ranks':[{'rank':101,'appid':730,'concurrent_in_game':100}]},
                     {'last_update':now,'ranks':[{'rank':1,'appid':730,'concurrent_in_game':True}]}):
            with self.assertRaises(ProviderError):sources.concurrent_chart({'response':body},now)

    def test_verified_bridge_distinguishes_absent_conflicting_and_missing_numeric_evidence(self):
        value=sources.bridge_rows([binding('a'),binding('a',identifier=None),binding('b'),binding('b',item='Q2')])
        self.assertEqual(value['a'],{'wikidata_id':'Q1','igdb_slug':'game','igdb_id':10})
        self.assertIsNone(value['b']);self.assertNotIn('c',value)
        self.assertEqual(sources.metacritic_id('https://www.metacritic.com/game/dreamcast/soulcalibur'),'soulcalibur')
        for url in ('https://evil.example/game/a','https://www.metacritic.com/person/a','javascript:game/a'):
            self.assertIsNone(sources.metacritic_id(url))


class ImmediateCache:
    async def get(self,provider,kind,key,loader,**kwargs):
        return {'data':await loader(),'stale':False,'fetched_at':'2026-10-21T00:00:00','error':None}


class RankingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sources._slots=asyncio.Semaphore(2)
        self.api=sources.GameBrowseSources()
        self.api.cache=ImmediateCache()

    async def test_partial_periods_survive_non_search_range_filters_without_fake_days(self):
        def raw(ident,when,form):
            value=int(datetime.fromisoformat(when).replace(tzinfo=timezone.utc).timestamp())
            return {'id':ident,'name':'Game','game_type':{'id':0},'first_release_date':value,
                'release_dates':[{'date':value,'date_format':{'format':form}}]}
        self.api.igdb=AsyncMock(return_value=[raw(1,'2027-12-31','YYYY'),raw(2,'2027-03-01','YYYYMM'),
            raw(3,'2027-03-17','YYYYMMDD'),raw(4,'2027-04-01','YYYYMMDD')])
        result=await self.api.catalogue_feed('newest',start='2027-03-15',end='2027-03-20')
        self.assertEqual([(row['igdb_id'],row['release_date']) for row in result['data']['rows']],
            [(1,'2027'),(2,'2027-03'),(3,'2027-03-17')])
        self.assertIn('release_dates.date_format=(1,2,3,4,5,6)',self.api.igdb.await_args.args[1])

    async def test_newest_defaults_to_released_but_respects_an_explicit_future_range(self):
        today=date.today();first=date(today.year+1,1,1);last=first+timedelta(days=30)
        stamp=lambda value:int(datetime(value.year,value.month,value.day,tzinfo=timezone.utc).timestamp())
        self.api.igdb=AsyncMock(return_value=[])
        await self.api.catalogue_feed('newest')
        self.assertIn(f'first_release_date<{stamp(today+timedelta(days=1))}',self.api.igdb.await_args.args[1])
        await self.api.catalogue_feed('newest',start=first.isoformat())
        query=self.api.igdb.await_args.args[1]
        self.assertIn(f'first_release_date>={stamp(first)}',query)
        self.assertNotIn('first_release_date<',query)
        await self.api.catalogue_feed('newest',end=last.isoformat())
        self.assertIn(f'first_release_date<{stamp(last+timedelta(days=1))}',self.api.igdb.await_args.args[1])

    async def test_newest_text_search_applies_the_effective_default_or_explicit_date_cutoff(self):
        today=date.today();future=date(today.year+1,1,1);past=today-timedelta(days=10)
        raw=lambda ident,when:{'id':ident,'name':'Game','game_type':{'id':0},
            'first_release_date':int(datetime(when.year,when.month,when.day,tzinfo=timezone.utc).timestamp()),
            'release_dates':[{'date_format':{'format':'YYYYMMDD'},'y':when.year,'m':when.month,'d':when.day,
                'date':int(datetime(when.year,when.month,when.day,tzinfo=timezone.utc).timestamp())}]}
        self.api.igdb=AsyncMock(return_value=[raw(1,future),raw(2,past)])
        result=await self.api.catalogue_feed('newest',q='Game')
        self.assertEqual([row['igdb_id'] for row in result['data']['rows']],[2])
        result=await self.api.catalogue_feed('newest',q='Game',start=future.isoformat())
        self.assertEqual([row['igdb_id'] for row in result['data']['rows']],[1])
        result=await self.api.catalogue_feed('newest',q='Game',end=future.isoformat())
        self.assertEqual([row['igdb_id'] for row in result['data']['rows']],[1,2])

    async def test_partial_title_search_preserves_canonical_identity_and_explicit_filters(self):
        base={'id':119133,'slug':'elden-ring','name':'Elden Ring','game_type':{'id':0},
            'first_release_date':1645747200,'platforms':[{'id':6,'name':'PC (Microsoft Windows)'}],
            'genres':[{'id':12,'name':'RPG'}]}
        expansion={'id':240009,'name':'Elden Ring: Shadow of the Erdtree','game_type':{'id':2},
            'first_release_date':1718928000,'parent_game':base,'platforms':base['platforms'],'genres':base['genres']}
        async def request(endpoint,query):
            if query.startswith('search "Elden R";'):return []
            self.assertTrue(query.startswith('search "Elden";'))
            return [base,{'id':999,'version_parent':base},expansion,
                {'id':4,'name':'Elder Scrolls','game_type':{'id':0}},
                {'id':5,'name':'Elden Ring Weapon Pack','game_type':{'id':1}}]
        self.api.igdb=AsyncMock(side_effect=request)
        page=(await self.api.catalogue_feed('popular',q='Elden R',platforms=[6],genres=[12]))['data']
        self.assertEqual([row['igdb_id'] for row in page['rows']],[119133,240009])
        self.assertEqual((page['search_mode'],page['next_offset'],page['has_more']),(1,5,False))
        filtered=(await self.api.catalogue_feed('popular',q='Elden R',platforms=[6],end='2023-12-31'))['data']
        self.assertEqual([row['igdb_id'] for row in filtered['rows']],[119133])
        self.assertFalse((await self.api.catalogue_feed('popular',q='Elden R',platforms=[167]))['data']['rows'])

    async def test_broader_continuations_keep_upstream_offsets_and_do_not_repeat_the_primary_probe(self):
        raw=[{'id':i+1,'name':'Elden Ring' if i%2==0 else 'Elder Scrolls','game_type':{'id':0}} for i in range(100)]
        self.api.igdb=AsyncMock(return_value=raw)
        page=(await self.api.catalogue_feed('popular',q='Elden R',offset=100,search_mode=1))['data']
        self.assertEqual((len(page['rows']),page['next_offset'],page['has_more'],page['search_mode']),(50,200,True,1))
        self.assertEqual(self.api.igdb.await_count,1)
        query=self.api.igdb.await_args.args[1]
        self.assertTrue(query.startswith('search "Elden";'));self.assertIn('offset 100;',query)
        for arguments in ({'search_mode':True},{'search_mode':2},{'search_mode':1,'q':'single'}):
            with self.assertRaises(ProviderError):await self.api.catalogue_feed('popular',**({'q':'Elden R'}|arguments))

    async def test_partial_timeline_uses_one_broader_mode_for_the_whole_finalized_window(self):
        async def request(endpoint,query):
            if query.startswith('search "Game P";'):return []
            self.assertTrue(query.startswith('search "Game";'))
            offset=100 if 'offset 100;' in query else 0
            return [{'id':i+1,'name':f'Game Prefix {i}','game_type':{'id':0},'first_release_date':1704067200+i*86400}
                for i in range(offset,102 if offset else 100)]
        self.api.igdb=AsyncMock(side_effect=request)
        result=(await self.api.timeline_search(snapshot=1,q='Game P'))['data']
        self.assertEqual(len(result['rows']),100);self.assertTrue(result['has_more'])
        self.assertEqual(self.api.igdb.await_count,3)
        queries=[call.args[1] for call in self.api.igdb.await_args_list]
        self.assertEqual(sum(query.startswith('search "Game P";') for query in queries),1)
        self.assertTrue(queries[-1].startswith('search "Game";'));self.assertIn('offset 100;',queries[-1])
        dates=[row['release_date'] for row in result['rows']]
        self.assertEqual(dates,sorted(dates,reverse=True))

    async def test_existing_browse_matches_do_not_expand_when_explicit_filters_exclude_them(self):
        self.api.igdb=AsyncMock(return_value=[{'id':1,'name':'Elden Ring','game_type':{'id':0},
            'platforms':[{'id':6,'name':'PC (Microsoft Windows)'}]}])
        result=await self.api.catalogue_feed('popular',q='Elden R',platforms=[167])
        self.assertEqual(result['data']['rows'],[])
        self.assertEqual(result['data']['search_mode'],0)
        self.assertEqual(self.api.igdb.await_count,1)

    async def test_timeline_search_finalizes_canonical_dates_before_exposing_pages_and_reuses_the_snapshot(self):
        from core.game_source_cache import GameSourceCache
        class Memory(ImmediateCache):
            def __init__(self):self.data={}
            async def get(self,provider,kind,key,loader,**kwargs):
                if key not in self.data:self.data[key]=await loader()
                return {'data':self.data[key],'stale':False}
            async def peek(self,provider,kind,key,**kwargs):return self.data[key]
        self.api.cache=Memory()
        rows=[{'igdb_id':i,'release_date':f'{2000+i%20}-01-01','source_rank':i} for i in range(1,111)]
        # The second raw relevance frame includes a duplicate resolved edition,
        # a newer game, and unknown dates that must sort last.
        self.api.catalogue_feed=AsyncMock(side_effect=[{'data':{'rows':rows[:80],'has_more':True,'next_offset':100}},
            {'data':{'rows':rows[80:]+[rows[0],{'igdb_id':999,'release_date':None,'source_rank':999}],'has_more':False,'next_offset':132}}])
        first=await self.api.timeline_search(snapshot=1,q='game')
        second=await self.api.timeline_search(snapshot=1,q='game',offset=100)
        combined=first['data']['rows']+second['data']['rows']
        self.assertEqual(len(combined),111);self.assertEqual(len({r['igdb_id'] for r in combined}),111)
        self.assertEqual([r['release_date'] or '' for r in combined],sorted([r['release_date'] or '' for r in combined],reverse=True))
        self.assertEqual(combined[-1]['igdb_id'],999);self.assertFalse(second['data']['has_more'])
        self.assertEqual(self.api.catalogue_feed.await_count,2)
        self.assertFalse(first['data']['coverage_limited'])
        # The production cache namespace must fit the real snapshot schema.
        GameSourceCache.query('igdb','game_timeline_windows','fixture')

    async def test_timeline_search_marks_a_capped_query_window_as_partial_not_exhaustive(self):
        self.api.catalogue_feed=AsyncMock(return_value={'data':{'rows':[],'has_more':True,'next_offset':100}})
        async def feed(*args,**kwargs):return {'data':{'rows':[],'has_more':True,'next_offset':kwargs['offset']+100}}
        self.api.catalogue_feed.side_effect=feed
        result=await self.api.timeline_search(snapshot=1,q='broad')
        self.assertEqual(self.api.catalogue_feed.await_count,10)
        self.assertTrue(result['data']['coverage_limited']);self.assertTrue(result['data']['partial'])
        self.assertFalse(result['data']['has_more'])

    async def test_critic_filter_uses_verified_slug_bridges_and_rejects_conflicts_wrong_ids_and_years(self):
        rows=[{'igdb_id':i,'igdb_slug':f'game-{i}','year':'2024' if i!=40 else None} for i in [10,20,30,40,50,60]]
        raw=[binding(f'rawg-{i}',slug=f'game-{i}',identifier=str(999 if i==20 else i)) for i in [10,20,30,40,50,60]]
        raw.append(binding('rawg-30',item='Q2',slug='game-30',identifier='30'))
        async def request(provider,url,**kwargs):
            if provider=='wikidata':return {'results':{'bindings':raw}}
            slug=url.rsplit('/',1)[-1];identifier=int(slug.split('-')[-1])
            return {'id':identifier,'slug':slug,'metacritic':0 if identifier==50 else 92,
                'released':'2000-01-01' if identifier==60 else '2024-01-01'}
        self.api.request=AsyncMock(side_effect=request)
        result=await self.api.critic_scores(rows)
        self.assertEqual(result,{10:92,50:0})
        self.assertEqual(self.api.request.await_count,4)
        self.assertFalse(any('name' in call.kwargs.get('params',{}) for call in self.api.request.await_args_list))

    async def test_popularity_type_is_discovered_and_bad_values_or_old_evidence_are_rejected(self):
        self.api.reference=AsyncMock(side_effect=[{'data':{'rows':[{'id':121,'name':'IGDB'}]}},
            {'data':{'rows':[{'id':88,'name':'Visits','external_popularity_source':121}]}}]*4)
        now=int(datetime.now(timezone.utc).timestamp())
        self.api.igdb=AsyncMock(return_value=[{'game_id':1,'value':0.5,'calculated_at':now}])
        result=await self.api.primitive('Visits','IGDB')
        self.assertIn('popularity_type=88',self.api.igdb.call_args.args[1])
        self.assertEqual(result['data']['source_at'],now)
        for value,stamp in ((float('nan'),now),(-1,now),(0.5,now-4*86400)):
            self.api.igdb.return_value=[{'game_id':1,'value':value,'calculated_at':stamp}]
            with self.assertRaises(ProviderError):await self.api.primitive('Visits','IGDB')

    async def test_native_feeds_push_supported_filters_but_resolve_search_editions_first(self):
        raw={'id':1,'name':'Game','slug':'game','game_type':{'id':0},'first_release_date':1609459200,
             'genres':[{'id':12,'name':'RPG'}],'platforms':[{'id':6,'name':'PC (Microsoft Windows)'}]}
        self.api.igdb=AsyncMock(return_value=[raw])
        result=await self.api.catalogue_feed('popular',genres=[12],platforms=[6,14,3],start='2020-01-01',end='2024-12-31')
        query=self.api.igdb.call_args.args[1]
        self.assertIn('genres=(12)',query);self.assertIn('platforms=(3,6,14)',query)
        self.assertIn('rating_count>0',query);self.assertIn('sort rating_count desc',query)
        self.assertEqual(result['data']['rows'][0]['igdb_id'],1)
        self.api.igdb=AsyncMock(return_value=[{'id':2,'version_parent':raw},{'id':3,'version_parent':raw},
            {**raw,'id':4,'game_type':{'id':1}}])
        result=await self.api.catalogue_feed('popular',q='x"; fields *;',genres=[12],platforms=[6])
        query=self.api.igdb.call_args.args[1]
        self.assertTrue(query.startswith('search "x\\"; fields *;";'))
        self.assertNotIn('where version_parent=null',query)
        self.assertEqual([row['igdb_id'] for row in result['data']['rows']],[1])
        self.api.igdb=AsyncMock(return_value=[{**raw,'game_type':{'id':1}}]*100)
        result=await self.api.catalogue_feed('popular',q='DLC')
        self.assertEqual(result['data']['rows'],[])
        self.assertTrue(result['data']['has_more'])
        self.assertEqual(result['data']['next_offset'],100)

    async def test_duplicate_critic_records_collapse_and_wrong_or_unknown_release_stays_unresolved(self):
        self.api.rawg_page=AsyncMock(return_value={'stale':False,'data':{'rows':[
            {'id':1,'slug':'soul-a','metacritic':98,'released':'1998-01-01'},
            {'id':2,'slug':'soul-b','metacritic':98,'released':'1998-01-01'},
            {'id':3,'slug':'perfect','metacritic':97,'released':'2000-01-01'},
            {'id':4,'slug':'unknown','metacritic':97,'released':'2000-01-01'}], 'has_more':False}})
        self.api.bridges=AsyncMock(side_effect=[{}, {'soul':{'igdb_id':1564,'igdb_slug':'soul','wikidata_id':'Q1'},
            'perfect':{'igdb_id':141537,'igdb_slug':'perfect-new','wikidata_id':'Q2'},
            'unknown':{'igdb_id':77,'igdb_slug':'unknown','wikidata_id':'Q3'}}])
        async def reference(identifier):
            return {'data':{'metacritic_id':'soul' if identifier<3 else 'perfect' if identifier==3 else 'unknown'}}
        self.api.rawg_reference=AsyncMock(side_effect=reference)
        self.api.games=AsyncMock(return_value=[
            {'id':1564,'name':'Soul','game_type':{'id':0},'first_release_date':894067200},
            {'id':141537,'name':'Perfect reboot','game_type':{'id':0},'first_release_date':1735689600},
            {'id':77,'name':'Unknown','game_type':{'id':0}}])
        result=await self.api.highest_rated()
        self.assertEqual([r['igdb_id'] for r in result['data']['rows']],[1564])
        self.assertTrue(result['data']['partial'])
        self.assertFalse(result['data']['has_more'])
        self.assertEqual(result['data']['rows'][0]['metacritic'],98)

    async def test_conflicting_rawg_identity_is_not_repaired_by_another_source(self):
        self.api.rawg_page=AsyncMock(return_value={'stale':False,'data':{'rows':[
            {'id':1,'slug':'conflict','metacritic':98,'released':'1998-01-01'}],'has_more':False}})
        self.api.bridges=AsyncMock(side_effect=[{'conflict':None},{}])
        self.api.rawg_reference=AsyncMock()
        self.api.games=AsyncMock(return_value=[])
        result=await self.api.highest_rated()
        self.api.rawg_reference.assert_not_called()
        self.assertEqual(result['data']['rows'],[])
        self.assertTrue(result['data']['partial'])
