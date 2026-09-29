"""Shared cloud-import catalogue resolution and failure boundaries."""

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy import select

from core.catalog_import import get_or_create_show, get_or_create_episode_media
from models.show import Show


class CatalogueShowTests(unittest.IsolatedAsyncioTestCase):
    def session(self, rows, failure=None):
        db = MagicMock()
        results = []
        for row in rows:
            result = MagicMock()
            result.scalars.return_value.first.return_value = row
            results.append(result)
        db.execute = AsyncMock(side_effect=results)
        db.flush = AsyncMock(side_effect=failure)
        db.begin_nested.return_value = MagicMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)
        )
        return db

    async def test_existing_show_avoids_external_lookup(self):
        show = Show(tmdb_id=15, title="Existing")
        db = self.session([show])
        with patch("core.tmdb.get_show", AsyncMock()) as fetch:
            self.assertIs(await get_or_create_show(db, 15, "Fallback", None), show)
        fetch.assert_not_awaited()
        db.add.assert_not_called()

    async def test_new_show_preserves_metadata(self):
        db = self.session([None])
        data = {
            "name": "Canonical", "original_name": "Original", "vote_average": 8.2,
            "external_ids": {"imdb_id": "tt123"}, "original_language": "de",
            "genres": [{"name": "Drama"}],
            "seasons": [{"season_number": 1, "episode_count": 5, "name": "Season 1"}],
        }
        with patch("core.tmdb.get_show", AsyncMock(return_value=data)) as fetch:
            show = await get_or_create_show(db, 15, "Fallback", "key")
        self.assertEqual(show.title, "Canonical")
        self.assertEqual(show.original_title, "Original")
        self.assertEqual(show.tmdb_rating, 8.2)
        self.assertEqual(show.tmdb_data["external_ids"], {"imdb_id": "tt123"})
        self.assertEqual(show.tmdb_data["genres"], ["Drama"])
        self.assertEqual(show.tmdb_data["seasons"][0]["episode_count"], 5)
        fetch.assert_awaited_once_with(15, api_key="key")
        db.add.assert_called_once_with(show)
        db.flush.assert_awaited_once()

    async def test_lookup_failure_does_not_write(self):
        db = self.session([None])
        with patch("core.tmdb.get_show", AsyncMock(side_effect=OSError("offline"))):
            self.assertIsNone(await get_or_create_show(db, 15, "Fallback", None))
        db.add.assert_not_called()
        db.flush.assert_not_awaited()

    async def test_concurrent_insert_returns_winner(self):
        winner = Show(id=42, tmdb_id=15, title="Winner")
        db = self.session([None, winner], IntegrityError("insert", {}, Exception("duplicate")))
        with patch("core.tmdb.get_show", AsyncMock(return_value={"name": "Other"})):
            self.assertIs(await get_or_create_show(db, 15, "Fallback", None), winner)
        db.begin_nested.return_value.__aexit__.assert_awaited_once()
        self.assertEqual(db.execute.await_count, 2)

    async def test_unrelated_integrity_failure_propagates(self):
        failure = IntegrityError("insert", {}, Exception("unrelated constraint"))
        db = self.session([None, None], failure)
        with patch("core.tmdb.get_show", AsyncMock(return_value={"name": "New"})):
            with self.assertRaises(IntegrityError) as caught:
                await get_or_create_show(db, 15, "Fallback", None)
        self.assertIs(caught.exception, failure)

    async def test_database_failure_is_not_treated_as_lookup_failure(self):
        db = self.session([None], RuntimeError("database unavailable"))
        with patch("core.tmdb.get_show", AsyncMock(return_value={"name": "New"})):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                await get_or_create_show(db, 15, "Fallback", None)


class CatalogueEpisodeTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        db = MagicMock()
        result = MagicMock()
        result.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=result)
        return db

    async def test_database_write_failure_propagates_instead_of_becoming_a_missing_episode(self):
        data = {"episodes": [{"episode_number": 1, "id": 51, "name": "Episode"}]}
        with (
            patch("core.tmdb.get_season", AsyncMock(return_value=data)),
            patch("core.catalog_import.create_media_safely", AsyncMock(side_effect=RuntimeError("database unavailable"))),
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                await get_or_create_episode_media(self.session(), 4, 40, 1, 1, None)

    async def test_external_failure_is_skipped_without_writing_or_poisoning_the_cache(self):
        cache = {}
        with (
            patch("core.tmdb.get_season", AsyncMock(side_effect=OSError("offline"))),
            patch("core.catalog_import.create_media_safely", AsyncMock()) as create,
        ):
            self.assertIsNone(await get_or_create_episode_media(self.session(), 4, 40, 1, 1, None, cache))
        create.assert_not_awaited()
        self.assertEqual(cache, {})

    async def test_season_cache_reuses_remote_data_and_keeps_episode_metadata(self):
        data = {"episodes": [
            {"episode_number": 1, "id": 51, "name": "First", "runtime": 42},
            {"episode_number": 2, "id": 52, "name": "Second", "runtime": 48},
        ]}
        cache = {}
        with (
            patch("core.tmdb.get_season", AsyncMock(return_value=data)) as fetch,
            patch("core.catalog_import.create_media_safely", AsyncMock(return_value=(MagicMock(), True))) as create,
        ):
            await get_or_create_episode_media(self.session(), 4, 40, 1, 1, "key", cache)
            await get_or_create_episode_media(self.session(), 4, 40, 1, 2, "key", cache)
        fetch.assert_awaited_once_with(40, 1, api_key="key")
        self.assertEqual([call.args[1] for call in create.await_args_list], [51, 52])
        self.assertEqual([call.kwargs["runtime"] for call in create.await_args_list], [42, 48])
        self.assertEqual(create.await_args_list[1].kwargs["show_id"], 4)
        self.assertEqual(create.await_args_list[1].kwargs["episode_number"], 2)


@unittest.skipUnless(os.getenv("TRACKING_TEST_DATABASE_URL"), "Requires disposable PostgreSQL database")
class CatalogueShowDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TRACKING_TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.db = AsyncSession(bind=self.connection, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.db.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def test_losing_insert_keeps_other_import_writes_usable(self):
        marker = Show(tmdb_id=91000001, title="Earlier import item")
        self.db.add(marker)
        await self.db.flush()
        winner = Show(tmdb_id=91000002, title="Other importer")

        async def another_importer(*args, **kwargs):
            # Reproduce the check/insert race: the row appears after the lookup.
            self.db.add(winner)
            await self.db.flush()
            return {"name": "Losing importer"}

        with patch("core.tmdb.get_show", side_effect=another_importer):
            result = await get_or_create_show(self.db, 91000002, "Fallback", None)
        self.assertEqual(result.id, winner.id)
        self.assertEqual(result.title, "Other importer")
        remaining = (await self.db.execute(select(Show).where(Show.tmdb_id == 91000001))).scalar_one()
        self.assertEqual(remaining.title, "Earlier import item")
        later = Show(tmdb_id=91000003, title="Later import item")
        self.db.add(later)
        await self.db.flush()
        self.assertIsNotNone(later.id)
