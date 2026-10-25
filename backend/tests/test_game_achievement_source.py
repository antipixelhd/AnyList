"""Steam catalogue contracts without player credentials or imports."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY','achievement-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_achievement_source import GameAchievementSource,percentages,definitions,icon
from core.catalogue_providers import ProviderError


class AchievementSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_percentages_supply_count_without_a_key_and_preserve_explicit_zero(self):
        http=SimpleNamespace(request=AsyncMock(return_value={'achievementpercentages':{'achievements':[
            {'name':'A0','percent':'74.9'},{'name':'A1','percent':0}]}}))
        source=GameAchievementSource(http=http,key='')
        result=await source.fetch('1245620','globals',None)
        self.assertEqual(result,{'available':True,'total':2,'percentages':{'A0':74.9,'A1':0.0}})
        call=http.request.call_args
        self.assertEqual(call.args,('steam','GET','https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/'))
        self.assertEqual(call.kwargs['params'],{'gameid':'1245620','format':'json'})
        self.assertEqual(await source.fetch('1245620','schema',None),{'available':False,'reason':'not_configured'})
        http.request.assert_awaited_once()

    async def test_schema_uses_only_server_key_and_english_catalogue_not_player_endpoints(self):
        http=SimpleNamespace(request=AsyncMock(return_value={'game':{'gameName':'Game','availableGameStats':{'achievements':[
            {'name':'A0','displayName':'A label','hidden':1,'description':'A description',
                'icon':'http://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps/10/icon.jpg'}]}}}))
        result=await GameAchievementSource(http=http,key='test-server-key').fetch('10','schema',None)
        self.assertTrue(result['available']);self.assertEqual(result['total'],1)
        self.assertTrue(result['definitions'][0]['hidden']);self.assertTrue(result['definitions'][0]['icon_url'].startswith('https:'))
        self.assertEqual(http.request.call_args.kwargs['params'],{'appid':'10','key':'test-server-key','l':'english','format':'json'})
        self.assertNotIn('test-server-key',str(result))

    def test_missing_malformed_and_duplicate_global_rows_are_unknown_not_zero(self):
        self.assertEqual(percentages({'achievementpercentages':{'achievements':[]}})['total'],0)
        for raw in [{}, {'achievementpercentages':{}}, {'achievementpercentages':{'achievements':None}},
            *[{'achievementpercentages':{'achievements':[{'name':'A','percent':value}]}} for value in [True,'NaN','inf',-1,101,None]],
            {'achievementpercentages':{'achievements':[{'name':'A','percent':1},{'name':'A','percent':2}]}}]:
            with self.assertRaises(ProviderError):percentages(raw)

    def test_schema_requires_explicit_complete_array_and_does_not_trust_arbitrary_icons(self):
        for raw in [{},{'game':{}},{'game':{'gameName':'Game','availableGameStats':{}}},
            {'game':{'gameName':'Game','availableGameStats':{'achievements':[{'name':'A','displayName':'Name','hidden':'1'}]}}}]:
            with self.assertRaises(ProviderError):definitions(raw,'10')
        self.assertEqual(definitions({'game':{'gameName':'Game','availableGameStats':{'achievements':[]}}},'10')['total'],0)
        for url in ['https://evil.example/steamcommunity/public/images/apps/10/a.jpg',
            'https://cdn.akamai.steamstatic.com:bad/steamcommunity/public/images/apps/10/a.jpg',
            'https://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps/11/a.jpg']:
            self.assertIsNone(icon(url,'10'))

    async def test_invalid_app_or_component_never_requests_remote_data(self):
        http=SimpleNamespace(request=AsyncMock());source=GameAchievementSource(http=http,key='')
        for app,part in [('0','globals'),('10/','schema'),('4294967296','globals'),('10','players')]:
            with self.assertRaises(ProviderError):await source.fetch(app,part,None)
        http.request.assert_not_awaited()
