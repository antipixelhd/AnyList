"""Phone platforms are excluded without confusing mixed releases or handhelds."""
import os
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace

os.environ.setdefault('SECRET_KEY','mobile-policy-test')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')

from core.game_types import mobile_only, capabilities, canonical_game, game_platforms, search_candidate
from core.catalogue_normalize import normalize_igdb
from core.game_title_match import decide
from core.game_match_support import corroborate


def game(native,platforms,**extra):
    return {'id':native,'name':'Same title','game_type':{'id':0},'platforms':platforms,
        'first_release_date':1577836800,**extra}


class MobilePolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_known_mobile_platforms_unknown_evidence_and_dedicated_handhelds(self):
        for native in (34,39,42,44,55,73,74,405,417):
            raw=game(1,[{'id':native}])
            self.assertTrue(mobile_only(raw))
            self.assertIsNone(search_candidate(raw))
            attrs=normalize_igdb(raw)['work']['attributes']
            self.assertTrue(attrs['mobile_only'])
            self.assertEqual(attrs['platform_evidence'],raw['platforms'])
            self.assertFalse(capabilities(attrs).independent)
        for platforms in ([],[{'id':6}],[{'id':130}],[{'id':38}],[{'id':46}], [{'id':99999}]):
            self.assertFalse(mobile_only(game(1,platforms)))
        self.assertTrue(mobile_only({'platforms':[{'id':'igdb.platform:34'}]}))
        unknown=normalize_igdb(game(1,[]))['work']['attributes']
        self.assertNotIn('mobile_only',unknown)
        self.assertNotIn('platform_evidence',unknown)

    def test_mixed_release_keeps_original_and_phone_port_never_adds_a_mobile_tag(self):
        original=game(1,[{'id':12,'name':'Xbox 360'}],ports=[{
            'id':2,'game_type':{'id':11},'platforms':[{'id':55,'name':'Legacy Mobile Device'}]}])
        self.assertEqual(game_platforms(original),original['platforms'])
        self.assertEqual(canonical_game(game(2,[{'id':55}],game_type={'id':11},parent_game=original))['id'],1)
        mixed=game(3,[{'id':6},{'id':34}])
        self.assertIsNotNone(search_candidate(mixed))
        self.assertFalse(normalize_igdb(mixed)['work']['attributes']['mobile_only'])

    def test_mobile_competitor_does_not_block_same_name_desktop_identity(self):
        title={'id':'tt1','title':'Same title','year':2020}
        result=decide(title,[game(1,[{'id':6}]),game(2,[{'id':55}])],complete=True,branches=[])
        self.assertEqual(result['status'],'verified')
        self.assertEqual(result['igdb_id'],1)
        self.assertEqual(len(result['evidence']['candidates']),1)

    async def test_mobile_only_candidates_do_not_trigger_expensive_support(self):
        source=SimpleNamespace(igdb=AsyncMock(side_effect=AssertionError('provider fan-out')))
        self.assertEqual(await corroborate(source,{'id':'tt1','title':'Same title'},[game(2,[{'id':55}])]),({},[]))

    def test_mobile_related_cards_are_not_published(self):
        raw=game(1,[{'id':6}],similar_games=[game(2,[{'id':34}]),game(3,[{'id':130}])])
        targets=[r['target']['identities'][0]['external_id'] for r in normalize_igdb(raw)['relationships']]
        self.assertNotIn('2',targets)
        self.assertIn('3',targets)
