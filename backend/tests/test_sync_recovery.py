"""Transaction, cancellation and restart boundaries of account reconciliation."""
import json
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core import account_sync, netflix_sessions, scheduler, server_sync, sync_delivery
from core.pull_cycle import PullCycleState
from core.sync_reconciliation import Reconciliation, _current, finalize, initialize
from models import Collection, CollectionFile, Media, MediaServerConnection, NetflixImportSession, User, UserSettings
from models.base import CollectionSource, MediaType
from models.sync import SyncJob, SyncStatus


class DeliveryEncodingTests(unittest.TestCase):
    def test_round_trip_preserves_per_title_exclusions_without_credentials(self):
        state = PullCycleState(7, new_ratings={(1, None): 8, (2, 3): 9},
            rating_sources={(1, None): {"trakt"}},
            watched_exclusions={1: ({5}, {CollectionSource.trakt})},
            library_observed_at_by_media={1: datetime(2026, 10, 5)}, library_api_key="secret")
        payload = json.loads(json.dumps(sync_delivery.delivery_payload(state)))
        recovered = PullCycleState(**{key: sync_delivery._decode(value) for key, value in payload.items()})
        self.assertEqual(recovered.new_ratings, state.new_ratings)
        self.assertEqual(recovered.watched_exclusions, state.watched_exclusions)
        self.assertEqual(recovered.library_observed_at_by_media, state.library_observed_at_by_media)
        self.assertNotIn("secret", json.dumps(payload))


