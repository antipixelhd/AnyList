import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core.watch_intents import _write_provider_watch_state
from models import MediaType
from models.tracking import StreamBaseline


class _Result:
    def scalar_one_or_none(self):
        return SimpleNamespace(user_id=7)


class NuvioWatchWriteTests(unittest.IsolatedAsyncioTestCase):
    def _connection(self):
        return SimpleNamespace(
            id=12, user_id=7, type="nuvio", url="http://nuvio", token="token",
            server_user_id="2",
        )

    def _db(self, baseline):
        return SimpleNamespace(
            get=AsyncMock(return_value=baseline),
            execute=AsyncMock(return_value=_Result()),
            refresh=AsyncMock(),
        )

    @asynccontextmanager
    async def _unlocked(self, _connection_id):
        yield

    async def test_successful_write_maps_the_exact_content_id_sent_to_nuvio(self):
        connection = self._connection()
        media = SimpleNamespace(
            id=31, media_type=MediaType.movie, show_id=None, tmdb_id=42,
            tmdb_data={}, imdb_id=None,
        )
        baseline = SimpleNamespace(snapshot={"mappings": {"existing-id": 99}})
        db = self._db(baseline)
        payload = {"content_id": "provider-id-used-for-write", "type": "movie"}

        with (
            patch("routers.sync._get_effective_tmdb_key", new_callable=AsyncMock, return_value="tmdb-key"),
            patch("routers.sync._ensure_nuvio_imdb_ids", new_callable=AsyncMock),
            patch("routers.sync._nuvio_watched_item", return_value=payload),
            patch("core.nuvio.connection_lock", side_effect=self._unlocked),
            patch("core.nuvio.push_watched_items", new_callable=AsyncMock) as push,
        ):
            await _write_provider_watch_state(db, connection, media, True, None)

        push.assert_awaited_once()
        self.assertEqual(push.await_args.args[3], [payload])
        self.assertEqual(baseline.snapshot["mappings"], {
            "existing-id": 99,
            "provider-id-used-for-write": 42,
        })

    async def test_successful_episode_write_maps_content_id_to_show_tmdb_id(self):
        connection = self._connection()
        episode = SimpleNamespace(
            id=31, media_type=MediaType.episode, show_id=8, tmdb_id=None,
            season_number=2, episode_number=3, tmdb_data={}, imdb_id=None,
        )
        show = SimpleNamespace(id=8, tmdb_id=700)
        baseline = SimpleNamespace(snapshot={"mappings": {"existing-id": 99}})
        db = self._db(baseline)
        db.get = AsyncMock(side_effect=lambda model, _identity: show if model.__name__ == "Show" else baseline)
        payload = {
            "content_id": "series-provider-id-used-for-write",
            "type": "episode", "season": 2, "episode": 3,
        }

        with (
            patch("routers.sync._get_effective_tmdb_key", new_callable=AsyncMock, return_value="tmdb-key"),
            patch("routers.sync._ensure_nuvio_imdb_ids", new_callable=AsyncMock),
            patch("routers.sync._nuvio_watched_item", return_value=payload),
            patch("core.nuvio.connection_lock", side_effect=self._unlocked),
            patch("core.nuvio.push_watched_items", new_callable=AsyncMock) as push,
        ):
            await _write_provider_watch_state(db, connection, episode, True, None)

        push.assert_awaited_once()
        self.assertEqual(baseline.snapshot["mappings"]["series-provider-id-used-for-write"], 700)

    async def test_failed_provider_write_does_not_add_baseline_mapping(self):
        connection = self._connection()
        media = SimpleNamespace(
            id=31, media_type=MediaType.movie, show_id=None, tmdb_id=42,
            tmdb_data={}, imdb_id=None,
        )
        baseline = SimpleNamespace(snapshot={"mappings": {"existing-id": 99}})
        db = self._db(baseline)
        payload = {"content_id": "provider-id-that-failed", "type": "movie"}

        with (
            patch("routers.sync._get_effective_tmdb_key", new_callable=AsyncMock, return_value="tmdb-key"),
            patch("routers.sync._ensure_nuvio_imdb_ids", new_callable=AsyncMock),
            patch("routers.sync._nuvio_watched_item", return_value=payload),
            patch("core.nuvio.connection_lock", side_effect=self._unlocked),
            patch("core.nuvio.push_watched_items", new_callable=AsyncMock, side_effect=RuntimeError("provider error")),
        ):
            with self.assertRaisesRegex(RuntimeError, "provider error"):
                await _write_provider_watch_state(db, connection, media, True, None)

        self.assertEqual(baseline.snapshot["mappings"], {"existing-id": 99})


if __name__ == "__main__":
    unittest.main()
