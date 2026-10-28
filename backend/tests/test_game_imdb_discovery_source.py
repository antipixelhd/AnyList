"""Explicit mappings and calibrated title/year fallback preserve work scope."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

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
    async def test_unique_complete_edition_automatically_projects_its_independent_parent(self):
        from test_game_title_match import game
        self.response([])
        title={'id':'tt1','title':'Final Fantasy XVI: The Complete Edition','year':2024}
        child=game(318557,'Final Fantasy XVI: Complete Edition',year=2024,game_type={'id':3},version_parent=31551)
        parent=game(31551,'Final Fantasy XVI',year=2023)
        acquired={'data':{'rows':[child],'complete':True,'branches':[]},'stale':False}
        self.sources.igdb.return_value=[parent]
        with patch('core.game_title_candidates.retrieve',AsyncMock(return_value=acquired)), \
             patch('core.game_match_support.corroborate',AsyncMock(side_effect=AssertionError('Unique editions need no cast fan-out'))):
            value=(await self.api.page(['tt1'],titles={'tt1':title}))['outcomes'][0]
        self.assertEqual((value['status'],value['igdb_id'],value['credit_parent']),('verified',31551,True))
        self.assertEqual(value['document']['work']['name'],'Final Fantasy XVI')
        self.assertEqual(value['evidence']['variant']['child']['id'],318557)
        self.assertIn('igdb:318557',value['evidence']['dependencies'])
        self.assertIn('igdb:31551',value['evidence']['dependencies'])
        self.assertEqual(value['evidence']['scope'],'parent_participation')

    async def test_edition_parent_must_be_current_independent_and_not_another_edition(self):
        from test_game_title_match import game
        for extra in ({'game_type':{'id':3}},{'version_parent':99},{'first_release_date':None},
                {'first_release_date':game(year=2025)['first_release_date']}):
            self.response([])
            title={'id':'tt1','title':'Example: Complete Edition','year':2024}
            child=game(20,title['title'],year=2024,version_parent=10)
            self.sources.igdb.return_value=[game(10,'Example',year=2020,**extra)]
            with patch('core.game_title_candidates.retrieve',AsyncMock(return_value={
                    'data':{'rows':[child],'complete':True,'branches':[]},'stale':False})):
                value=(await self.api.page(['tt1'],titles={'tt1':title}))['outcomes'][0]
            self.assertEqual(value['reason'],'variant_parent_scope')

    async def test_unique_port_uses_parent_participation_instead_of_a_primary_imdb_binding(self):
        from test_game_title_match import game
        self.response([])
        title={'id':'tt1','title':'Example Portable','year':2024}
        child=game(20,title['title'],year=2024,game_type={'id':11},parent_game={'id':10})
        self.sources.igdb.return_value=[game(10,'Example',year=2020)]
        with patch('core.game_title_candidates.retrieve',AsyncMock(return_value={
                'data':{'rows':[child],'complete':True,'branches':[]},'stale':False})):
            value=(await self.api.page(['tt1'],titles={'tt1':title}))['outcomes'][0]
        self.assertEqual((value['status'],value['igdb_id'],value['credit_parent']),('verified',10,True))
        self.assertEqual(value['evidence']['variant']['child']['game_type']['id'],11)

    async def test_candidate_acquisition_cannot_spend_the_bounded_deep_verification_allowance(self):
        from test_game_title_match import game
        for failure in (False,True):
            with self.subTest(failure=failure):
                self.response([]);clock=[0]
                async def retrieve(sources,title):
                    clock[0]+=20
                    return {'data':{'rows':[game(int(title['id'][2:]),title['title'],year=2018)],
                        'complete':True,'branches':[]},'stale':False}
                async def support(*args):
                    clock[0]+=4
                    if failure:raise ProviderError('unavailable')
                    return {},[]
                with patch('core.game_imdb_discovery_source.monotonic',side_effect=lambda:clock[0]), \
                     patch('core.game_title_candidates.retrieve',AsyncMock(side_effect=retrieve)), \
                     patch('core.game_match_support.corroborate',AsyncMock(side_effect=support)) as deeper:
                    result=await self.api.page(['tt1','tt2','tt3'],titles={f'tt{i}':{
                        'id':f'tt{i}','title':f'Example {i}','year':2017} for i in range(1,4)})
                self.assertEqual(deeper.await_count,2)
                self.assertEqual([row['reason'] for row in result['outcomes']],
                    ['unavailable' if failure else 'title_year_evidence']*2+['corroboration_deferred'])
                self.assertEqual(result['outcomes'][2]['retry_after'],2)

    def test_version_scope_requires_a_known_later_version_and_matching_parent_name(self):
        from core.game_version_scope import original_parent
        from test_game_title_match import game
        title={'id':'tt1','title':'Original','year':2000}
        later=game(20,'Original HD',year=2015,game_type={'id':9},
            parent_game={'id':10,'name':'Original','game_type':{'id':0}})
        self.assertEqual(original_parent(title,later),10)
        self.assertEqual(original_parent({**title,'title':'Localized','aliases':['Original']},later),10)
        for extra in ({'version_parent':10},{'game_type':{'id':1}},{'game_type':{'id':0}},
                {'first_release_date':None},{'parent_game':None},
                {'parent_game':{**later['parent_game'],'name':'Other'}},
                {'parent_game':{**later['parent_game'],'game_type':{'id':11}}}):
            self.assertIsNone(original_parent(title,{**later,**extra}))
        self.assertIsNone(original_parent({**title,'year':2015},later))
        self.assertIsNone(original_parent({**title,'year':None},later))
        self.assertIsNone(original_parent({**title,'title':'Original Complete Edition'},later))

    async def test_original_scope_survives_a_wikidata_reference_to_its_later_remaster(self):
        from test_game_title_match import game
        self.response([('tt1','Q1')])
        self.wiki.entities.return_value={'data':{'entities':{'Q1':mapped('Q1','tt1',20)}},'stale':False}
        base=game(10,'Grandia II',year=2000)
        remaster=game(20,'Grandia II HD Remaster',year=2015,game_type={'id':9},
            parent_game={'id':10,'name':'Grandia II','game_type':{'id':0}})
        self.sources.igdb.side_effect=[[remaster],[base,remaster],[]]
        result=await self.api.page(['tt1'],titles={'tt1':{'id':'tt1','title':'Grandia II','year':2000}})
        value=result['outcomes'][0]
        self.assertEqual((value['status'],value['igdb_id'],value['binding_source']),('verified',10,'matched'))
        self.assertEqual(value['evidence']['version_reference']['game']['id'],20)
        self.assertEqual(value['evidence']['source_outcome']['reason'],'cross_version_reference')
        self.assertIn('igdb:20',value['evidence']['dependencies'])

    async def test_original_scope_cannot_bypass_ambiguity_or_a_different_parent(self):
        from test_game_title_match import game
        for state in ('ambiguous','wrong_parent','missing_version'):
            with self.subTest(state=state):
                self.response([('tt1','Q1')]);self.sources.cache.rows.clear()
                self.wiki.entities.return_value={'data':{'entities':{'Q1':mapped('Q1','tt1',20)}},'stale':False}
                base=game(30 if state=='wrong_parent' else 10,'Example',year=2000)
                remaster=game(20,'Example Remastered',year=2015,game_type={'id':9},
                    parent_game={'id':10,'name':'Example','game_type':{'id':0}})
                pool=[base]+([] if state=='missing_version' else [remaster])+([game(40,'Example',year=2000)] if state=='ambiguous' else [])
                self.sources.igdb.side_effect=[[remaster],pool,[]]
                with patch('core.game_match_support.corroborate',AsyncMock(return_value=({},[]))):
                    value=(await self.api.page(['tt1'],titles={'tt1':{'id':'tt1','title':'Example','year':2000}}))['outcomes'][0]
                self.assertNotEqual(value['status'],'verified')

    async def test_optional_deep_timeout_keeps_an_ordinary_verified_title_in_the_same_batch(self):
        self.response([])
        self.sources.igdb.side_effect=[[raw(10,name='Fast Game',first_release_date=1506038400)],[],
            [raw(20,name='Gap Game')],[]]
        with patch('core.game_match_support.corroborate',AsyncMock(side_effect=TimeoutError)):
            result=await self.api.page(['tt1','tt2'],titles={'tt1':{'id':'tt1','title':'Fast Game','year':2017},
                'tt2':{'id':'tt2','title':'Gap Game','year':None}})
        self.assertEqual(result['outcomes'][0]['status'],'verified')
        self.assertEqual(result['outcomes'][1]['status'],'unresolved')
        self.assertEqual(result['outcomes'][1]['retry_after'],60)

    async def test_complete_title_year_fallback_accepts_primary_name_and_retains_provenance(self):
        self.response([])
        self.sources.igdb.side_effect=[[raw(10,name='Spacelords',first_release_date=1506038400)],[]]
        result=await self.api.page(['tt1'],titles={'tt1':{'id':'tt1','title':'Spacelords','year':2017}})
        outcome=result['outcomes'][0]
        self.assertEqual((outcome['status'],outcome['igdb_id'],outcome['binding_source']),('verified',10,'matched'))
        self.assertTrue(outcome['evidence']['retrieval_complete'])
        self.assertEqual(outcome['evidence']['source_outcome']['status'],'unmapped')
        self.assertTrue({'imdb:tt1','igdb:10','wikidata-imdb:tt1','igdb-name:spacelords'}.issubset(outcome['evidence']['dependencies']))

    async def test_title_formats_are_used_by_automatic_discovery_without_manual_or_deep_support(self):
        for title,year,provider,stamp,discovery in [
            ('Pryzm, Chapter 1: The Dark Unicorn',2002,'Pryzm Chapter One: The Dark Unicorn',1023667200,[]),
            ('The Clue Finders: The Incredible Toy Store Adventure!',2001,'ClueFinders: The Incredible Toy Store Adventure',1001808000,[[raw(99,name='Clue')]])]:
            with self.subTest(title=title):
                self.response([]);self.sources.cache.rows.clear()
                self.sources.igdb.side_effect=[[],[],[],[],*discovery,[raw(10,name=provider,first_release_date=stamp)]]
                with patch('core.game_match_support.corroborate',AsyncMock()) as deeper:
                    result=await self.api.page(['tt1'],titles={'tt1':{'id':'tt1','title':title,'year':year}})
                outcome=result['outcomes'][0]
                self.assertEqual((outcome['status'],outcome['igdb_id'],outcome['binding_source']),('verified',10,'matched'))
                self.assertTrue(next(r for r in outcome['evidence']['candidates'] if r['id']==10).get('title_format'))
                self.assertFalse(outcome['evidence']['support']['snapshots'])
                deeper.assert_not_awaited()

    async def test_wikidata_outage_does_not_prevent_fresh_title_evidence(self):
        self.http.request.side_effect=ProviderError('unavailable')
        self.sources.igdb.side_effect=[[raw(10,name='Spacelords',first_release_date=1506038400)],[]]
        result=await self.api.page(['tt1'],titles={'tt1':{'id':'tt1','title':'Spacelords','year':2017}})
        self.assertEqual(result['outcomes'][0]['status'],'verified')
        self.assertFalse(result['stale'])

    async def test_dlc_without_confirmed_parent_remains_unresolved(self):
        self.response([])
        self.sources.igdb.side_effect=[[raw(10,1,name='Example DLC',first_release_date=1506038400)],[]]
        result=await self.api.page(['tt1'],titles={'tt1':{'id':'tt1','title':'Example DLC','year':2017}})
        self.assertEqual((result['outcomes'][0]['status'],result['outcomes'][0]['reason']),('unresolved','credit_parent'))

    async def test_confirmed_dlc_and_updates_preserve_child_identity_and_full_parent(self):
        for kind in (1,14,6):
            with self.subTest(kind=kind):
                self.response([]);self.sources.cache.rows.clear()
                self.sources.igdb.side_effect=[[raw(10,kind,name='Example DLC',first_release_date=1506038400,
                    parent_game={'id':20,'game_type':{'id':0}})],[],[raw(20,name='Main game')]]
                result=await self.api.page(['tt1'],titles={'tt1':{'id':'tt1','title':'Example DLC','year':2017}})
                row=result['outcomes'][0]
                self.assertEqual((row['status'],row['igdb_id']),('verified',10))
                edge=row['document']['relationships'][0]
                self.assertEqual(edge['target']['name'],'Main game')
                self.assertEqual(edge['relation'],{1:'dlc',14:'update',6:'episode'}[kind])

    async def test_title_fallback_cannot_override_an_incompatible_explicit_imdb_identity(self):
        self.response([('tt1','Q1')])
        self.wiki.entities.return_value={'data':{'entities':{'Q1':mapped('Q1','tt999',10)}},'stale':False}
        self.sources.igdb.return_value=[raw(10,name='Spacelords',first_release_date=1506038400)]
        result=await self.api.page(['tt1'],titles={'tt1':{'id':'tt1','title':'Spacelords','year':2017}})
        self.assertEqual(result['outcomes'][0]['reason'],'explicit_identity_conflict')
        self.sources.igdb.assert_awaited_once()
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

    async def test_verified_expansion_fetches_parent_and_orphan_dlc_remains_unresolved(self):
        self.response([('tt1','Q1'),('tt2','Q2')])
        self.wiki.entities.return_value={'data':{'entities':{'Q1':mapped('Q1','tt1',10),'Q2':mapped('Q2','tt2',20)}},'stale':False}
        self.sources.igdb.side_effect=[[raw(10,2,parent_game={'id':30}),raw(20,1)], [raw(30)]]
        result=await self.api.page(['tt1','tt2']);expansion,dlc=result['outcomes']
        self.assertEqual((expansion['status'],dlc['status'],dlc['reason']),('verified','unresolved','credit_parent'))
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
