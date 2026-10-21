import asyncio
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY', 'games-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core import game_browse_sources as sources
from core.catalogue_providers import ProviderError


def binding(key, item='Q1', slug='game', identifier='10'):
    return {name:{'value':value} for name,value in {'key':key,'item':'http://www.wikidata.org/entity/'+item,
        'slug':slug, **({'id':identifier} if identifier is not None else {})}.items()}


class SourceProjectionsTests(unittest.TestCase):
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
