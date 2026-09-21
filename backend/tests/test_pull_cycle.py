import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

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
