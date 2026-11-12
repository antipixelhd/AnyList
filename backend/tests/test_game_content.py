import os
import unittest

os.environ.setdefault('SECRET_KEY','content-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_content import erotic_game
from core.game_types import search_candidate,candidate_fields
from core.game_browse_sources import card


class GameContentTests(unittest.TestCase):
    def test_erotic_theme_is_distinct_from_mature_ratings_romance_and_nudity(self):
        mature={'id':1,'name':'Mature game','game_type':{'id':0},'age_ratings':[{'rating_category':{'rating':'18'}}],
            'themes':[{'id':44,'name':'Romance'}],'keywords':[{'name':'nudity'}]}
        self.assertFalse(erotic_game(mature));self.assertFalse(search_candidate(mature)['is_erotic'])
        for themes in ([42],[{'id':42,'name':'Erotic'}]):
            erotic={**mature,'themes':themes}
            self.assertTrue(erotic_game(erotic));self.assertTrue(card(erotic)['is_erotic'])

    def test_canonical_parent_supplies_content_classification_and_projection(self):
        original={'id':1,'name':'Original','game_type':{'id':0},'themes':[42]}
        edition={'id':2,'version_parent':original}
        port={'id':3,'game_type':{'id':11},'parent_game':original}
        self.assertTrue(search_candidate(edition)['is_erotic'])
        self.assertTrue(search_candidate(port)['is_erotic'])
        fields=candidate_fields('id,name,game_type.type').split(',')
        for field in ('themes','version_parent.themes','parent_game.themes','version_parent.parent_game.themes'):
            self.assertIn(field,fields)
