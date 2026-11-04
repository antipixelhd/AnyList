"""Compact headers never establish a verified IMDb identity."""
import unittest
import os
os.environ.setdefault('SECRET_KEY','bulk-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_bulk_source import person_candidates,eligible,studio_root,header_query


def game(native,name='Title',kind=0,platforms=None,**extra):
    return {'id':native,'name':name,'game_type':kind,'platforms':[6] if platforms is None else platforms,
        'first_release_date':1577836800,**extra}


class BulkSourceTests(unittest.TestCase):
    def test_character_pending_survives_the_public_work_schema_without_warm_payload_noise(self):
        from schemas_contributors import ContributorWork
        pending=ContributorWork(key='game:1',type='game',title='Game',characters_pending=True)
        self.assertTrue(pending.model_dump()['characters_pending'])
        ready=ContributorWork(key='game:1',type='game',title='Game')
        self.assertNotIn('characters_pending',ready.model_dump())

    def test_explicit_dubbing_jobs_are_acting_but_other_miscellaneous_jobs_are_not(self):
        from core.game_credit_roles import source_role
        from core.game_imdb_person import game_rows
        from core.game_imdb_review import title_index
        base={'category':{'id':'miscellaneous'},'title':{'id':'tt1','titleType':{'id':'videoGame'},
            'titleText':{'text':'Game'},'releaseYear':{'year':2024}}}
        row={**base,'jobs':[{'text':'voice actor: Goro Majima'}]}
        self.assertEqual(source_role(row),'voice_actor')
        games,omitted=game_rows([row]);self.assertEqual(omitted,0)
        self.assertEqual(games[0]['category'],'voice_actor')
        self.assertEqual(games[0]['source_category'],'miscellaneous')
        self.assertEqual(games[0]['characters'],['Goro Majima'])
        self.assertEqual(title_index([row])[0]['roles'],['voice_actor'])
        from core.game_imdb_projection import credit_document
        doc,_=credit_document('1','tt1',[{**row,'name':{'id':'nm1','nameText':{'text':'Actor'}}}],generation='fresh')
        self.assertEqual(doc['credits'][0]['role'],'voice_actor')
        self.assertEqual(doc['credits'][0]['character_label'],'Goro Majima')
        self.assertEqual(doc['credits'][0]['attributes']['imdb_category']['id'],'miscellaneous')
        for job in ['facial motion capture','voice director','voice actor:']:
            self.assertEqual(source_role({**base,'jobs':[{'text':job}]}),'miscellaneous')

    def test_unknown_year_playable_name_is_only_a_scheduling_hint(self):
        from core.game_bulk_source import promising_sources
        source=[{'imdb_id':'tt1','title':'Title','year':None,'roles':['actor']}]
        self.assertEqual(person_candidates(source,[game(1)],'actor'),[])
        self.assertEqual(promising_sources(source,[game(1)],'actor'),{'tt1'})
        self.assertEqual(promising_sources(source,[game(1,platforms=[34])],'actor'),set())

    def test_unknown_competitor_is_not_a_negative(self):
        source=[{'imdb_id':'tt1','title':'Title','year':2020,'roles':['actor']}]
        self.assertEqual(len(person_candidates(source,[game(1)],'actor')),1)
        self.assertEqual(person_candidates(source,[game(1),game(2,first_release_date=None)],'actor'),[])
        self.assertEqual(person_candidates([{**source[0],'year':None}],[game(1)],'actor'),[])

    def test_mobile_mixed_platforms_and_unknown_are_independent(self):
        self.assertFalse(eligible(game(1,platforms=[34,39]),'actor'))
        self.assertTrue(eligible(game(1,platforms=[34,6]),'actor'))
        self.assertFalse(eligible(game(1,platforms=[]),'actor'))
        self.assertFalse(eligible(game(1,themes=[42]),'actor'))

    def test_actor_children_fold_but_studio_expansions_stay_separate(self):
        base=game(1,'Base')
        child=game(2,'Title',2,parent_game=base)
        source=[{'imdb_id':'tt1','title':'Title','year':2020,'roles':['actor','director']}]
        self.assertEqual(person_candidates(source,[child],'actor')[0]['game']['id'],1)
        self.assertEqual(person_candidates(source,[child],'staff')[0]['game']['id'],2)
        self.assertEqual(studio_root(child)['id'],2)
        self.assertFalse(eligible(game(3,kind=2),'studio'))

    def test_roles_and_versions_are_not_guessed(self):
        source=[{'imdb_id':'tt1','title':'Title','year':2020,'roles':['miscellaneous']}]
        self.assertEqual(person_candidates(source,[game(1)],'actor'),[])
        self.assertEqual(len(person_candidates(source,[game(1)],'staff')),1)
        base=game(1,'Base')
        self.assertEqual(studio_root(game(2,kind=11,parent_game=base))['id'],1)
        self.assertIsNone(studio_root(game(2,kind=11)))

    def test_query_variants_keep_the_source_scope_and_page_bound(self):
        query=header_query([{'title':'Star Wars: Battlefront II','year':2017}])
        self.assertIn('Star Wars Battlefront II',query)
        self.assertIn('star-wars-battlefront-2',query)
        self.assertIn('limit 500',query)
        self.assertIn('bladestorm-the-hundred-years-war',header_query([{'title':'BladeStorm: Hundred Years War','year':2007}]))
