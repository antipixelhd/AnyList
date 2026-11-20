import os
import unittest
from types import SimpleNamespace

os.environ.setdefault('SECRET_KEY','game-source-links-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')

from core.media_details import game_source_links


class GameSourceLinksTests(unittest.TestCase):
    def test_merged_black_ops_links_use_main_app_independently_of_identity_order(self):
        work=SimpleNamespace(attributes={'steam_primary_app_id':'202970','igdb_slug':'call-of-duty-black-ops-ii'})
        ids=[SimpleNamespace(namespace='steam.app',external_id=value) for value in ('202990','202970')]
        rows=[{'name':'Steam','url':f'https://store.steampowered.com/app/{value}/'} for value in ('202990','202970')]
        rows.append({'name':'IGDB','url':'https://www.igdb.com/games/multiplayer'})
        expected=[{'name':'Steam','url':'https://store.steampowered.com/app/202970/'},
            {'name':'IGDB','url':'https://www.igdb.com/games/call-of-duty-black-ops-ii'}]
        self.assertEqual(game_source_links(work,rows,ids),expected)
        self.assertEqual(game_source_links(work,list(reversed(rows)),list(reversed(ids))),expected)

    def test_ambiguous_or_invalid_main_app_does_not_fall_back_to_another_component(self):
        ids=[SimpleNamespace(namespace='steam.app',external_id=value) for value in ('202990','202970')]
        for preferred in (None,'999'):
            work=SimpleNamespace(attributes={'steam_primary_app_id':preferred})
            self.assertEqual(game_source_links(work,[{'name':'Steam','url':'https://store.steampowered.com/app/202990/'}],ids),[])
        self.assertEqual(game_source_links(work,[],ids[:1]),
            [{'name':'Steam','url':'https://store.steampowered.com/app/202990/'}])
