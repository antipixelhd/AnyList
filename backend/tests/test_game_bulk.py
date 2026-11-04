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
