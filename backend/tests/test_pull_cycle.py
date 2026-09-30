import asyncio
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "local-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core.pull_cycle import (
    coordinated_pull_cycle,
    defer_fan_out,
    defer_library_fan_out,
    defer_watch_removals,
    is_active,
)
from models.base import CollectionSource


class PullCycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_unwatch_waits_for_cycle_and_excludes_its_connection(self):
        from core.pull_propagation import propagate_media_server_pull

        db = AsyncMock()
        db.get.return_value = SimpleNamespace(approved=True)
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
        conn = SimpleNamespace(id=7, user_id=71, type='nuvio')
        with patch('core.outbound_sync.fan_out_changes', AsyncMock()), \
             patch('routers.history._push_watch_state', AsyncMock()) as push:
            async with coordinated_pull_cycle(71) as state:
                await propagate_media_server_pull(
                    db, conn=conn, watched_ids=set(), ratings={},
                    removed_watched_ids={10},
                )
                self.assertEqual(state.removed_watch_exclusions, {10: {7}})
                push.assert_not_awaited()

    async def test_media_server_pull_excludes_source(self):
        from core.pull_propagation import propagate_media_server_pull

        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: object())
        db.get.return_value = SimpleNamespace(approved=True)
        conn = SimpleNamespace(id=7, user_id=41, type='jellyfin')
        fan_out = AsyncMock()
        queue = AsyncMock()
        dispatch = AsyncMock()
        with patch('core.outbound_sync.fan_out_changes', fan_out), \
             patch('core.watch_intents.queue_watch_intents', queue), \
             patch('core.watch_intents.dispatch_watch_intents', dispatch):
            await propagate_media_server_pull(db, conn=conn, watched_ids={10}, ratings={})
            fan_out.assert_awaited_once()
            self.assertEqual(fan_out.await_args.args[:5], (db, 41, 7, {10}, {}))
            self.assertEqual(queue.await_args.kwargs['exclude_connection_id'], 7)
            self.assertEqual(fan_out.await_args.kwargs['durable_watch_media_ids'], {10})

    async def test_cloud_pull_exports_only_when_approved_and_complete(self):
        from core.pull_propagation import propagate_cloud_pull

        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalar_one_or_none=lambda: SimpleNamespace(approved=True),
        )
        fan_out = AsyncMock()
        queue = AsyncMock()
        dispatch = AsyncMock()
        with patch('core.cloud_reconciliation.cloud_push_is_approved', AsyncMock(return_value=False)), \
             patch('core.outbound_sync.fan_out_changes', fan_out), \
             patch('core.watch_intents.queue_watch_intents', queue), \
             patch('core.watch_intents.dispatch_watch_intents', dispatch):
            await propagate_cloud_pull(
                db, user_id=41, provider='trakt', watched_ids={10},
                ratings={(11, None): 8.0}, complete=True,
            )
            fan_out.assert_awaited_once()
            self.assertEqual(fan_out.await_args.kwargs['exclude_cloud_source'], CollectionSource.trakt)
            self.assertEqual(fan_out.await_args.kwargs['durable_watch_media_ids'], {10})
            self.assertEqual(fan_out.await_args.args[3], {10})
            self.assertEqual(fan_out.await_args.args[4], {(11, None): 8.0})

        fan_out.reset_mock()
        db.execute.return_value = SimpleNamespace(
            scalar_one_or_none=lambda: SimpleNamespace(approved=False),
        )
        with patch('core.outbound_sync.fan_out_changes', fan_out):
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
                source_observed_at=datetime(2026, 1, 1),
            ))
            self.assertTrue(defer_library_fan_out(
                41,
                source_connection_id=8,
                new_collected_ids=set(),
                removed_collected_ids={12},
                api_key=None,
                source_observed_at=datetime(2026, 1, 2),
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
        self.assertEqual(state.library_observed_at_by_media, {
            10: datetime(2026, 1, 1), 12: datetime(2026, 1, 2),
        })
        self.assertEqual(state.library_source_ids_by_media, {10: {7}, 12: {8}})

    async def test_provider_library_queue_excludes_only_each_titles_source(self):
        from core.streaming_library import queue_provider_library_changes
        from models.streaming_library import StreamingLibraryDelivery, StreamingLibraryIntent

        class _Scalars:
            def __init__(self, values=()):
                self.values = list(values)

            def all(self):
                return self.values

        class _Result:
            def __init__(self, values=()):
                self.values = list(values)

            def scalars(self):
                return _Scalars(self.values)

        class _Database:
            def __init__(self):
                self.calls = 0
                self.added = []
                self.next_intent_id = 1
                self.target_query = None

            async def execute(self, query):
                self.calls += 1
                if self.calls == 2:
                    self.target_query = str(query)
                    return _Result([
                        SimpleNamespace(id=7, user_id=41, type='nuvio', push_collection=True),
                        SimpleNamespace(id=8, user_id=41, type='stremio', push_collection=True),
                        SimpleNamespace(id=9, user_id=41, type='stremio', push_collection=True),
                    ])
                if self.calls == 4:
                    return _Result([10, 20])
                return _Result()

            def add(self, value):
                if isinstance(value, StreamingLibraryIntent):
                    value.id = self.next_intent_id
                    self.next_intent_id += 1
                self.added.append(value)

            async def flush(self):
                return None

        db = _Database()
        observed_at = datetime(2026, 1, 1)
        await queue_provider_library_changes(
            db, 41, {10, 20},
            exclude_connection_ids={7, 8},
            source_observed_at_by_media={10: observed_at, 20: observed_at},
            source_connection_ids_by_media={10: {7}, 20: {8}},
        )

        intents = {row.media_id: row for row in db.added if isinstance(row, StreamingLibraryIntent)}
        deliveries = {
            (media_id, row.connection_id): row.state
            for row in db.added if isinstance(row, StreamingLibraryDelivery)
            for media_id, intent in intents.items() if row.intent_id == intent.id
        }
        self.assertEqual(deliveries, {
            (10, 7): 'observed', (10, 8): 'pending', (10, 9): 'pending',
            (20, 7): 'pending', (20, 8): 'observed', (20, 9): 'pending',
        })
        self.assertNotIn('NOT IN', db.target_query)

    async def test_cycle_flush_groups_watch_ids_by_their_own_source_exclusions(self):
        import main
        from core.pull_cycle import allow_cycle_delivery

        class _Result:
            def __init__(self, *, scalar=None, rows=()):
                self.scalar = scalar
                self.rows = list(rows)

            def scalar_one_or_none(self):
                return self.scalar

            def scalars(self):
                return iter(self.rows)

        class _SessionContext:
            def __init__(self, db):
                self.db = db

            async def __aenter__(self):
                return self.db

            async def __aexit__(self, *_args):
                return None

        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            _Result(scalar=None),       # user settings
            _Result(rows=[10, 11]),     # final canonical WatchEvents
            _Result(rows=[]),           # removed episode remains unwatched
        ]), commit=AsyncMock())
        fanout = AsyncMock()
        push_unwatched = AsyncMock()
        queue = AsyncMock()
        dispatch = AsyncMock()
        state_cm = coordinated_pull_cycle(61)
        with patch('db.async_sessionmaker', side_effect=lambda *_a, **_k: lambda: _SessionContext(db)), \
             patch('core.outbound_sync.fan_out_changes', fanout), \
             patch('routers.history._push_watch_state', push_unwatched), \
             patch('core.watch_intents.queue_watch_intents', queue), \
             patch('core.watch_intents.dispatch_watch_intents', dispatch), \
             patch('core.stream_actions.dispatch_stream_actions', AsyncMock()), \
             patch('core.cloud_actions.dispatch_cloud_actions', AsyncMock()):
            async with state_cm as state:
                for source_id, media_id in ((7, 10), (8, 11), (7, 12)):
                    defer_fan_out(
                        61, exclude_connection_id=source_id, exclude_cloud_source=None,
                        new_watched_ids={media_id}, new_ratings={}, removed_ratings=set(),
                        new_collected_ids=set(), removed_collected_ids=set(),
                    )
                defer_watch_removals(61, {13}, 7)
                with allow_cycle_delivery(state):
                    await main._flush_pull_cycle(state)

        watched_calls = [call for call in fanout.await_args_list if call.args[3]]
        self.assertEqual(len(watched_calls), 2)
        by_media_id = {next(iter(call.args[3])): call.kwargs['exclude_connection_ids']
                       for call in watched_calls}
        self.assertEqual(by_media_id, {10: {7}, 11: {8}})
        self.assertEqual(len(queue.await_args_list), 3)
        self.assertEqual(frozenset(queue.await_args_list[0].args[2]), {10})
        self.assertEqual(queue.await_args_list[0].kwargs['exclude_connection_ids'], {7})
        self.assertEqual(queue.await_args_list[1].kwargs['exclude_connection_ids'], {8})
        self.assertTrue(all(call.kwargs['durable_watch_media_ids'] for call in watched_calls))
        self.assertEqual(queue.await_args_list[-1].args[2], {13})
        self.assertEqual(queue.await_args_list[-1].kwargs['exclude_connection_ids'], {7})
        push_unwatched.assert_awaited_once_with(
            db, 61, [13], watched=False, exclude_connection_ids={7},
            skip_stream_watch_writes=True,
        )

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
