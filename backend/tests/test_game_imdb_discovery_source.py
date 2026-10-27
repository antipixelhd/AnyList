"""Reverse game discovery never establishes identity through a title search."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY','discovery-source-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_imdb_discovery_source import GameIMDbDiscoverySource,game_reference
from core.catalogue_providers import ProviderError
from test_game_credit_sources import item,claim,snak


def mapped(q,imdb,igdb,slug='game',**claims):
    return item(q,claims={'P345':[claim(imdb)],'P5794':[claim(slug,qualifiers={'P9043':[snak(str(igdb))]})],**claims})


def raw(native,kind=0,**values):
    return {'id':native,'name':'Unrelated provider display name','slug':f'game-{native}','game_type':{'id':kind},**values}


class Cache:
    def __init__(self):self.rows={}
    async def get(self,provider,kind,key,loader,**kwargs):
        if key not in self.rows:self.rows[key]=await loader()
        return {'data':self.rows[key],'stale':False,'fetched_at':'2026-10-25T00:00:00'}


class DiscoverySourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http=SimpleNamespace(request=AsyncMock())
        self.sources=SimpleNamespace(api=SimpleNamespace(http=self.http),cache=Cache(),igdb=AsyncMock())
        self.wiki=SimpleNamespace(entities=AsyncMock())
        self.api=GameIMDbDiscoverySource(None,sources=self.sources,wiki=self.wiki)

    def response(self,pairs):
        self.http.request.return_value={'results':{'bindings':[
            {'title':{'value':title},'item':{'value':'http://www.wikidata.org/entity/'+q}} for title,q in pairs]}}

    async def test_explicit_numeric_qualifier_and_standalone_ids_are_batched_without_title_matching(self):
        self.response([('tt1','Q1'),('tt2','Q2')])
        one=mapped('Q1','tt1',10)
        two=item('Q2',claims={'P345':[claim('tt2')],'P9043':[claim('20')]})
        self.wiki.entities.return_value={'data':{'entities':{'Q1':one,'Q2':two}},'stale':False}
        self.sources.igdb.return_value=[raw(10),raw(20,9)]
        result=await self.api.page(['tt1','tt2'])
        self.assertEqual([(row['id'],row['status'],row['igdb_id']) for row in result['outcomes']],
            [('tt1','verified',10),('tt2','verified',20)])
        self.assertEqual(self.http.request.await_count,1);self.sources.igdb.assert_awaited_once()
        call=self.http.request.await_args
        self.assertEqual(call.args[:3],('wikidata','POST','https://query.wikidata.org/sparql'))
        self.assertIn('VALUES ?title { "tt1" "tt2" }',call.kwargs['data']['query'])
        self.assertEqual(result['outcomes'][1]['document']['work']['attributes']['game_class'],'remaster')
        self.assertFalse(result['outcomes'][0]['document']['coverage']['credits_complete'])
        self.assertNotIn('search ',self.sources.igdb.await_args.args[1])

    async def test_query_service_outage_recovers_exact_statement_candidates_and_verifies_claims(self):
        self.http.request.side_effect=[ProviderError('rate_limited',3600),{'query':{'search':[{'title':'Q1'},{'title':'Q2'}]}}]
        self.wiki.entities.return_value={'data':{'entities':{'Q1':mapped('Q1','tt1',10),'Q2':mapped('Q2','tt999',20)}},'stale':False}
        self.sources.igdb.return_value=[raw(10)]
        result=await self.api.page(['tt1','tt2'])
        self.assertEqual([row['status'] for row in result['outcomes']],['verified','unmapped'])
        self.assertEqual(self.http.request.await_count,2)
        call=self.http.request.await_args
        self.assertEqual(call.args,('wikidata','GET','https://www.wikidata.org/w/api.php'))
        self.assertEqual(call.kwargs['params']['srsearch'],'haswbstatement:P345=tt1|P345=tt2')

    async def test_index_fallback_truncation_and_missing_entities_are_not_verified_negatives(self):
        for response in ({'query':{'search':[]},'continue':{'sroffset':50}},
                         {'error':{'code':'maxlag'}},{'query':{'search':[{'title':{'bad':'Q1'}}]}}):
            self.http.request.return_value=response
            with self.assertRaises(ProviderError):await self.api.indexed_items(['tt1'])
        self.http.request.return_value={'query':{'search':[{'title':'Q1'}]}}
        self.wiki.entities.return_value={'data':{'entities':{}},'stale':False}
        with self.assertRaises(ProviderError):await self.api.indexed_items(['tt1'])

    async def test_verified_expansion_fetches_its_typed_parent_and_ordinary_dlc_is_excluded(self):
        self.response([('tt1','Q1'),('tt2','Q2')])
        self.wiki.entities.return_value={'data':{'entities':{'Q1':mapped('Q1','tt1',10),'Q2':mapped('Q2','tt2',20)}},'stale':False}
        self.sources.igdb.side_effect=[[raw(10,2,parent_game={'id':30}),raw(20,1)], [raw(30)]]
        result=await self.api.page(['tt1','tt2']);expansion,dlc=result['outcomes']
        self.assertEqual((expansion['status'],dlc['status'],dlc['reason']),('verified','excluded','dlc'))
        parent=next(edge for edge in expansion['document']['relationships'] if edge['relation']=='expansion')
        self.assertEqual(parent['target']['attributes']['game_class'],'game')
        self.assertEqual(self.sources.igdb.await_count,2)

    async def test_conflicts_missing_entities_and_verified_no_mapping_are_distinct(self):
        self.response([('tt1','Q1'),('tt1','Q2'),('tt2','Q3')])
        self.wiki.entities.return_value={'data':{'entities':{'Q1':mapped('Q1','tt1',10),'Q2':mapped('Q2','tt1',20)}},'stale':False}
        result=await self.api.page(['tt1','tt2','tt3'])
        self.assertEqual([row['status'] for row in result['outcomes']],['conflict','unresolved','unmapped'])
        self.sources.igdb.assert_not_awaited()

    async def test_edition_and_port_identity_are_preserved_instead_of_binding_the_base_title(self):
        self.response([('tt1','Q1'),('tt2','Q2')])
        self.wiki.entities.return_value={'data':{'entities':{'Q1':mapped('Q1','tt1',10),'Q2':mapped('Q2','tt2',20)}},'stale':False}
        self.sources.igdb.return_value=[raw(10,version_parent=30),raw(20,11,parent_game={'id':30})]
        result=await self.api.page(['tt1','tt2'])
        self.assertEqual([row['status'] for row in result['outcomes']],['unresolved','unresolved'])
        self.assertTrue(all('document' not in row for row in result['outcomes']))

    async def test_unknown_or_unverified_parent_never_certifies_an_eligible_expansion(self):
        self.response([('tt1','Q1')]);self.wiki.entities.return_value={'data':{'entities':{'Q1':mapped('Q1','tt1',10)}},'stale':False}
        self.sources.igdb.side_effect=[[raw(10,2,parent_game={'id':20})],[raw(20,1)]]
        result=await self.api.page(['tt1'])
        self.assertEqual(result['outcomes'][0]['reason'],'expansion_parent')

    async def test_provider_failures_are_not_cached_as_no_mapping(self):
        self.http.request.side_effect=ProviderError('unavailable')
        for _ in range(2):
            with self.assertRaisesRegex(ProviderError,'unavailable'):await self.api.page(['tt1'])
        self.assertEqual(self.http.request.await_count,4);self.wiki.entities.assert_not_awaited()

    async def test_redirected_candidates_share_one_verified_item_and_deprecated_claims_do_not_bind(self):
        self.response([('tt1','Q1'),('tt1','Q2')])
        target=mapped('Q2','tt1',10)
        self.wiki.entities.return_value={'data':{'entities':{'Q1':target,'Q2':target}},'stale':False}
        self.sources.igdb.return_value=[raw(10)]
        result=await self.api.page(['tt1'])
        self.assertEqual(result['outcomes'][0]['status'],'verified')
        target['claims']['P345']=[claim('tt1',rank='deprecated')]
        result=await self.api.page(['tt1'])
        self.assertEqual(result['outcomes'][0]['status'],'conflict')

    async def test_invalid_and_truncated_reverse_results_fail_before_metadata_fanout(self):
        for ids in ([],['nm1'],['tt1']*2,[{}],['tt1']*21):
            with self.assertRaisesRegex(ProviderError,'invalid_request'):await self.api.page(ids)
        self.response([('tt2','Q1')])
        with self.assertRaisesRegex(ProviderError,'bad_response'):await self.api.page(['tt1'])
        self.wiki.entities.assert_not_awaited()

    def test_rank_conflicts_and_slug_only_references_do_not_use_names(self):
        self.assertEqual(game_reference(item('Q1',claims={'P5794':[claim('game')]})),{'slug':'game'})
        self.assertIsNone(game_reference(mapped('Q1','tt1',10,P9043=[claim('20')])))
        self.assertEqual(game_reference(item('Q1',claims={'P9043':[claim('10'),claim('20',rank='preferred')]})),{'id':20})
