import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import enrichment, show_metadata


def session(row=None):
    return SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row)),
        flush=AsyncMock(), add=Mock(), begin_nested=Mock(return_value=AsyncMock()),
    )


class ShowLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_show_backfills_tvdb_identity_without_metadata_request(self):
        show = SimpleNamespace(tvdb_id=None, tmdb_data={"external_ids": {"tvdb_id": "42"}})
        db = session(show)
        with patch.object(show_metadata.tmdb, "get_show", AsyncMock()) as fetch, \
             patch.object(show_metadata, "link_show_ids", AsyncMock(return_value=True)) as link:
            self.assertIs(await show_metadata.find_or_create_show(db, 10, "key"), show)
        link.assert_awaited_once_with(db, show, tvdb_id=42)
        fetch.assert_not_awaited()
        db.add.assert_not_called()
        db.flush.assert_awaited_once()

    async def test_new_show_does_not_claim_another_shows_tvdb_identity(self):
        db = session()
        data = {"name": "Example", "external_ids": {"tvdb_id": 42}, "seasons": [], "networks": []}
        with patch.object(show_metadata.tmdb, "get_show", AsyncMock(return_value=data)), \
             patch.object(show_metadata, "show_tvdb_id_is_free", AsyncMock(return_value=False)):
            show = await show_metadata.find_or_create_show(db, 10, "key")
        self.assertEqual(show.tmdb_id, 10)
        self.assertEqual(show.title, "Example")
        self.assertIsNone(show.tvdb_id)
        self.assertEqual(show.tmdb_data["external_ids"], {"tvdb_id": 42})
        db.add.assert_called_once_with(show)
        db.flush.assert_awaited_once()

    async def test_failed_metadata_request_propagates_without_creating_placeholder(self):
        db = session()
        with patch.object(show_metadata.tmdb, "get_show", AsyncMock(side_effect=RuntimeError("offline"))):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                await show_metadata.find_or_create_show(db, 10, "key")
        db.add.assert_not_called()
        db.flush.assert_not_awaited()


class TvdbFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_unmatched_show_or_anonymous_caller_needs_no_lookup(self):
        db = session()
        for show, user_id in ((None, 7), (SimpleNamespace(tvdb_id=None), 7), (SimpleNamespace(tvdb_id=42), None)):
            self.assertEqual(await enrichment.resolve_tvdb_fallback(db, show, user_id), (None, None, None))
        db.execute.assert_not_awaited()

    async def test_key_and_language_are_resolved_for_matched_show(self):
        db = session()
        show = SimpleNamespace(tvdb_id=42)
        with patch("core.settings_store.get_user_tvdb_key", AsyncMock(return_value="key")) as key, \
             patch("core.translations.get_user_metadata_language", AsyncMock(return_value="de")) as language:
            self.assertEqual(await enrichment.resolve_tvdb_fallback(db, show, 7), (42, "key", "deu"))
        key.assert_awaited_once_with(db, 7)
        language.assert_awaited_once_with(db, 7)

    async def test_missing_key_skips_language_and_lookup_failure_is_best_effort(self):
        db = session()
        show = SimpleNamespace(tvdb_id=42)
        with patch("core.settings_store.get_user_tvdb_key", AsyncMock(return_value=None)), \
             patch("core.translations.get_user_metadata_language", AsyncMock()) as language:
            self.assertEqual(await enrichment.resolve_tvdb_fallback(db, show, 7), (42, None, None))
        language.assert_not_awaited()
        with patch("core.settings_store.get_user_tvdb_key", AsyncMock(side_effect=RuntimeError("offline"))):
            self.assertEqual(await enrichment.resolve_tvdb_fallback(db, show, 7), (None, None, None))
