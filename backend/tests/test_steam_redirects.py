import os
import unittest

os.environ.setdefault('SECRET_KEY', 'redirect-tests')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.catalogue_providers import ProviderError
from core.steam_redirects import target_app


class SteamRedirectTests(unittest.TestCase):
    def test_only_explicit_different_steam_app_redirects_are_accepted(self):
        self.assertEqual(target_app('2076040', 302, 'https://store.steampowered.com/app/2073850'), '2073850')
        self.assertEqual(target_app('2076040', 301, '/app/2073850/THE_FINALS/'), '2073850')
        self.assertIsNone(target_app('2076040', 200, None))
        for status, location in ((429, None), (500, None), (302, '/login/'),
                (302, '//evil.test/app/2073850'), (302, 'http://store.steampowered.com/app/2073850'),
                (302, 'https://store.steampowered.com:443/app/2073850'),
                (302, 'https://evil@store.steampowered.com/app/2073850'),
                (302, '/app/2076040/'), (302, '/app/0'), (302, '/app/4294967296'), (302, '/app/2073850#x')):
            with self.subTest(status=status, location=location), self.assertRaises(ProviderError):
                target_app('2076040', status, location)
