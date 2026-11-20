import os
import unittest

os.environ.setdefault('SECRET_KEY','person-fixture-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')

from core.game_imdb_person import profile,game_rows


class PersonProjectionTests(unittest.TestCase):
    def test_unknown_and_partial_calendar_dates_are_not_invented(self):
        self.assertEqual(profile({'birthDate':{'dateComponents':{'year':1980}}}),{})
        self.assertEqual(profile({'birthDate':{'dateComponents':{'year':1980,'month':2,'day':31}}}),{})
        self.assertEqual(profile({'birthDate':{'dateComponents':{'year':1980,'month':2,'day':3}}}),{'birthday':'1980-02-03'})

    def test_denied_biography_is_unavailable_and_professions_remain_separate(self):
        raw={'bio':{'text':{'plainText':'Biography'}},'primaryProfessions':[{'category':{'text':'Actor'}}]}
        self.assertEqual(profile(raw),{'biography':'Biography','known_for_department':'Actor'})
        raw['unavailable_fields']=['Name.bio']
        self.assertEqual(profile(raw),{'known_for_department':'Actor'})

    def test_explicit_types_and_roles_preserve_editions_without_title_matching(self):
        def row(native,role='actor',kind='videoGame'):
            return {'title':{'id':native,'titleText':{'text':'Same name'},'titleType':{'id':kind}},'category':{'id':role}}
        games,omitted=game_rows([row('tt1'),row('tt1'),row('tt2'),row('tt3',kind='movie'),row('tt1','writer')])
        self.assertEqual([(row['title_id'],row['category']) for row in games],[('tt1','actor'),('tt2','actor'),('tt1','writer')])
        self.assertEqual(omitted,0)
        self.assertEqual(game_rows([{'title':None},row('bad'),row('tt1',role='bad role')])[1],3)
