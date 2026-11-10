import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
from core import server_sync

from core.watch_intents import _write_nuvio_watch_batch
from models import MediaType


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

    async def _confirm_additions(self, _url, _token, _profile, additions, _removals, *, on_snapshot, **kwargs):
        await on_snapshot(object(), 'access', additions)
        return additions

    async def test_successful_write_maps_the_exact_content_id_sent_to_nuvio(self):
        connection = self._connection()
        media = SimpleNamespace(
            id=31, media_type=MediaType.movie, show_id=None, tmdb_id=42,
            tmdb_data={}, imdb_id=None,
        )
        baseline = SimpleNamespace(snapshot={"mappings": {"existing-id": 99}})
        db = self._db(baseline)
        payload = {"content_id": "provider-id-used-for-write", "content_type": "movie", "watched_at": 1760000000000}

        with (
            patch("core.settings_store.get_effective_tmdb_key", new_callable=AsyncMock, return_value="tmdb-key"),
            patch("core.nuvio_projection.ensure_imdb_ids", new_callable=AsyncMock),
            patch("core.nuvio_payloads.watched_item", return_value=payload),
            patch("core.nuvio.connection_lock", side_effect=self._unlocked),
            patch("core.nuvio.sync_watched_delta", new_callable=AsyncMock, side_effect=self._confirm_additions) as push,
            patch("core.nuvio_visibility.sync_next_up_visibility", new_callable=AsyncMock),
        ):
            await _write_nuvio_watch_batch(db, connection, [(SimpleNamespace(id=1, media_id=31, desired_watched=True), media, None)])

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
        show = SimpleNamespace(id=8, tmdb_id=700, tmdb_data={})
        baseline = SimpleNamespace(snapshot={"mappings": {"existing-id": 99}})
        db = self._db(baseline)
        db.get = AsyncMock(side_effect=lambda model, _identity: show if model.__name__ == "Show" else baseline)
        payload = {
            "content_id": "series-provider-id-used-for-write",
            "content_type": "series", "season": 2, "episode": 3, "watched_at": 1760000000000,
        }

        with (
            patch("core.settings_store.get_effective_tmdb_key", new_callable=AsyncMock, return_value="tmdb-key"),
            patch("core.nuvio_projection.ensure_imdb_ids", new_callable=AsyncMock),
            patch("core.nuvio_payloads.watched_item", return_value=payload),
            patch("core.nuvio.connection_lock", side_effect=self._unlocked),
            patch("core.nuvio.sync_watched_delta", new_callable=AsyncMock, side_effect=self._confirm_additions) as push,
            patch("core.nuvio_visibility.sync_next_up_visibility", new_callable=AsyncMock),
        ):
            await _write_nuvio_watch_batch(db, connection, [(SimpleNamespace(id=1, media_id=31, desired_watched=True), episode, None)])

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
        payload = {"content_id": "provider-id-that-failed", "content_type": "movie", "watched_at": 1760000000000}

        with (
            patch("core.settings_store.get_effective_tmdb_key", new_callable=AsyncMock, return_value="tmdb-key"),
            patch("core.nuvio_projection.ensure_imdb_ids", new_callable=AsyncMock),
            patch("core.nuvio_payloads.watched_item", return_value=payload),
            patch("core.nuvio.connection_lock", side_effect=self._unlocked),
            patch("core.nuvio.sync_watched_delta", new_callable=AsyncMock, side_effect=RuntimeError("provider error")),
        ):
            with self.assertRaisesRegex(RuntimeError, "provider error"):
                await _write_nuvio_watch_batch(db, connection, [(SimpleNamespace(id=1, desired_watched=True), media, None)])

        self.assertEqual(baseline.snapshot["mappings"], {"existing-id": 99})

    async def test_mixed_watch_history_batch_refreshes_once_and_shares_the_readback(self):
        from core import nuvio

        addition = {'content_id': 'tt1', 'season': 6, 'episode': 7, 'watched_at': 1760000000000}
        removal = {'content_id': 'tt1', 'season': 6, 'episode': 8}
        token = SimpleNamespace(access_token='access', refresh_token='refresh')
        on_refresh = AsyncMock()
        on_snapshot = AsyncMock()
        with (
            patch.object(nuvio, 'refresh_session', AsyncMock(return_value=token)) as refresh,
            patch.object(nuvio, '_rpc', AsyncMock()) as rpc,
            patch.object(nuvio, '_pull_watched_items', AsyncMock(side_effect=[[removal], [addition]])) as read,
        ):
            result = await nuvio.sync_watched_delta('https://example.test', 'old', 2,
                [addition], [removal], on_refresh=on_refresh, on_snapshot=on_snapshot)
        refresh.assert_awaited_once()
        self.assertEqual(read.await_count, 2)
        on_snapshot.assert_awaited_once_with(read.await_args.args[0], 'access', [addition])
        on_refresh.assert_awaited_once_with(token)
        self.assertEqual([call.args[3:] for call in rpc.await_args_list], [
            ('sync_push_watched_items', {'p_profile_id': 2, 'p_items': [addition]}),
            ('sync_delete_watched_items', {'p_profile_id': 2, 'p_keys': [removal]}),
        ])
        self.assertEqual(result, [addition])

    async def test_existing_history_batch_preserves_dates_and_only_reads_once(self):
        from core import nuvio

        history = [{'content_id': 'tt1', 'season': 1, 'episode': episode} for episode in range(1, 101)]
        additions = [{**row, 'watched_at': 1760000000000} for row in history]
        with (
            patch.object(nuvio, 'refresh_session', AsyncMock(return_value=SimpleNamespace(access_token='access'))) as refresh,
            patch.object(nuvio, '_rpc', AsyncMock()) as rpc,
            patch.object(nuvio, '_pull_watched_items', AsyncMock(return_value=history)) as read,
        ):
            result = await nuvio.sync_watched_delta('https://example.test', 'old', 2, additions, [])
        self.assertEqual(result, history)
        refresh.assert_awaited_once()
        read.assert_awaited_once()
        rpc.assert_not_awaited()

    async def test_full_push_persists_confirmed_visibility_echo_before_continuing(self):

        events = []
        db = SimpleNamespace(commit=AsyncMock(side_effect=lambda: events.append("commit")))
        conn = SimpleNamespace(id=12)
        written = {"tv": {"tt42": True}}

        async def record(_db, _conn, _written):
            events.append("record")

        with patch("core.nuvio_visibility.record_visibility_echo", side_effect=record) as save:
            await server_sync._record_full_push_visibility_echo(db, conn, written)

        save.assert_awaited_once_with(db, conn, written)
        self.assertEqual(events, ["record", "commit"])


if __name__ == "__main__":
    unittest.main()
