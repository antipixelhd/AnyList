import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

os.environ.setdefault('SECRET_KEY','wiki-title-test')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_wikidata_title_support import project,agrees,corroborate,discovery_candidate
from core.game_title_match import decide,valid_binding_evidence,VERSION
from core.catalogue_providers import ProviderError
from test_game_credit_sources import item,claim,snak
from test_game_title_match import game
from test_game_imdb_discovery_source import Cache


class WikiTitleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.title={'id':'tt1','title':'Dragon Ball Z: Super Butoden 2','year':1993}
        self.raw=game(10,'Dragon Ball Z: Super Butouden 2',year=1993,slug='super-butouden-2')
        self.record=item('Q1','Dragon Ball Z: Super Butōden 2',claims={
            'P9043':[claim('10')],
            'P577':[claim({'time':'+1993-00-00T00:00:00Z','precision':9,'before':0,'after':0})]})

    async def test_explicit_igdb_identity_alias_and_year_can_resolve_a_localized_spelling(self):
        proof=project(self.record,self.raw,self.title)
        self.assertTrue(proof)
        result=decide(self.title,[self.raw],complete=True,branches=[],corroboration={10:proof})
        self.assertEqual(result['igdb_id'],10)
        self.assertTrue(valid_binding_evidence({'matcher_version':VERSION,'igdb_id':'10','matching':result['evidence']},'tt1'))
        self.assertEqual(decide(self.title,[self.raw],complete=True,branches=[])['status'],'unmapped')

    async def test_wrong_identity_year_alias_deprecated_date_and_conflicting_imdb_cannot_support(self):
        variants=[{**self.record,'claims':{**self.record['claims'],'P9043':[claim('20')]}},
            {**self.record,'claims':{**self.record['claims'],'P345':[claim('tt2')]}},
            {**self.record,'claims':{**self.record['claims'],'P577':[claim({'time':'+1993-00-00T00:00:00Z','precision':9},rank='deprecated')]}},
            {**self.record,'labels':{'en':{'value':'Another Game'}}},
            {**self.record,'claims':{**self.record['claims'],'P577':[claim({'time':'+1994-00-00T00:00:00Z','precision':9})]}},
            {**self.record,'claims':{**self.record['claims'],'P577':[claim({'time':'+1993-00-00T00:00:00Z','precision':8})]}},
            {**self.record,'claims':{**self.record['claims'],'P577':[claim({'time':'+1993-00-00T00:00:00Z','precision':9,'before':1})]}}]
        for record in variants:
            with self.subTest(record=record):self.assertFalse(project(record,self.raw,self.title))

    async def test_same_year_competitors_and_incomplete_retrieval_stay_unresolved(self):
        proof=project(self.record,self.raw,self.title)
        other={**self.raw,'id':20}
        other_record={**self.record,'id':'Q2','claims':{**self.record['claims'],'P9043':[claim('20')]}}
        evidence={10:proof,20:project(other_record,other,self.title)}
        self.assertEqual(decide(self.title,[self.raw,other],complete=True,branches=[],corroboration=evidence)['reason'],'ambiguous_candidates')
        self.assertEqual(decide(self.title,[self.raw],complete=False,branches=[],corroboration=evidence)['reason'],'incomplete_candidates')

    async def test_preferred_matching_id_does_not_hide_an_active_conflicting_id(self):
        record={**self.record,'claims':{**self.record['claims'],'P345':[claim('tt1',rank='preferred'),claim('tt2')]}}
        self.assertFalse(project(record,self.raw,self.title))
        record['claims']['P345'][1]['rank']='deprecated'
        self.assertTrue(project(record,self.raw,self.title))
        record['claims']['P345']=[{'rank':'normal','mainsnak':{'snaktype':'somevalue'}}]
        self.assertTrue(project(record,self.raw,self.title))

    async def test_fuzzy_discovery_never_accepts_without_verified_alias_evidence(self):
        self.assertTrue(discovery_candidate(self.raw,self.title))
        self.assertFalse(discovery_candidate({**self.raw,'name':'Dragon Ball Z: Super Butouden 3'},self.title))
        proof={'wikidata_title':{'imdb_id':'tt1','igdb_id':10,'item':'Q1','revision':1,'names':['!!!'],'years':[1993]}}
        self.assertFalse(agrees({'id':10,'corroboration':proof},{**self.title,'aliases':['']}))

    async def test_shared_cached_support_and_changed_input_do_not_reuse_a_negative(self):
        sources=SimpleNamespace(cache=Cache())
        wiki=SimpleNamespace(game_item=AsyncMock(return_value={'data':{'item':'Q1'},'stale':False}),
            entities=AsyncMock(return_value={'data':{'entities':{'Q1':self.record}},'stale':False}))
        with patch('core.game_wikidata_title_support.GameCreditSources',return_value=wiki):
            first=await corroborate(sources,self.title,[self.raw])
            self.assertTrue(first[0][10]);self.assertEqual(first[1][0]['kind'],'game_match_wiki')
            self.assertEqual(await corroborate(sources,self.title,[self.raw]),first)
            wiki.entities.assert_awaited_once_with(['Q1'],release_references=True)
            self.assertFalse((await corroborate(sources,{**self.title,'year':1994},[self.raw]))[0])
            self.assertEqual(wiki.entities.await_count,2)

    async def test_many_candidates_do_not_fan_out_or_hide_unknown_competitors(self):
        sources=SimpleNamespace(cache=Cache())
        with patch('core.game_wikidata_title_support.GameCreditSources') as factory:
            self.assertEqual(await corroborate(sources,self.title,[{**self.raw,'id':i} for i in range(1,5)]),({},[]))
            factory.assert_not_called()

    async def test_similar_dlc_titles_cannot_exhaust_the_exact_game_corroboration_slots(self):
        title={'id':'tt1855429','title':'ZEN Pinball','year':2008}
        base=game(20730,'Zen Pinball',year=2009,slug='zen-pinball')
        port=game(260831,'Zen Pinball',year=2011,slug='zen-pinball-mobile',game_type={'id':11},parent_game={'id':20730})
        dlcs=[game(100+i,'Zen Pinball: '+name,year=2009,slug='table-'+str(i),game_type={'id':1})
            for i,name in enumerate(('Pasha','Thor','Mars','X-Men','Rome','Portal','Venom','Blade','Tesla','Shaman','Biolab','Inferno'))]
        record=item('Q3575187','Zen Pinball',claims={'P9043':[claim('20730')],
            'P577':[claim({'time':'+2008-07-07T00:00:00Z','precision':11})]})
        sources=SimpleNamespace(cache=Cache())
        wiki=SimpleNamespace(game_item=AsyncMock(return_value={'data':{'item':record['id']},'stale':False}),
            entities=AsyncMock(return_value={'data':{'entities':{record['id']:record}},'stale':False}))
        with patch('core.game_wikidata_title_support.GameCreditSources',return_value=wiki):
            evidence,snapshots=await corroborate(sources,title,[*dlcs,port,base])
        self.assertEqual(set(evidence),{20730});self.assertEqual(len(snapshots),1)
        wiki.game_item.assert_awaited_once_with(20730,'zen-pinball')
        result=decide(title,[*dlcs,port,base],complete=True,branches=[],corroboration=evidence)
        self.assertEqual((result['status'],result['igdb_id']),('verified',20730))
        self.assertEqual(next(r for r in result['evidence']['candidates'] if r['id']==20730)['years'],[2009])
        self.assertTrue(valid_binding_evidence({'matcher_version':VERSION,'igdb_id':'20730','matching':result['evidence']},title['id']))

    async def test_exact_unknown_competitors_and_excess_exact_candidates_are_not_discarded(self):
        title={'id':'tt1','title':'Example Game','year':2017}
        base=game(10,title['title'],year=2017,slug='example')
        record=item('Q1',title['title'],claims={'P9043':[claim('10')],
            'P577':[claim({'time':'+2017-00-00T00:00:00Z','precision':9})]})
        competitor={**base,'id':20,'first_release_date':None,'slug':'namesake'}
        fuzzy=game(30,'Example Game: Extras',year=2017,slug='extras')
        sources=SimpleNamespace(cache=Cache())
        wiki=SimpleNamespace(game_item=AsyncMock(side_effect=[{'data':{'item':'Q1'},'stale':False},
            {'data':{'item':None},'stale':False}]),
            entities=AsyncMock(return_value={'data':{'entities':{'Q1':record}},'stale':False}))
        with patch('core.game_wikidata_title_support.GameCreditSources',return_value=wiki):
            evidence,_=await corroborate(sources,title,[base,competitor,fuzzy])
            self.assertEqual(wiki.game_item.await_count,2)
            self.assertEqual(decide(title,[base,competitor,fuzzy],complete=True,branches=[],corroboration=evidence)['reason'],'unknown_competitor')
            wiki.game_item.reset_mock()
            self.assertEqual(await corroborate(sources,title,[{**base,'id':i} for i in range(1,5)]+[fuzzy]),({},[]))
            wiki.game_item.assert_not_awaited()

    async def test_optional_conflicting_identity_is_cached_but_outages_are_not_negatives(self):
        sources=SimpleNamespace(cache=Cache())
        wiki=SimpleNamespace(game_item=AsyncMock(side_effect=ProviderError('identity_conflict')),entities=AsyncMock())
        with patch('core.game_wikidata_title_support.GameCreditSources',return_value=wiki):
            result=await corroborate(sources,self.title,[self.raw])
            self.assertFalse(result[0])
            self.assertEqual(await corroborate(sources,self.title,[self.raw]),result)
            wiki.game_item.assert_awaited_once();wiki.entities.assert_not_awaited()
            self.assertEqual(next(iter(sources.cache.rows.values()))['state'],'identity_conflict')
            sources.cache.rows.clear();wiki.game_item.side_effect=ProviderError('unavailable')
            for _ in range(2):
                with self.assertRaisesRegex(ProviderError,'unavailable'):await corroborate(sources,self.title,[self.raw])
            self.assertFalse(sources.cache.rows)
