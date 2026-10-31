import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY', 'steam-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.steam import SteamError, SteamSource, achievement_data, parse_profile
from core.steam_sync import achievement_due
from routers.steam import Draft, selected_games


class SteamTests(unittest.IsolatedAsyncioTestCase):
    def test_profile_forms_and_untrusted_urls(self):
        native = '76561198000000000'
        for value in (native, f'https://steamcommunity.com/profiles/{native}/', f'steamcommunity.com/profiles/{native}?l=english', f'http://www.steamcommunity.com/profiles/{native}'):
            self.assertEqual(parse_profile(value), ('id', native))
        for value in ('some-player', 'https://steamcommunity.com/id/some-player/', 'steamcommunity.com/id/some-player'):
            self.assertEqual(parse_profile(value), ('vanity', 'some-player'))
        self.assertEqual(parse_profile('76561202255233023'), ('id', '76561202255233023'))
        for value in ('https://evil.test/id/name', 'https://steamcommunity.com.evil.test/id/name', 'https://user@steamcommunity.com/id/name', 'https://steamcommunity.com:90/id/name', 'https://steamcommunity.com/profiles/123', '123', 'steamcommunity.com/id/name/games', '', 'https://steamcommunity.com/id/%2Fetc'):
            with self.subTest(value=value), self.assertRaises(SteamError):
                parse_profile(value)

    async def test_profile_and_library_privacy_and_no_shared_personal_cache(self):
        http = AsyncMock()
        source = SteamSource(http=http, key='private-test-key')
        http.request.return_value = {'response': {'players': [{'steamid': '76561198000000000', 'communityvisibilitystate': 1}]}}
        with self.assertRaisesRegex(SteamError, 'private'):
            await source.profile('76561198000000000')
        http.request.return_value = {'response': {}}
        with self.assertRaisesRegex(SteamError, 'Game details'):
            await source.library('76561198000000000')
        http.request.return_value = {'response': {'game_count': 0}}
        self.assertEqual(await source.library('76561198000000000'), [])
        self.assertTrue(all(call.kwargs['cache'] is False for call in http.request.await_args_list))

    def test_unknown_stats_never_become_zero_and_completion_uses_denominator(self):
        self.assertIsNone(achievement_data({'playerstats': {'success': False}})['achievement_count'])
        self.assertIsNone(achievement_data({'playerstats': {'success': True}})['achievement_count'])
        self.assertEqual(achievement_data({'playerstats': {'success': True, 'achievements': [{'achieved': 1}, {'achieved': 0}]}}),
                         {'achievement_count': 1, 'achievement_total': 2})

    def test_selection_or_thresholds_manual_overrides_reset_and_unknown(self):
        rows = [{'app_id': '1', 'playtime_minutes': 30, 'achievement_count': None},
                {'app_id': '2', 'playtime_minutes': 0, 'achievement_count': 1},
                {'app_id': '3', 'playtime_minutes': 5, 'achievement_count': 0}]
        draft = Draft(generation=1)
        self.assertEqual(selected_games(rows, draft), ['1', '2'])
        draft.overrides = {'1': False, '3': True}
        draft.minimum_playtime = 0
        self.assertEqual(selected_games(rows, draft), ['2', '3'])
        self.assertEqual(selected_games(rows, Draft(generation=1)), ['1', '2'])

    def test_achievement_polling_checks_changed_and_daily_quiet_games(self):
        stamp = datetime(2026, 10, 31, 12)
        row = {'playtime_minutes': 45}
        prior = {'observed_playtime_minutes': 45, 'achievement_checked_at': stamp.isoformat()}
        self.assertFalse(achievement_due(row, prior, stamp))
        self.assertTrue(achievement_due({'playtime_minutes': 46}, prior, stamp))
        self.assertTrue(achievement_due(row, prior, stamp + timedelta(days=1)))

    async def test_visible_card_release_date_uses_shared_public_store_without_player_credentials(self):
        http = AsyncMock()
        source = SteamSource(http=http, key=None)
        http.request.return_value = {'1245620': {'success': True, 'data': {'steam_appid': 1245620, 'release_date': {'date': '25 Feb, 2022'}}}}
        self.assertEqual(await source.metadata('1245620'), {'release_date': '2022-02-25'})
        call = http.request.await_args
        self.assertNotIn('key', call.kwargs.get('params', {}))
        self.assertNotEqual(call.kwargs.get('cache'), False)
