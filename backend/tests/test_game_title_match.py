"""Matching must reject plausible-but-incomplete and wrong-edition evidence."""
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY', 'mapping-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')
from core.game_title_match import decide, spelling,valid_binding_evidence,VERSION
from core.game_title_candidates import retrieve, PAGE, MAX_PAGES
from core.catalogue_providers import ProviderError


def game(native=10, name='Spacelords', year=2017, **extra):
    return {'id': native, 'name': name, 'first_release_date': int(datetime(year, 9, 22, tzinfo=timezone.utc).timestamp()),
            'game_type': {'id': 0, 'type': 'Main Game'}, **extra}


class TitleDecisionTests(unittest.TestCase):
    title = {'id': 'tt1', 'title': 'Spacelords', 'year': 2017}

    def decide(self, rows, **kwargs):
        return decide(self.title, rows, complete=kwargs.get('complete', True), branches=[])

    def test_exact_primary_ranks_over_a_historical_alias_without_merging_games(self):
        result = self.decide([game(20, 'Raiders of the Broken Planet', alternative_names=[{'name': 'Spacelords'}],
            websites=[{'url':'https://store.steampowered.com/app/436180/'}]),
            game(alternative_names=[{'name':'Raiders of the Broken Planet'}],
                external_games=[{'uid':'436180','external_game_source':{'name':'Steam'}}])])
        self.assertEqual(result['igdb_id'], 10)
        self.assertEqual([row['id'] for row in result['evidence']['candidates']], [10, 20])
        self.assertNotIn('canonical_id', result)

    def test_uncorroborated_alias_and_unknown_competitors_remain_unresolved(self):
        self.assertEqual(self.decide([game(),game(20,'Other',alternative_names=[{'name':'Spacelords'}])])['reason'],'unresolved_alias_scope')
        for extra in ({'first_release_date':None},{'game_type':None}):
            self.assertEqual(self.decide([game(),game(20,**extra)])['status'],'unresolved')

    def test_normalization_preserves_identity_bearing_words_numbers_and_boundaries(self):
        self.assertEqual(spelling('Baldur’s Gáte III'), spelling("Baldur's Gate III"))
        self.assertNotEqual(spelling('Baldurs Gate II'), spelling('Baldurs Gate III'))
        self.assertNotEqual(spelling('AB C'), spelling('A BC'))
        self.assertNotEqual(spelling('Example: Remastered'), spelling('Example'))
        self.assertNotEqual(spelling('Example+'),spelling('Example'))

    def test_incomplete_pool_cannot_turn_one_returned_hit_into_a_unique_mapping(self):
        result = self.decide([game()], complete=False)
        self.assertEqual((result['status'], result['reason']), ('unresolved', 'incomplete_candidates'))

    def test_equal_namesake_candidates_never_use_provider_order_as_a_tiebreak(self):
        for rows in ([game(), game(20)], [game(20), game()]):
            self.assertEqual(self.decide(rows)['reason'], 'ambiguous_candidates')

    def test_explicit_same_title_port_parent_resolves_to_one_main_game(self):
        port=game(20,game_type={'id':11},parent_game={'id':10})
        result=self.decide([port,game()])
        self.assertEqual(result['igdb_id'],10)
        self.assertTrue(valid_binding_evidence({'matcher_version':VERSION,'igdb_id':'10','matching':result['evidence']},'tt1'))
        self.assertEqual(self.decide([game(),{**port,'parent_game':{'id':99}}])['reason'],'ambiguous_candidates')

    def test_different_release_year_namesake_does_not_tie(self):
        self.assertEqual(self.decide([game(20,year=2002),game()])['igdb_id'],10)

    def test_missing_year_and_known_wrong_year_stay_unresolved(self):
        self.assertEqual(self.decide([game(year=2018)])['status'], 'unresolved')
        result = decide({**self.title, 'year': None}, [game()], complete=True, branches=[])
        self.assertEqual(result['status'], 'unresolved')

    def test_platform_release_year_can_corroborate_the_title(self):
        raw = game(year=2018, release_dates=[{'date': 1506038400,'date_format':{'id':0,'format':'YYYYMMDD'}}])
        self.assertEqual(self.decide([raw])['status'], 'verified')

    def test_tbd_placeholder_is_not_release_year_evidence(self):
        raw=game(release_dates=[{'date':1506038400,'date_format':{'id':7,'format':'TBD'}}])
        self.assertEqual(self.decide([raw])['status'],'unresolved')

    def test_fuzzy_similarity_does_not_establish_an_exact_mapping(self):
        self.assertEqual(self.decide([game(name='Spacelord')])['status'], 'unmapped')

    def test_version_parent_is_not_collapsed_into_its_base(self):
        self.assertEqual(self.decide([game(version_parent=100)])['status'], 'unresolved')

    def test_exact_past_launch_disambiguates_a_cancelled_port_with_distinct_parent_and_platform(self):
        main=game(platforms=[{'id':6}])
        prototype=game(20,first_release_date=None,game_type={'id':11},parent_game={'id':100},
            platforms=[{'id':19}],release_dates=[{'status':{'name':'Cancelled'}}])
        scope={'app':'42','developer_agreement':True,'store_title':'Spacelords',
            'release_scope':dict.fromkeys(('imdb_date','store_date','igdb_date'),'2017-09-22')}
        result=decide(self.title,[main,prototype],complete=True,branches=[],corroboration={10:scope})
        self.assertEqual(result['igdb_id'],10)
        self.assertTrue(valid_binding_evidence({'matcher_version':VERSION,'igdb_id':'10','matching':result['evidence']},'tt1'))
        scheduled={**prototype,'release_dates':[{'date':1506038400,'date_format':{'format':'YYYYMMDD'},'status':{'name':'Cancelled'}}]}
        result=decide(self.title,[main,scheduled],complete=True,branches=[],corroboration={10:scope})
        self.assertTrue(next(row for row in result['evidence']['candidates'] if row['id']==20)['year_match'])
        self.assertEqual(result['igdb_id'],10)
        self.assertTrue(valid_binding_evidence({'matcher_version':VERSION,'igdb_id':'10','matching':result['evidence']},'tt1'))
        for extra in ({'platforms':[{'id':6}]},{'parent_game':{'id':10}},{'parent_game':None},
                {'release_dates':[{'status':{'name':'Full Release'}}]},{'game_type':{'id':0}}):
            self.assertEqual(decide(self.title,[main,{**prototype,**extra}],complete=True,branches=[],corroboration={10:scope})['status'],'unresolved')
        wrong={**scope,'release_scope':{**scope['release_scope'],'imdb_date':'2017-09-23'}}
        self.assertEqual(decide(self.title,[main,prototype],complete=True,branches=[],corroboration={10:wrong})['status'],'unresolved')

    def test_parent_scoped_decorated_titles_preserve_identity_and_edition_words(self):
        title={'id':'tt2','title':'Small Town: Alice & Bob in The Long Dark Night','year':2017}
        child=game(30,'Small Town: The Long Dark Night',game_type={'id':1},parent_game={'id':100,'name':'Small Town','game_type':{'id':0}})
        other=game(40,'The Long Dark Night')
        result=decide(title,[other,child],complete=True,branches=[])
        self.assertEqual(result['igdb_id'],30)
        self.assertEqual(result['evidence']['candidates'][0]['parent_title_scope']['parent'],100)
        for raw in ({**child,'parent_game':None},{**child,'parent_game':{'id':200,'name':'Other Town','game_type':{'id':0}}}):
            self.assertNotEqual(decide(title,[other,raw],complete=True,branches=[])['status'],'verified')
        for name in ('Small Town: Remastered and Deluxe in The Long Dark Night','Small Town: Alice and Bob in The Long Dark Night Part II'):
            self.assertNotEqual(decide({**title,'title':name},[child],complete=True,branches=[])['status'],'verified')
        self.assertEqual(decide(title,[child,{**child,'id':31}],complete=True,branches=[])['status'],'unresolved')


class CandidateRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_unicode_names_are_sent_as_real_utf8_not_json_escape_sequences(self):
        self.sources.igdb.return_value=[]
        await retrieve(self.sources,{'id':'tt2','title':'Déraciné','year':2018})
        for call in self.sources.igdb.await_args_list:
            self.assertIn('Déraciné',call.args[1])
            self.assertNotIn('\\u00e9',call.args[1])

    async def asyncSetUp(self):
        class Cache:
            async def get(self, provider, kind, key, loader, **kwargs):
                return {'data': await loader(), 'stale': False}
        self.sources = SimpleNamespace(igdb=AsyncMock(), cache=Cache())
        self.title = {'id': 'tt1', 'title': 'Spacelords', 'year': 2017}

    async def test_all_pages_and_both_query_branches_are_consumed(self):
        self.sources.igdb.side_effect = [[game(i) for i in range(1, PAGE + 1)], [game(200)], []]
        result = (await retrieve(self.sources, self.title))['data']
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['rows']), PAGE + 1)
        self.assertIn('offset 100', self.sources.igdb.await_args_list[1].args[1])
        self.assertIn('alternative_names.name', self.sources.igdb.await_args_list[2].args[1])

    async def test_exhausted_budget_is_explicitly_incomplete(self):
        self.sources.igdb.return_value = [game(i) for i in range(1, PAGE + 1)]
        result = (await retrieve(self.sources, self.title))['data']
        self.assertFalse(result['complete'])
        self.assertEqual(self.sources.igdb.await_count, 2 * MAX_PAGES)

    async def test_fallback_queries_are_bounded_to_the_named_parent_and_consume_both_branches(self):
        title={'id':'tt2','title':'Small Town: Alice and Bob in The Long Dark Night','year':2017}
        child=game(30,'Small Town: The Long Dark Night',game_type={'id':1},parent_game={'id':100,'name':'Small Town','game_type':{'id':0}})
        self.sources.igdb.side_effect=[[],[],[child],[child]]
        data=(await retrieve(self.sources,title))['data']
        self.assertEqual(self.sources.igdb.await_count,4);self.assertTrue(data['complete'])
        for call in self.sources.igdb.await_args_list[2:]:self.assertIn('parent_game.name ~ "Small Town"',call.args[1])
        self.assertEqual(decide(title,data['rows'],complete=data['complete'],branches=data['branches'])['igdb_id'],30)

    async def test_provider_failure_and_oversized_branches_are_not_negatives(self):
        self.sources.igdb.side_effect = ProviderError('timeout')
        with self.assertRaisesRegex(ProviderError, 'timeout'):
            await retrieve(self.sources, self.title)
        with self.assertRaises(ProviderError):
            await retrieve(self.sources, {**self.title, 'aliases': [str(i) for i in range(30)]})
