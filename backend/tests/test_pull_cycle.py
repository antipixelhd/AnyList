import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "local-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core.pull_cycle import (
    coordinated_pull_cycle,
    defer_fan_out,
    defer_library_fan_out,
    is_active,
)
from models.base import CollectionSource


class PullCycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_media_server_pull_excludes_source(self):
        from core.pull_propagation import propagate_media_server_pull

        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: object())
        db.get.return_value = SimpleNamespace(approved=True)
        conn = SimpleNamespace(id=7, user_id=41, type='jellyfin')
        fan_out = AsyncMock()
        with patch('routers.sync._fan_out_changes_to_other_connections', fan_out):
            await propagate_media_server_pull(db, conn=conn, watched_ids={10}, ratings={})
            fan_out.assert_awaited_once()
            self.assertEqual(fan_out.await_args.args[:5], (db, 41, 7, {10}, {}))

    async def test_cloud_pull_exports_only_when_approved_and_complete(self):
        from core.pull_propagation import propagate_cloud_pull

        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: object())
        fan_out = AsyncMock()
        with patch('core.cloud_reconciliation.cloud_push_is_approved', AsyncMock(return_value=True)), \
             patch('routers.sync._fan_out_changes_to_other_connections', fan_out):
            await propagate_cloud_pull(
                db, user_id=41, provider='trakt', watched_ids={10},
                ratings={(11, None): 8.0}, complete=True,
            )
            fan_out.assert_awaited_once()
            self.assertEqual(fan_out.await_args.kwargs['exclude_cloud_source'], CollectionSource.trakt)
            self.assertEqual(fan_out.await_args.args[3], {10})
            self.assertEqual(fan_out.await_args.args[4], {(11, None): 8.0})

        fan_out.reset_mock()
        with patch('core.cloud_reconciliation.cloud_push_is_approved', AsyncMock(return_value=False)), \
             patch('routers.sync._fan_out_changes_to_other_connections', fan_out):
            await propagate_cloud_pull(
                db, user_id=41, provider='trakt', watched_ids={10},
                ratings={}, complete=True,
            )
            await propagate_cloud_pull(
                db, user_id=41, provider='trakt', watched_ids={10},
                ratings={}, complete=False,
            )
            fan_out.assert_not_awaited()

    async def test_overlapping_sources_coalesce_deltas_and_exclusions(self):
        async with coordinated_pull_cycle(41) as state:
            self.assertTrue(is_active(41))
            self.assertTrue(defer_fan_out(
                41,
                exclude_connection_id=7,
                exclude_cloud_source=None,
                new_watched_ids={10},
                new_ratings={(10, None): 8.0},
                removed_ratings=set(),
                new_collected_ids={10},
                removed_collected_ids=set(),
            ))
            self.assertTrue(defer_fan_out(
                41,
                exclude_connection_id=8,
                exclude_cloud_source=CollectionSource.trakt,
                new_watched_ids={11},
                new_ratings={(10, None): 9.0},
                removed_ratings={(12, None)},
                new_collected_ids=set(),
                removed_collected_ids={12},
            ))
            self.assertTrue(defer_library_fan_out(
                41,
                source_connection_id=7,
                new_collected_ids={10},
                removed_collected_ids=set(),
                api_key="key",
            ))
            self.assertTrue(defer_library_fan_out(
                41,
                source_connection_id=8,
                new_collected_ids=set(),
                removed_collected_ids={12},
                api_key=None,
            ))

        self.assertFalse(is_active(41))
        self.assertEqual(state.new_watched_ids, {10, 11})
        self.assertEqual(state.new_ratings, {(10, None): 9.0})
        self.assertEqual(state.removed_ratings, {(12, None)})
        self.assertEqual(state.excluded_connection_ids, {7, 8})
        self.assertEqual(state.excluded_cloud_sources, {CollectionSource.trakt})
        self.assertEqual(state.library_source_ids, {7, 8})
        self.assertEqual(state.library_new_ids, {10})
        self.assertEqual(state.library_removed_ids, {12})

    async def test_failed_pull_does_not_cancel_peer_or_skip_cycle_flush(self):
        import main

        both_started = asyncio.Event()
        starts = 0

        async def arrive():
            nonlocal starts
            starts += 1
            if starts == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)

        async def successful_pull():
            await arrive()
            defer_fan_out(
                52,
                exclude_connection_id=3,
                exclude_cloud_source=None,
                new_watched_ids={99},
                new_ratings={},
                removed_ratings=set(),
                new_collected_ids=set(),
                removed_collected_ids=set(),
            )

        async def failed_pull():
            await arrive()
            raise RuntimeError("provider unavailable")

        flush = AsyncMock()
        with patch.object(main, "_flush_pull_cycle", flush):
            await main._run_scheduled_pull_cycle(52, [
                ("stremio:3", successful_pull),
                ("nuvio:4", failed_pull),
            ])

        flush.assert_awaited_once()
        state = flush.await_args.args[0]
        self.assertEqual(state.new_watched_ids, {99})
        self.assertEqual(state.excluded_connection_ids, {3})
        self.assertFalse(is_active(52))


if __name__ == "__main__":
    unittest.main()
