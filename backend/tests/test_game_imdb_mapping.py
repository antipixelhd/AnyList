"""Explicit statement identity, edition conflicts and redirect safety."""
import unittest
import os
from copy import deepcopy

os.environ.setdefault('SECRET_KEY','mapping-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')

from core.game_imdb_mapping import game_mapping
from core.game_credit_sources import entity_redirects, verify_game
from core.catalogue_providers import ProviderError
from test_game_credit_sources import claim, game, item, snak


class GameMappingTests(unittest.TestCase):
    def source(self):
        raw = game()
        raw['claims']['P345'] = [claim('tt123')]
        return raw

    def test_qualifier_and_standalone_numeric_ids_are_both_explicit(self):
        raw = self.source()
        self.assertEqual(game_mapping(raw,10,'game')['imdb_id'],'tt123')
        raw['claims']['P9043'] = [claim('10')]
        raw['claims']['P5794'] = []
        self.assertEqual(game_mapping(raw,10,'renamed-slug')['status'],'verified')
        self.assertTrue(verify_game(raw,10,'renamed-slug'))
        raw['claims']['P9043'] = [claim('11')]
        self.assertEqual(game_mapping(raw,10,'game')['status'],'conflict')

    def test_numeric_identity_survives_a_renamed_slug_without_fuzzy_matching(self):
        self.assertEqual(game_mapping(self.source(),10,'different-title')['status'],'verified')
        raw = self.source()
        raw['claims']['P5794'][0]['qualifiers'] = {}
        self.assertEqual(game_mapping(raw,10,'game')['status'],'verified')
        self.assertEqual(game_mapping(raw,10,'Game')['status'],'conflict')

    def test_multiple_games_or_imdb_titles_stay_unresolved(self):
        for prop, additional in [('P9043',claim('11')),('P345',claim('tt456')),
                                 ('P5794',claim('complete-edition',qualifiers={'P9043':[snak('11')]}))]:
            raw = self.source()
            raw['claims'].setdefault(prop,[]).append(additional)
            result = game_mapping(raw,10,'game')
            self.assertEqual(result['status'],'conflict',prop)
            self.assertIsNone(result['imdb_id'])

    def test_best_rank_is_respected_and_deprecated_or_missing_values_are_not_mappings(self):
        raw = self.source()
        raw['claims']['P345'].append(claim('tt456',rank='preferred',id='Q100$preferred'))
        raw['claims']['P345'].append(claim('tt789',rank='deprecated'))
        result = game_mapping(raw,10,'game')
        self.assertEqual(result['imdb_id'],'tt456')
        self.assertEqual(result['evidence']['P345'][0]['id'],'Q100$preferred')
        raw['claims']['P345']=[{'rank':'preferred','mainsnak':{'snaktype':'novalue'}},claim('tt123')]
        self.assertEqual(game_mapping(raw,10,'game')['status'],'unmapped')
        raw['claims']['P9043']=[claim('10',rank='deprecated')]
        raw['claims']['P5794']=[]
        self.assertEqual(game_mapping(raw,10,'game')['status'],'unmapped')

    def test_names_years_and_wrong_identifier_types_never_bind(self):
        raw = item('Q100','Exact title',{'P345':[claim('tt123')]})
        self.assertEqual(game_mapping(raw,10,'exact-title')['status'],'unmapped')
        for native in ['nm123','tt123/','https://www.imdb.com/title/tt123/',True]:
            raw=self.source();raw['claims']['P345']=[claim(native)]
            self.assertEqual(game_mapping(raw,10,'game')['status'],'conflict')

    def test_redirect_chain_is_explicit_bounded_and_cycle_safe(self):
        raw=item('Q300',claims={'P9043':[claim('10')]})
        body={'entities':{'Q300':raw},'redirects':[{'from':'Q100','to':'Q200'},{'from':'Q200','to':'Q300'}]}
        self.assertEqual(entity_redirects(body,['Q100'])['Q100']['id'],'Q300')
        self.assertEqual(entity_redirects(body,['Q400']),{})
        invalid=deepcopy(body);invalid['redirects'].append({'from':'Q300','to':'Q100'})
        with self.assertRaisesRegex(ProviderError,'identity_conflict'):entity_redirects(invalid,['Q100'])
        invalid=deepcopy(body);invalid['redirects'].append({'from':'Q100','to':'Q400'})
        with self.assertRaisesRegex(ProviderError,'identity_conflict'):entity_redirects(invalid,['Q100'])
