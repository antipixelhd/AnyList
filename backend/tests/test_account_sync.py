"""Account admission, cadence, remote coverage and checkpoint regressions."""
import asyncio
import json
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from sqlalchemy import select

os.environ.setdefault("SECRET_KEY", "local-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from fastapi import BackgroundTasks, HTTPException
from core import account_sync, nuvio, provider_changes
from models import MediaServerConnection, User, UserSettings
from models.base import CollectionSource
from models.sync import SyncJob, SyncStatus
from routers import sync


def result(value=None, values=()):
    return SimpleNamespace(scalar_one_or_none=lambda: value,
                           scalars=lambda: SimpleNamespace(all=lambda: list(values)))


class AccountSyncRulesTests(unittest.IsolatedAsyncioTestCase):
    def test_completion_anchors_next_due_and_disabled_stays_disabled(self):
        finished = datetime(2026, 10, 4, 12, 17)
        self.assertEqual(account_sync.next_pull_due(finished, 0.5), finished + timedelta(minutes=30))
        self.assertIsNone(account_sync.next_pull_due(finished, None))
        self.assertEqual(account_sync.next_pull_due(None, 1), datetime.min)

    async def test_admission_locks_only_current_account_and_rejects_active_pull(self):
        db = SimpleNamespace(execute=AsyncMock(side_effect=[result(), result(19)]))
        with self.assertRaises(HTTPException) as raised:
            await account_sync.require_idle_account(db, 7)
        self.assertEqual(raised.exception.status_code, 409)
        lock, active = [call.args[0] for call in db.execute.await_args_list]
        self.assertIn("FOR UPDATE", str(lock))
        self.assertIn(7, active.compile().params.values())
        self.assertIn("pull_cycle", str(active.compile(compile_kwargs={"literal_binds": True})))

    async def test_manual_queue_includes_all_connections_and_clouds_with_pull_options(self):
        settings = SimpleNamespace(trakt_access_token="token", trakt_sync_watched=True)
        connections = [SimpleNamespace(id=2, type="plex", sync_watched=True),
                       SimpleNamespace(id=3, type="nuvio", sync_collection=True),
                       SimpleNamespace(id=4, type="emby", sync_watched=False)]
        db = SimpleNamespace(execute=AsyncMock(side_effect=[result(settings), result(values=connections)]),
                             add=Mock(), flush=AsyncMock(), commit=AsyncMock())
        def add(job):
            job.id = 30 + db.add.call_count
        db.add.side_effect = add
        with patch.object(account_sync, "require_idle_account", AsyncMock()), \
             patch.object(account_sync.settings_store, "get_effective_tmdb_key", AsyncMock(return_value="key")):
            parent = await account_sync.queue_account_pull(db, 7)
        jobs = [call.args[0] for call in db.add.call_args_list]
        self.assertEqual(parent.user_id, 7)
        self.assertEqual(parent.job_type, "pull_cycle")
        self.assertEqual([(job.source.value, job.connection_id) for job in jobs[1:]],
                         [("plex", 2), ("nuvio", 3), ("trakt", None)])
        self.assertEqual(parent.stats["child_job_ids"], [job.id for job in jobs[1:]])

    async def test_not_due_scheduled_account_creates_nothing(self):
        db = SimpleNamespace(execute=AsyncMock(side_effect=[result(SimpleNamespace(pull_sync_interval=1)),
                                                          result(datetime.utcnow())]), add=Mock())
        with patch.object(account_sync, "require_idle_account", AsyncMock()):
            self.assertIsNone(await account_sync.queue_account_pull(db, 7, scheduled=True))
        db.add.assert_not_called()

    async def test_account_route_schedules_only_authenticated_account(self):
        tasks, db = BackgroundTasks(), object()
        with patch.object(account_sync, "queue_account_pull", AsyncMock(return_value=SimpleNamespace(id=42))) as queue:
            response = await sync.sync_account(tasks, db, SimpleNamespace(id=7))
        queue.assert_awaited_once_with(db, 7)
        self.assertEqual(response["job_id"], 42)
        self.assertEqual(tasks.tasks[0].args, (7, 42))

    async def test_cancel_keeps_parent_active_until_workers_stop(self):
        parent = SimpleNamespace(status=SyncStatus.running, stats={"child_job_ids": [3, 4]})
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        await account_sync.request_cycle_cancel(db, parent)
        self.assertEqual(parent.status, SyncStatus.running)
        self.assertTrue(parent.stats["cancel_requested"])


class AccountSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_account_does_not_block_other_due_accounts(self):
        from core import scheduler
        rows = [SimpleNamespace(user_id=user_id, pull_sync_interval=1,
                                trakt_auto_push_interval=None, simkl_auto_push_interval=None,
                                mdblist_auto_push_interval=None) for user_id in (7, 8)]
        db = SimpleNamespace(execute=AsyncMock(side_effect=[SimpleNamespace(scalars=lambda: iter([])),
                                                            SimpleNamespace(scalars=lambda: iter(rows))]),
                             commit=AsyncMock(), rollback=AsyncMock())
        queue = AsyncMock(side_effect=[HTTPException(409, "busy"), SimpleNamespace(id=42)])
        launched = []
        def launch(coro):
            launched.append(coro)
            coro.close()
        with patch.object(account_sync, "queue_account_pull", queue), \
             patch.object(scheduler.asyncio, "create_task", launch):
            await scheduler._schedule_sync_tick(db)
        self.assertEqual(queue.await_args_list[0].args, (db, 7))
        self.assertEqual(queue.await_args_list[1].args, (db, 8))
        self.assertTrue(all(call.kwargs == {"scheduled": True} for call in queue.await_args_list))
        db.rollback.assert_awaited_once()
        self.assertEqual(len(launched), 1)

    async def test_push_keeps_its_own_due_time_and_runner_arguments(self):
        from core import scheduler
        finished = datetime.utcnow()
        db = SimpleNamespace(execute=AsyncMock(side_effect=[result(), result(finished)]),
                             add=Mock(), flush=AsyncMock(), commit=AsyncMock())
        await scheduler._queue_scheduled_push(db, user_id=7, source=CollectionSource.plex,
                                              interval=1, runner=AsyncMock(), connection_id=3)
        db.add.assert_not_called()
        db.execute.side_effect = [result(), result()]
        def add(job):
            job.id = 42
        db.add.side_effect = add
        calls = []
        async def runner(*args):
            calls.append(args)
        tasks = []
        def launch(coro):
            tasks.append(coro)
        with patch.object(scheduler.asyncio, "create_task", launch):
            await scheduler._queue_scheduled_push(db, user_id=7, source=CollectionSource.plex,
                                                  interval=1, runner=runner, connection_id=3)
        await tasks[0]
        self.assertEqual(calls, [(7, 3, 42)])
        self.assertEqual(db.add.call_args.args[0].job_type, "push")


class ProviderChangeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = SimpleNamespace(mdblist_api_key="test-key", mdblist_sync_watched=True)
        self.db = SimpleNamespace(execute=AsyncMock(return_value=result()), rollback=AsyncMock())

    async def check(self, payload, previous=None):
        self.db.execute.return_value = result(previous)
        with patch.object(provider_changes.mdblist, "_request", AsyncMock(return_value=payload)):
            return await provider_changes.check_provider_changes(self.db, user_id=7, provider="mdblist", settings=self.settings)

    async def test_successful_identical_checkpoint_skips_and_changed_clock_fetches(self):
        first = await self.check({"all": "2026-10-04T12:00:00Z"})
        self.assertEqual(first.state, provider_changes.ChangeState.changed)
        previous = SimpleNamespace(stats={"provider_checkpoint": first.checkpoint})
        same = await self.check({"all": "2026-10-04T12:00:00Z"}, previous)
        self.assertEqual(same.state, provider_changes.ChangeState.unchanged)
        newer = await self.check({"all": "2026-10-04T13:00:00Z"}, previous)
        self.assertEqual(newer.state, provider_changes.ChangeState.changed)

    async def test_changed_credentials_or_pull_options_invalidate_checkpoint(self):
        first = await self.check({"all": "2026-10-04T12:00:00Z"})
        previous = SimpleNamespace(stats={"provider_checkpoint": first.checkpoint})
        for field, value in (("mdblist_api_key", "another-account"), ("mdblist_sync_ratings", True)):
            setattr(self.settings, field, value)
            self.assertEqual((await self.check({"all": "2026-10-04T12:00:00Z"}, previous)).state,
                             provider_changes.ChangeState.changed)
        self.assertNotIn("test-key", json.dumps(first.checkpoint))

    async def test_invalid_or_missing_aggregate_is_unknown(self):
        for payload in ({}, {"all": None}, {"all": "bad"}, {"all": "2026-10-04T12:00:00"}, []):
            self.assertEqual((await self.check(payload)).state, provider_changes.ChangeState.unknown)

    async def test_failed_probe_is_unknown_and_does_not_advance_checkpoint(self):
        with patch.object(provider_changes.mdblist, "_request", AsyncMock(side_effect=RuntimeError("offline"))):
            checked = await provider_changes.check_provider_changes(self.db, user_id=7, provider="mdblist", settings=self.settings)
        self.assertEqual(checked.state, provider_changes.ChangeState.unknown)
        self.assertIsNone(checked.checkpoint)

    async def test_uncovered_providers_and_trakt_custom_lists_never_skip(self):
        for provider in ("plex", "jellyfin", "emby", "arvio", "trakt"):
            checked = await provider_changes.check_provider_changes(self.db, user_id=7, provider=provider,
                                                                     settings=SimpleNamespace(trakt_sync_lists=True))
            self.assertEqual(checked.state, provider_changes.ChangeState.unknown)
        self.db.execute.assert_not_awaited()

    async def test_stremio_metadata_detects_removed_ids_even_with_same_max_clock(self):
        conn = SimpleNamespace(id=2, url="url", token="token", provider_account_id="account", server_user_id=None,
                               identity_version=1, stremio_full_sync_done=True, sync_collection=True)
        async def check(meta, saved=None):
            self.db.execute.return_value = result(saved)
            with patch.object(provider_changes.stremio, "datastore_meta", AsyncMock(return_value=meta)):
                return await provider_changes.check_provider_changes(self.db, user_id=7, provider="stremio", settings=self.settings, conn=conn)
        first = await check([["one", 120], ["two", 125]])
        previous = SimpleNamespace(stats={"provider_checkpoint": first.checkpoint})
        self.assertEqual((await check([["two", 125], ["one", 120]], previous)).state,
                         provider_changes.ChangeState.unchanged)
        self.assertEqual((await check([["two", 125]], previous)).state, provider_changes.ChangeState.changed)

    async def test_nuvio_settings_change_is_not_hidden_by_identical_data_cursors(self):
        session = SimpleNamespace(access_token="access", refresh_token="refresh")
        settings = {"version": 1, "features": {}}
        with patch.object(nuvio, "refresh_session", AsyncMock(return_value=session)), \
             patch.object(nuvio, "get_profiles", AsyncMock(return_value=[{"profile_index": 1}])), \
             patch.object(nuvio, "_rpc", AsyncMock(return_value=120)), \
             patch.object(nuvio, "_pull_profile_settings", AsyncMock(return_value=settings)):
            first = await nuvio.pull_change_marker("url", "refresh", 1)
            settings = {"version": 1, "features": {"changed": True}}
            with patch.object(nuvio, "_pull_profile_settings", AsyncMock(return_value=settings)):
                second = await nuvio.pull_change_marker("url", "refresh", 1)
        self.assertEqual(first["library"], second["library"])
        self.assertNotEqual(first, second)


@unittest.skipUnless(os.getenv("TRACKING_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class AccountSyncDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        self.engine = create_async_engine(os.environ["TRACKING_TEST_DATABASE_URL"])
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.factory() as db:
            self.user = User(email="cycle-owner@test.invalid", username="cycle-owner", api_key="cycle-owner-test")
            db.add(self.user)
            await db.flush()
            db.add(UserSettings(user_id=self.user.id, pull_sync_interval=1))
            db.add(MediaServerConnection(user_id=self.user.id, type="plex", name="Fixture", url="http://invalid", token="test"))
            await db.commit()

    async def asyncTearDown(self):
        from sqlalchemy import delete
        async with self.factory() as db:
            await db.execute(delete(User).where(User.id == self.user.id))
            await db.commit()
        await self.engine.dispose()

    async def test_concurrent_manual_and_scheduled_admission_creates_only_one_cycle(self):
        async def queue(scheduled):
            async with self.factory() as db:
                try:
                    with patch.object(account_sync.settings_store, "get_effective_tmdb_key", AsyncMock(return_value="key")):
                        return await account_sync.queue_account_pull(db, self.user.id, scheduled=scheduled)
                except HTTPException as error:
                    await db.rollback()
                    return error.status_code
        first, second = await asyncio.gather(queue(False), queue(True))
        self.assertEqual(sum(isinstance(item, SyncJob) for item in (first, second)), 1)
        self.assertIn(409, (first, second))

    async def test_manual_completion_resets_shared_due_time(self):
        async with self.factory() as db:
            db.add(SyncJob(user_id=self.user.id, source=CollectionSource.manual, job_type="pull_cycle",
                           status=SyncStatus.completed, updated_at=datetime.utcnow()))
            await db.commit()
            self.assertIsNone(await account_sync.queue_account_pull(db, self.user.id, scheduled=True))

    async def test_cycle_holds_admission_until_final_outbound_delivery_finishes(self):
        from core import scheduler
        from core.pull_cycle import is_active
        with patch.object(account_sync.settings_store, "get_effective_tmdb_key", AsyncMock(return_value="key")):
            async with self.factory() as db:
                parent = await account_sync.queue_account_pull(db, self.user.id)
        delivering, release = asyncio.Event(), asyncio.Event()
        async def complete_provider(user_id, child_id):
            self.assertTrue(is_active(user_id))
            async with self.factory() as db:
                child = await db.get(SyncJob, child_id)
                child.status = SyncStatus.completed
                await db.commit()
        async def deliver(state):
            delivering.set()
            await release.wait()
        with patch.object(account_sync, "async_sessionmaker", return_value=self.factory), \
             patch.object(account_sync, "_run_provider", complete_provider), \
             patch.object(scheduler, "_flush_pull_cycle", deliver):
            task = asyncio.create_task(account_sync.run_account_pull(self.user.id, parent.id))
            try:
                await asyncio.wait_for(delivering.wait(), 5)
                async with self.factory() as db:
                    with self.assertRaises(HTTPException) as raised:
                        await account_sync.queue_account_pull(db, self.user.id)
                    self.assertEqual(raised.exception.status_code, 409)
            finally:
                release.set()
                await task
        async with self.factory() as db:
            finished = await db.get(SyncJob, parent.id)
            self.assertEqual(finished.status, SyncStatus.completed)
            self.assertEqual(finished.processed_items, finished.total_items)

    async def test_cancelled_cycle_blocks_admission_and_finishes_as_cancelled(self):
        from core import scheduler
        with patch.object(account_sync.settings_store, "get_effective_tmdb_key", AsyncMock(return_value="key")):
            async with self.factory() as db:
                parent = await account_sync.queue_account_pull(db, self.user.id)
                await account_sync.request_cycle_cancel(db, parent)
            async with self.factory() as db:
                with self.assertRaises(HTTPException):
                    await account_sync.require_idle_account(db, self.user.id)
        with patch.object(account_sync, "async_sessionmaker", return_value=self.factory), \
             patch.object(scheduler, "_flush_pull_cycle", AsyncMock()):
            await account_sync.run_account_pull(self.user.id, parent.id)
        async with self.factory() as db:
            self.assertEqual((await db.get(SyncJob, parent.id)).status, SyncStatus.cancelled)

    async def test_incomplete_import_does_not_publish_a_remote_checkpoint(self):
        async with self.factory() as db:
            conn = (await db.execute(select(MediaServerConnection).where(
                MediaServerConnection.user_id == self.user.id))).scalar_one()
            job = SyncJob(user_id=self.user.id, connection_id=conn.id, source=CollectionSource.plex,
                          job_type="pull", status=SyncStatus.pending)
            db.add(job)
            await db.commit()
        checked = provider_changes.ChangeCheck(provider_changes.ChangeState.changed, {"marker": "120"})
        async def fail_pull(*args):
            async with self.factory() as db:
                current = await db.get(SyncJob, job.id)
                current.status = SyncStatus.completed
                current.stats = {"provider_snapshot_complete": False}
                await db.commit()
        with patch.object(account_sync, "async_sessionmaker", return_value=self.factory), \
             patch.object(account_sync, "check_provider_changes", AsyncMock(return_value=checked)), \
             patch.dict(account_sync.SERVER_RUNNERS, {"plex": fail_pull}):
            await account_sync._run_provider(self.user.id, job.id)
        async with self.factory() as db:
            current = await db.get(SyncJob, job.id)
            self.assertNotIn("provider_checkpoint", current.stats or {})

    async def test_worker_skips_only_verified_unchanged_and_checkpoints_only_success(self):
        async with self.factory() as db:
            conn = (await db.execute(select(MediaServerConnection).where(
                MediaServerConnection.user_id == self.user.id))).scalar_one()
            conn_id = conn.id
        for state in provider_changes.ChangeState:
            with self.subTest(state=state):
                async with self.factory() as db:
                    job = SyncJob(user_id=self.user.id, connection_id=conn_id, source=CollectionSource.plex,
                                  job_type="pull", status=SyncStatus.pending)
                    db.add(job)
                    await db.commit()
                checkpoint = None if state == provider_changes.ChangeState.unknown else {"marker": "120"}
                checked = provider_changes.ChangeCheck(state, checkpoint)
                async def complete(*args):
                    async with self.factory() as db:
                        current = await db.get(SyncJob, job.id)
                        current.status = SyncStatus.completed
                        await db.commit()
                runner = AsyncMock(side_effect=complete)
                with patch.object(account_sync, "async_sessionmaker", return_value=self.factory), \
                     patch.object(account_sync, "check_provider_changes", AsyncMock(return_value=checked)), \
                     patch.dict(account_sync.SERVER_RUNNERS, {"plex": runner}):
                    await account_sync._run_provider(self.user.id, job.id)
                self.assertEqual(runner.await_count, 0 if state == provider_changes.ChangeState.unchanged else 1)
                async with self.factory() as db:
                    finished = await db.get(SyncJob, job.id)
                    self.assertEqual(finished.status, SyncStatus.completed)
                    self.assertEqual((finished.stats or {}).get("provider_checkpoint"), checkpoint)