@unittest.skipUnless(os.getenv("TRACKING_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class SyncRecoveryDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TRACKING_TEST_DATABASE_URL"])
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.factory() as db:
            self.user = User(email="recovery@test.invalid", username="recovery", api_key="recovery-fixture")
            self.media = Media(tmdb_id=7600001, media_type=MediaType.movie, title="Recovery fixture")
            db.add_all([self.user, self.media])
            await db.flush()
            self.conn = MediaServerConnection(user_id=self.user.id, type="stremio", name="Fixture",
                                             url="http://invalid", token="fixture")
            db.add_all([self.conn, UserSettings(user_id=self.user.id)])
            await db.commit()
        with patch.object(account_sync.settings_store, "get_effective_tmdb_key", AsyncMock(return_value="fixture")):
            async with self.factory() as db:
                self.job = await account_sync.queue_account_pull(db, self.user.id)

    async def asyncTearDown(self):
        from sqlalchemy import delete
        async with self.factory() as db:
            await db.execute(delete(User).where(User.id == self.user.id))
            await db.execute(delete(Media).where(Media.id == self.media.id))
            await db.commit()
        await self.engine.dispose()

    async def staged_library(self):
        state = Reconciliation(self.user.id)
        async with self.factory() as db:
            await initialize(db, state)
        state.collection_files[(self.conn.id, self.media.id, "item")] = (
            self.conn.id, self.media.id, "item", CollectionSource.stremio, None, {})
        return state

    async def test_membership_and_delivery_commit_together_and_recover_after_restart(self):
        state = await self.staged_library()
        cycle = PullCycleState(self.user.id)
        async with self.factory() as db:
            self.assertIsNone((await db.execute(select(Collection))).scalar_one_or_none())
            await finalize(db, state, cycle, job_id=self.job.id)
            job = await db.get(SyncJob, self.job.id)
            job.status = SyncStatus.failed  # Simulate restart before delivery.
            await db.commit()
        with patch.object(scheduler, "_flush_pull_cycle", AsyncMock(side_effect=RuntimeError("offline"))):
            await sync_delivery.retry_cycle_deliveries(self.factory)
        async with self.factory() as db:
            job = await db.get(SyncJob, self.job.id)
            self.assertTrue(job.stats["delivery_pending"])
            self.assertIsNotNone((await db.execute(select(Collection).where(Collection.user_id == self.user.id))).scalar_one())
        with patch.object(scheduler, "_flush_pull_cycle", AsyncMock()) as flush:
            await sync_delivery.retry_cycle_deliveries(self.factory)
            await sync_delivery.retry_cycle_deliveries(self.factory)
        flush.assert_awaited_once()
        self.assertEqual(flush.call_args.args[0].user_id, self.user.id)
        async with self.factory() as db:
            self.assertNotIn("delivery_pending", (await db.get(SyncJob, self.job.id)).stats)

    async def test_failed_handoff_persistence_rolls_back_library_as_well(self):
        state = await self.staged_library()
        async with self.factory() as db:
            with patch.object(sync_delivery, "persist_delivery", AsyncMock(side_effect=RuntimeError("db failed"))):
                with self.assertRaises(RuntimeError):
                    await finalize(db, state, PullCycleState(self.user.id), job_id=self.job.id)
            await db.rollback()
        async with self.factory() as db:
            self.assertFalse((await db.execute(select(Collection).where(Collection.user_id == self.user.id))).first())
            self.assertFalse((await db.get(SyncJob, self.job.id)).stats.get("delivery_pending"))

    async def test_two_staged_sources_merge_membership_and_earliest_add_date(self):
        async with self.factory() as db:
            second = MediaServerConnection(user_id=self.user.id, type="stremio", name="Second",
                                           url="http://invalid", token="second-fixture")
            db.add(second)
            await db.commit()
        state = Reconciliation(self.user.id)
        async with self.factory() as db:
            await initialize(db, state)
        earliest = datetime(2020, 1, 1)
        for conn, added_at in ((self.conn, None), (second, earliest)):
            source_id = f"{conn.id}:item"
            state.collection_files[(conn.id, self.media.id, source_id)] = (
                conn.id, self.media.id, source_id, CollectionSource.stremio, added_at, {})
        async with self.factory() as db:
            await finalize(db, state, PullCycleState(self.user.id), job_id=self.job.id)
        async with self.factory() as db:
            collection = (await db.execute(select(Collection).where(Collection.user_id == self.user.id))).scalar_one()
            self.assertEqual(collection.added_at, earliest)
            self.assertEqual(len((await db.execute(select(CollectionFile).where(
                CollectionFile.collection_id == collection.id))).scalars().all()), 2)

    async def test_fetching_stages_additions_and_removals_without_changing_membership(self):
        state = await self.staged_library()
        state.collection_files.clear()
        token = _current.set(state)
        try:
            async with self.factory() as db:
                # Metadata already exists; no external lookup is needed.
                await server_sync.sync_items([
                    {"Id": "item", "Name": self.media.title, "ProviderIds": {"Tmdb": str(self.media.tmdb_id)}},
                ], MediaType.movie, CollectionSource.stremio, db,
                    {"skipped": 0, "errors": 0}, self.user.id, sync_watched=False,
                    sync_ratings=False, connection_id=self.conn.id)
                await server_sync._remove_stream_collection_sources(db, self.user.id, self.conn.id,
                    source=CollectionSource.stremio, removed_ids=set(), complete_snapshot_source_ids=set())
                await db.commit()
            async with self.factory() as db:
                self.assertFalse((await db.execute(select(Collection).where(Collection.user_id == self.user.id))).first())
            self.assertTrue(state.collection_files)
            self.assertTrue(state.collection_removals)
        finally:
            _current.reset(token)

    async def test_cancel_is_rejected_after_reconciliation_window_closes(self):
        from fastapi import HTTPException
        async with self.factory() as db:
            job = await db.get(SyncJob, self.job.id)
            job.status = SyncStatus.running
            job.stats = {**job.stats, "phase": "reconciling"}
            await db.commit()
        async with self.factory() as db:
            job = await db.get(SyncJob, self.job.id)
            with self.assertRaises(HTTPException) as error:
                await account_sync.request_cycle_cancel(db, job)
            self.assertEqual(error.exception.status_code, 409)
            await db.rollback()
        async with self.factory() as db:
            self.assertFalse((await db.get(SyncJob, self.job.id)).stats.get("cancel_requested"))
        from routers.sync import abort_sync
        async with self.factory() as db:
            await abort_sync(db, self.user)
        async with self.factory() as db:
            parent = await db.get(SyncJob, self.job.id)
            self.assertEqual(parent.status, SyncStatus.running)
            for child_id in parent.stats["child_job_ids"]:
                self.assertEqual((await db.get(SyncJob, child_id)).status, SyncStatus.pending)

    async def test_recovery_drops_rating_superseded_by_a_local_edit(self):
        from models import Rating
        cycle = PullCycleState(self.user.id, new_ratings={(self.media.id, None): 8},
                               rating_sources={(self.media.id, None): {"trakt"}})
        async with self.factory() as db:
            db.add(Rating(user_id=self.user.id, media_id=self.media.id, rating=9))
            await sync_delivery.persist_delivery(db, self.job.id, cycle)
            (await db.get(SyncJob, self.job.id)).status = SyncStatus.failed
            await db.commit()
        with patch("db.async_sessionmaker", return_value=self.factory), \
             patch("core.outbound_sync.fan_out_changes", AsyncMock()) as fanout, \
             patch("core.stream_actions.dispatch_stream_actions", AsyncMock()), \
             patch("core.cloud_actions.dispatch_cloud_actions", AsyncMock()):
            await sync_delivery.retry_cycle_deliveries(self.factory)
        self.assertTrue(all(not call.args[4] for call in fanout.await_args_list))
        async with self.factory() as db:
            self.assertNotIn("delivery_pending", (await db.get(SyncJob, self.job.id)).stats)

    async def test_removal_stays_visible_until_reconciliation_commits(self):
        async with self.factory() as db:
            collection = Collection(user_id=self.user.id, media_id=self.media.id)
            db.add(collection)
            await db.flush()
            db.add(CollectionFile(collection_id=collection.id, connection_id=self.conn.id,
                                  source=CollectionSource.stremio, source_id="item"))
            await db.commit()
        state = Reconciliation(self.user.id)
        async with self.factory() as db:
            await initialize(db, state)
        token = _current.set(state)
        try:
            async with self.factory() as db:
                await server_sync._remove_stream_collection_sources(db, self.user.id, self.conn.id,
                    source=CollectionSource.stremio, removed_ids=set(), complete_snapshot_source_ids=set())
                await db.commit()
            async with self.factory() as db:
                self.assertTrue((await db.execute(select(Collection).where(Collection.user_id == self.user.id))).first())
                await finalize(db, state, PullCycleState(self.user.id), job_id=self.job.id)
            async with self.factory() as db:
                self.assertFalse((await db.execute(select(Collection).where(Collection.user_id == self.user.id))).first())
        finally:
            _current.reset(token)

    async def test_cancelled_cycle_discards_staged_membership(self):
        async def worker(user_id, child_id):
            from core.sync_reconciliation import collecting
            state = collecting(user_id)
            state.collection_files[(self.conn.id, self.media.id, "item")] = (
                self.conn.id, self.media.id, "item", CollectionSource.stremio, None, {})
            async with self.factory() as db:
                await account_sync.request_cycle_cancel(db, await db.get(SyncJob, self.job.id))
        with patch.object(account_sync, "async_sessionmaker", return_value=self.factory), \
             patch.object(account_sync, "_run_provider", worker):
            await account_sync.run_account_pull(self.user.id, self.job.id)
        async with self.factory() as db:
            self.assertEqual((await db.get(SyncJob, self.job.id)).status, SyncStatus.cancelled)
            self.assertFalse((await db.execute(select(Collection).where(Collection.user_id == self.user.id))).first())

    async def test_delivery_failure_retains_checkpoint_for_accepted_provider_snapshot(self):
        async def worker(user_id, child_id):
            async with self.factory() as db:
                child = await db.get(SyncJob, child_id)
                child.status = SyncStatus.completed
                child.stats = {"provider_pending_checkpoint": {"cursor": "fixture"}}
                await db.commit()
        with patch.object(account_sync, "async_sessionmaker", return_value=self.factory), \
             patch.object(account_sync, "_run_provider", worker), \
             patch.object(scheduler, "_flush_pull_cycle", AsyncMock(side_effect=RuntimeError("offline"))):
            await account_sync.run_account_pull(self.user.id, self.job.id)
        async with self.factory() as db:
            parent = await db.get(SyncJob, self.job.id)
            self.assertEqual(parent.status, SyncStatus.failed)
            self.assertTrue(parent.stats["delivery_pending"])
            child = await db.get(SyncJob, parent.stats["child_job_ids"][0])
            self.assertEqual(child.stats["provider_checkpoint"], {"cursor": "fixture"})

    async def test_netflix_committed_handoff_survives_busy_cycle_and_restart(self):
        async with self.factory() as db:
            db.add(NetflixImportSession(id="recovery-import", user_id=self.user.id, status="committed",
                result={"pull_pending": True}, expires_at=datetime.utcnow()+timedelta(days=1)))
            await db.commit()
        async def complete(user_id, job_id):
            async with self.factory() as db:
                job = await db.get(SyncJob, job_id)
                job.status = SyncStatus.completed
                await db.commit()
        with patch.object(netflix_sessions, "async_sessionmaker", return_value=self.factory), \
             patch.object(account_sync.settings_store, "get_effective_tmdb_key", AsyncMock(return_value="fixture")), \
             patch.object(account_sync, "run_account_pull", complete):
            await netflix_sessions.retry_committed_import_pulls()  # Busy account.
            async with self.factory() as db:
                self.assertTrue((await db.get(NetflixImportSession, "recovery-import")).result["pull_pending"])
                job = await db.get(SyncJob, self.job.id)
                job.status = SyncStatus.failed
                for child_id in job.stats["child_job_ids"]:
                    (await db.get(SyncJob, child_id)).status = SyncStatus.failed
                await db.commit()
            await netflix_sessions.retry_committed_import_pulls()
            await netflix_sessions.retry_committed_import_pulls()
        async with self.factory() as db:
            self.assertFalse((await db.get(NetflixImportSession, "recovery-import")).result["pull_pending"])
