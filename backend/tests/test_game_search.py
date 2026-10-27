import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY','games-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')

from core.catalogue_providers import CatalogueProviders
from core.game_search import broader_query, matches_partial


class PartialGameSearchTests(unittest.IsolatedAsyncioTestCase):
    def test_partial_words_are_candidate_filters_and_quoted_queries_are_not_broadened(self):
        self.assertEqual(broader_query('Elden R'),'Elden')
        self.assertEqual(broader_query('  Deep Rock G  '),'Deep Rock')
        for query in ('Eld','X R','Elden_R','Elden "R"','x"; fields *;'):
            self.assertIsNone(broader_query(query))
        self.assertTrue(matches_partial('eLDeN R','Elden Ring: Shadow of the Erdtree'))
        self.assertFalse(matches_partial('Elden R','Elder Scrolls'))
        self.assertFalse(matches_partial('Elden R','Elden Bling'))

    def test_normalized_and_fuzzy_candidate_discovery_keeps_all_query_tokens(self):
        for query,title in [('baldurs',"Baldur’s Gate"),('BALDURS G',"Baldur's Gate"),
                            ('pokémon','Pokemon'),('Baldru',"Baldur's Gate"),('Eldne R','Elden Ring')]:
            self.assertTrue(matches_partial(query,title),(query,title))
        self.assertFalse(matches_partial('baldurs racing',"Baldur's Gate"))
        self.assertFalse(matches_partial('Elden R','Elder Scrolls'))
        self.assertEqual(broader_query('baldurs'),'bal')
        self.assertEqual(broader_query('Baldurs G'),'bal')

    async def test_single_word_recovery_is_bounded_and_prefers_exact_titles(self):
        api=CatalogueProviders()
        api.igdb=AsyncMock(side_effect=[[],[
            {'id':3,'name':'Baldurs Gate 3','game_type':{'id':0}},
            {'id':1,'name':'Baldurs','game_type':{'id':0}},
            {'id':2,'name':'Ball Racing','game_type':{'id':0}}]])
        with patch.object(api,'validate'):
            result=await api.search('igdb','game','baldurs')
        self.assertEqual([row['external_id'] for row in result],['1','3'])
        self.assertEqual(api.igdb.await_count,2)

    async def test_quick_search_recovers_matching_canonical_candidates_only(self):
        api=CatalogueProviders()
        base={'id':119133,'name':'Elden Ring','game_type':{'id':0}}
        api.igdb=AsyncMock(side_effect=[[],[base,{'id':999,'version_parent':base},
            {'id':240009,'name':'Elden Ring: Shadow of the Erdtree','game_type':{'id':2},'parent_game':base},
            {'id':4,'name':'Elder Scrolls','game_type':{'id':0}},
            {'id':5,'name':'Elden Ring Weapon Pack','game_type':{'id':1}}]])
        with patch.object(api,'validate'):
            result=await api.search('igdb','game','Elden R')
        self.assertEqual([row['external_id'] for row in result],['119133','240009'])
        self.assertEqual(api.igdb.await_count,2)
        self.assertTrue(api.igdb.await_args_list[1].args[1].startswith('search "Elden";'))

    async def test_successful_or_later_quick_search_pages_do_not_add_broader_requests(self):
        api=CatalogueProviders();api.igdb=AsyncMock(return_value=[{'id':119133,'name':'Elden Ring','game_type':{'id':0}}])
        with patch.object(api,'validate'):
            self.assertEqual(len(await api.search('igdb','game','Elden R')),1)
            api.igdb.return_value=[]
            self.assertEqual(await api.search('igdb','game','Elden R',page=2),[])
        self.assertEqual(api.igdb.await_count,2)
