"""Matching must reject plausible-but-incomplete and wrong-edition evidence."""
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY', 'mapping-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')
from core.game_title_match import decide, spelling,title_formats,valid_binding_evidence,VERSION
from core.game_title_candidates import retrieve, PAGE, MAX_PAGES
from core.catalogue_providers import ProviderError


def game(native=10, name='Spacelords', year=2017, **extra):
    return {'id': native, 'name': name, 'first_release_date': int(datetime(year, 9, 22, tzinfo=timezone.utc).timestamp()),
            'game_type': {'id': 0, 'type': 'Main Game'}, **extra}


class TitleDecisionTests(unittest.TestCase):
    title = {'id': 'tt1', 'title': 'Spacelords', 'year': 2017}

    def decide(self, rows, **kwargs):
        return decide(self.title, rows, complete=kwargs.get('complete', True), branches=[])

    def test_annual_sports_suffix_must_agree_with_source_and_first_release_year(self):
        title={'id':'tt1','title':'Backyard Basketball','year':2001}
        raw=game(50297,'Backyard Basketball 2001',year=2001,genres=[{'name':'Sport'}])
        result=decide(title,[raw,game(20,title['title'],year=2003)],complete=True,branches=[])
        self.assertEqual((result['status'],result['igdb_id']),('verified',50297))
        self.assertEqual(next(row for row in result['evidence']['candidates'] if row['id']==50297)['annual_title_scope']['year'],2001)
        self.assertTrue(valid_binding_evidence({'matcher_version':VERSION,'matching':result['evidence'],'igdb_id':'50297'},'tt1'))
        for version in (6,7):
            self.assertFalse(valid_binding_evidence({'matcher_version':version,
                'matching':{**result['evidence'],'version':version},'igdb_id':'50297'},'tt1'))
        for change in ({'genres':[]},{'name':'Backyard Basketball 2002'}, {'first_release_date':game(year=2000)['first_release_date']},
                {'game_type':{'id':1}},{'game_type':{'id':11}},{'game_type':{'id':8}},
                {'version_parent':50},{'parent_game':{'id':50}},{'name':'Backyard Basketball 2 2001'}):
            with self.subTest(change=change):
                self.assertNotEqual(decide(title,[{**raw,**change}],complete=True,branches=[])['status'],'verified')
        self.assertEqual(decide(title,[raw],complete=False,branches=[])['reason'],'incomplete_candidates')
        self.assertEqual(decide(title,[raw,{**raw,'id':99}],complete=True,branches=[])['reason'],'ambiguous_candidates')
        unknown=game(20,title['title'],first_release_date=None)
        self.assertEqual(decide(title,[raw,unknown],complete=True,branches=[])['reason'],'unknown_competitor')

    def test_unchanged_v6_proofs_remain_supported_but_unknown_or_mixed_versions_do_not(self):
        proof=self.decide([game()])['evidence']
        for version in (6,7,VERSION):
            self.assertTrue(valid_binding_evidence({'matching':{**proof,'version':version},
                'matcher_version':version,'igdb_id':'10'},'tt1'))
        for version in (5,VERSION+1,None):
            self.assertFalse(valid_binding_evidence({'matching':{**proof,'version':version},
                'matcher_version':version,'igdb_id':'10'},'tt1'))
        self.assertFalse(valid_binding_evidence({'matching':proof,'matcher_version':6,'igdb_id':'10'},'tt1'))

    def test_episode_title_requires_confirmed_series_parent_number_and_release(self):
        title={'id':'tt1','title':'Vader Immortal: A Star Wars VR Series - Episode III','year':2019}
        parent={'id':100,'name':'Vader Immortal: A Star Wars VR Series','game_type':{'id':0}}
        episode=game(30,'Vader Immortal: Episode III',year=2019,game_type={'id':6},parent_game=parent)
        result=decide(title,[episode],complete=True,branches=[])
        self.assertEqual(result['igdb_id'],30)
        self.assertTrue(valid_binding_evidence({'matcher_version':VERSION,'igdb_id':'30','matching':result['evidence']},'tt1'))
        for extra in ({'name':'Vader Immortal: Episode II'},{'parent_game':None},
                {'parent_game':{**parent,'name':'Other Series'}},{'game_type':{'id':0}},
                {'first_release_date':None},{'version_parent':100}):
            self.assertNotEqual(decide(title,[{**episode,**extra}],complete=True,branches=[])['status'],'verified')
        self.assertNotEqual(decide(title,[episode],complete=False,branches=[])['status'],'verified')
        self.assertNotEqual(decide(title,[episode,{**episode,'id':31}],complete=True,branches=[])['status'],'verified')
        self.assertNotEqual(decide({**title,'title':title['title']+' Remastered'},[episode],complete=True,branches=[])['status'],'verified')

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

    def test_reviewed_title_format_examples_can_be_matched_without_reviewed_bindings(self):
        examples=[('BladeStorm: Hundred Years War',"Bladestorm: The Hundred Years' War",2007),
            ('Pryzm, Chapter 1: The Dark Unicorn','Pryzm Chapter One: The Dark Unicorn',2002),
            ('The Clue Finders: The Incredible Toy Store Adventure!','ClueFinders: The Incredible Toy Store Adventure',2001),
            ("Ten'gai Makyou: Dai-yon no Mokushiroku: The Apocalypse IV",'Tengai Makyou: Daiyon no Mokushiroku - The Apocalypse IV',1997)]
        for source,name,year in examples:
            with self.subTest(source=source):
                title={'id':'tt1','title':source,'year':year}
                result=decide(title,[game(name=name,year=year)],complete=True,branches=[])
                self.assertEqual(result['igdb_id'],10)
                self.assertEqual(result['evidence']['candidates'][0]['title_format']['version'],1)
                self.assertTrue(valid_binding_evidence({'matcher_version':VERSION,'igdb_id':'10','matching':result['evidence']},'tt1'))

    def test_title_format_rules_generalize_and_preserve_semantic_differences(self):
        for one,two in [('ClueFinders & Friends','Clue Finders and Friends'),('The Outpost: The Return','Outpost: Return'),
                        ('Star-Fall: Episode IV','Starfall: Episode 4'),('Stories: Part Two','Stories: Part 2'),
                        ('Lineage2M','Lineage 2M'),('NBA2K24','NBA 2K24')]:
            with self.subTest(one=one):self.assertTrue(title_formats(one)&title_formats(two))
        for one,two in [('AB C','A BC'),('Game II','Game 2'),('Game+','Game'),('Game: Remastered','Game'),
                        ('Game: Complete Edition','Game'),('Game: Chapter One','Game: Chapter Two'),
                        ('Game: Episode IV','Game: Episode V'),('Main Game: DLC','Main Game'),
                        ('NBA2K24','NBA2K23'),('Game64','Game6 4')]:
            with self.subTest(one=one):self.assertFalse(title_formats(one)&title_formats(two))

    def test_title_format_matching_keeps_year_completeness_and_edition_guards(self):
        title={'id':'tt1','title':'Stories: Chapter 1','year':2002}
        main=game(name='Stories: Chapter One',year=2002)
        for candidate in [game(name=main['name'],year=2003),{**main,'first_release_date':None},
                          {**main,'version_parent':99},{**main,'game_type':None}]:
            self.assertNotEqual(decide(title,[candidate],complete=True,branches=[])['status'],'verified')
        self.assertNotEqual(decide({**title,'year':None},[main],complete=True,branches=[])['status'],'verified')
        self.assertEqual(decide(title,[main],complete=False,branches=[])['reason'],'incomplete_candidates')
        for other in [{**main,'id':20},{**main,'id':20,'first_release_date':None}]:
            for rows in ([main,other],[other,main]):
                self.assertEqual(decide(title,rows,complete=True,branches=[])['status'],'unresolved')

    def test_typographic_port_variants_require_an_explicit_matching_parent(self):
        title={'id':'tt1','title':'Stories: Chapter 1','year':2002}
        main=game(name='Stories: Chapter One',year=2002)
        port=game(20,name='The Stories: Chapter 1',year=2002,game_type={'id':11},parent_game={'id':10})
        result=decide(title,[port,main],complete=True,branches=[])
        self.assertEqual(result['igdb_id'],10)
        self.assertTrue(valid_binding_evidence({'matcher_version':VERSION,'igdb_id':'10','matching':result['evidence']},'tt1'))
        self.assertEqual(decide(title,[main,{**port,'parent_game':{'id':99}}],complete=True,branches=[])['status'],'unresolved')

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
    async def test_annual_sports_year_queries_keep_other_versions_in_the_complete_pool(self):
        old=game(20,'Backyard Basketball',year=2003,genres=[{'name':'Sport'}],alternative_names=[{'name':None}])
        annual=game(10,'Backyard Basketball 2001',year=2001,genres=[{'name':'Sport'}])
        self.sources.igdb.side_effect=[[old],[old],[annual],[annual]]
        title={'id':'tt1','title':'Backyard Basketball','year':2001}
        result=(await retrieve(self.sources,title))['data']
        self.assertEqual(self.sources.igdb.await_count,4)
        self.assertEqual({row['id'] for row in result['rows']},{10,20})
        self.assertTrue(result['complete'])
        self.assertIn('Backyard Basketball 2001',self.sources.igdb.await_args_list[2].args[1])
        self.assertEqual(decide(title,result['rows'],complete=True,branches=[])['igdb_id'],10)
    async def test_numbered_episode_search_is_scoped_to_the_full_parent_title(self):
        child=game(30,'Vader Immortal: Episode III',year=2019,game_type={'id':6},
            parent_game={'id':100,'name':'Vader Immortal: A Star Wars VR Series','game_type':{'id':0}})
        self.sources.igdb.side_effect=[[],[],[child],[]]
        title={'id':'tt1','title':'Vader Immortal: A Star Wars VR Series - Episode III','year':2019}
        data=(await retrieve(self.sources,title))['data']
        self.assertTrue(data['complete'])
        self.assertEqual(decide(title,data['rows'],complete=data['complete'],branches=data['branches'])['igdb_id'],30)
        self.assertIn('parent_game.name ~ "Vader Immortal: A Star Wars VR Series"',self.sources.igdb.await_args_list[2].args[1])
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

    async def test_empty_full_title_uses_one_keyword_but_not_as_identity_evidence(self):
        title={'id':'tt2','title':'The Clue Finders','year':2017}
        self.sources.igdb.side_effect=[[],[],[game(30,'ClueFinders',year=2018)]]
        data=(await retrieve(self.sources,title))['data']
        self.assertEqual(self.sources.igdb.await_count,3)
        self.assertIn('search "Clue";',self.sources.igdb.await_args_list[-1].args[1])
        self.assertEqual(data['branches'][-1]['kind'],'keyword')
        self.assertTrue(data['complete'])
        self.assertNotEqual(decide(title,data['rows'],complete=True,branches=data['branches'])['status'],'verified')

    async def test_truncated_keyword_pool_cannot_turn_a_format_match_into_a_unique_identity(self):
        title={'id':'tt2','title':'The Clue Finders','year':2017}
        rows=[game(30,'ClueFinders'),*[game(i,'Unrelated') for i in range(100,100+PAGE-1)]]
        self.sources.igdb.side_effect=[[],[],*[rows for _ in range(MAX_PAGES)]]
        data=(await retrieve(self.sources,title))['data']
        self.assertEqual(self.sources.igdb.await_count,2+MAX_PAGES)
        self.assertFalse(data['complete'])
        self.assertEqual(decide(title,data['rows'],complete=data['complete'],branches=data['branches'])['reason'],'incomplete_candidates')

    async def test_joined_written_words_are_retrieved_after_an_unrelated_keyword_pool(self):
        title={'id':'tt2','title':'The Clue Finders','year':2017}
        self.sources.igdb.side_effect=[[],[],[game(40,'Clue')],[game(30,'ClueFinders')]]
        data=(await retrieve(self.sources,title))['data']
        self.assertEqual(self.sources.igdb.await_count,4)
        self.assertIn('search "ClueFinders";',self.sources.igdb.await_args_list[-1].args[1])
        self.assertEqual(data['branches'][-1]['kind'],'joined_keyword')
        self.assertEqual(decide(title,data['rows'],complete=data['complete'],branches=data['branches'])['igdb_id'],30)

    async def test_nonempty_format_match_still_retrieves_competing_family_versions(self):
        title={'id':'tt2','title':'The Clue Finders','year':2017}
        main=game(30,'ClueFinders');other=game(40,'Clue Finders')
        self.sources.igdb.side_effect=[[main],[],[other]]
        data=(await retrieve(self.sources,title))['data']
        self.assertEqual(self.sources.igdb.await_count,3)
        self.assertEqual(decide(title,data['rows'],complete=data['complete'],branches=data['branches'])['status'],'unresolved')

    async def test_empty_joined_digit_title_can_discover_its_written_word_family(self):
        title={'id':'tt2','title':'Lineage2M','year':2017}
        self.sources.igdb.side_effect=[[],[],[game(30,'Lineage 2M')]]
        data=(await retrieve(self.sources,title))['data']
        self.assertIn('search "Lineage";',self.sources.igdb.await_args_list[-1].args[1])
        self.assertEqual(decide(title,data['rows'],complete=data['complete'],branches=data['branches'])['igdb_id'],30)

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
