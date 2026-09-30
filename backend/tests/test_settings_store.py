import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import settings_store


def session(*rows):
    return SimpleNamespace(
        info={},
        execute=AsyncMock(side_effect=[SimpleNamespace(scalar_one_or_none=lambda row=row: row) for row in rows]),
    )


class MetadataCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_key_wins_without_querying_global_settings(self):
        db = session(SimpleNamespace(tmdb_api_key="user-key"))
        self.assertEqual(await settings_store.get_user_tmdb_key(db, 7), "user-key")
        self.assertEqual(await settings_store.get_user_tmdb_key(db, 7), "user-key")
        self.assertEqual(db.execute.await_count, 1)
        self.assertNotIn("global_settings", db.info)

    async def test_cached_keys_are_scoped_by_user(self):
        db = session(SimpleNamespace(tmdb_api_key="first"), SimpleNamespace(tmdb_api_key="second"))
        self.assertEqual(await settings_store.get_user_tmdb_key(db, 7), "first")
        self.assertEqual(await settings_store.get_user_tmdb_key(db, 8), "second")
        self.assertEqual(await settings_store.get_user_tmdb_key(db, 7), "first")
        self.assertEqual(db.execute.await_count, 2)

    async def test_empty_user_keys_share_the_cached_global_fallback(self):
        global_settings = SimpleNamespace(tmdb_api_key="global-key")
        db = session(SimpleNamespace(tmdb_api_key=""), global_settings, None)
        self.assertEqual(await settings_store.get_user_tmdb_key(db, 7), "global-key")
        self.assertEqual(await settings_store.get_user_tmdb_key(db, 8), "global-key")
        self.assertEqual(await settings_store.get_user_tmdb_key(db, 7), "global-key")
        self.assertEqual(db.execute.await_count, 3)
        self.assertIs(await settings_store.get_global_settings(db), global_settings)

    async def test_missing_user_and_global_rows_are_cached(self):
        db = session(None, None, None)
        self.assertIsNone(await settings_store.get_user_tmdb_key(db, 7))
        self.assertIsNone(await settings_store.get_user_tmdb_key(db, 8))
        self.assertIsNone(await settings_store.get_user_tmdb_key(db, 7))
        self.assertIsNone(await settings_store.get_global_settings(db))
        self.assertEqual(db.execute.await_count, 3)

    async def test_loaded_user_settings_avoid_all_queries(self):
        db = session()
        self.assertEqual(await settings_store.get_effective_tmdb_key(db, SimpleNamespace(tmdb_api_key="own-key")), "own-key")
        db.execute.assert_not_awaited()

    async def test_job_fallback_refreshes_without_overwriting_request_cache(self):
        db = session(SimpleNamespace(tmdb_api_key="new-key"), None)
        cached = SimpleNamespace(tmdb_api_key="old-key")
        db.info["global_settings"] = cached
        self.assertEqual(await settings_store.get_effective_tmdb_key(db, None), "new-key")
        self.assertIsNone(await settings_store.get_effective_tmdb_key(db, SimpleNamespace(tmdb_api_key="")))
        self.assertIs(await settings_store.get_global_settings(db), cached)
        self.assertEqual(db.execute.await_count, 2)

    def test_client_environment_fallback_counts_as_a_usable_key(self):
        with patch.object(settings_store, "settings", SimpleNamespace()):
            self.assertFalse(settings_store.check_tmdb_key(None))
            self.assertFalse(settings_store.check_tmdb_key(""))
            self.assertTrue(settings_store.check_tmdb_key("explicit-key"))
        with patch.object(settings_store, "settings", SimpleNamespace(tmdb_api_key="environment-key")):
            self.assertTrue(settings_store.check_tmdb_key(None))
