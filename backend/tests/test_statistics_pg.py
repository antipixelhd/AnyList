"""Real PostgreSQL scheduling, invalidation and API tests in disposable schemas."""

import asyncio
import importlib
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "statistics-test-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core import statistics_snapshots as snapshots
from db import get_db
from dependencies import get_optional_user
from models import Base, GlobalSettings, Media, User, UserProfileData, WatchEvent
from models.base import MediaType, PrivacyLevel
from models.statistics import UserStatsSnapshot, UserStatsState
from models.tracking import TrackedEntry
from routers.tracking import router

URL = os.getenv("STATISTICS_TEST_DATABASE_URL") or os.getenv("TRACKING_TEST_DATABASE_URL")


@unittest.skipUnless(URL, "Requires explicit disposable statistics PostgreSQL")
class StatisticsDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not (":55449/catalogue_test" in URL or "/anylist_test" in URL):
            raise RuntimeError("Refusing non-disposable statistics DB")
        self.schema = "statistics_test_" + uuid.uuid4().hex
        self.admin = create_async_engine(URL)
        async with self.admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(URL, connect_args={"server_settings": {"search_path": self.schema}})

        def setup(conn):
            wanted = [t for t in Base.metadata.sorted_tables if not t.name.startswith("user_stats_") and t.name != "stats_metadata_revision"]
            Base.metadata.create_all(conn, tables=wanted)
            with Operations.context(MigrationContext.configure(conn)):
                importlib.import_module("migrations.versions.mt039_statistics_snapshots").upgrade()
                importlib.import_module("migrations.versions.mt040_statistics_utc_schedule").upgrade()

        async with self.engine.begin() as conn:
            await conn.run_sync(setup)
            precision = await conn.scalars(text("SELECT DISTINCT datetime_precision FROM information_schema.columns WHERE table_schema=current_schema() AND table_name IN ('user_stats_state', 'user_stats_snapshots') AND data_type LIKE 'timestamp%'"))
            self.assertEqual(list(precision), [3])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            user = User(email="stats@example.org", username="statistics-fixture", api_key="fixture-key")
            db.add(user)
            db.add(GlobalSettings(id=1, show_anime=True, enable_logged_out_navigation=True))
            await db.flush()
            db.add(UserProfileData(user_id=user.id, privacy_level=PrivacyLevel.public))
            movie = Media(title="Fixture movie", media_type=MediaType.movie, runtime=100, release_date="2020-01-01")
            db.add(movie)
            await db.flush()
            db.add(TrackedEntry(user_id=user.id, media_id=movie.id, status="completed", rating_mode="manual", manual_score=8))
            db.add(WatchEvent(user_id=user.id, media_id=movie.id, completed=True, watched_at=datetime(2026, 1, 1)))
            await db.commit()
            self.user_id, self.media_id = user.id, movie.id
        app = FastAPI()
        app.include_router(router, prefix="/tracking")

        async def db_override():
            async with self.Session() as db:
                yield db

        self.viewer = None
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[get_optional_user] = lambda: self.viewer
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        async with self.admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin.dispose()

    async def build(self):
        claim = await snapshots.claim_next(self.Session)
        self.assertIsNotNone(claim)
        result = await snapshots.compute(claim, self.Session)
        self.assertTrue(await snapshots.publish(claim, result, self.Session))
        return claim, result

    async def force_due(self):
        async with self.Session() as db:
            await db.execute(update(UserStatsState).where(UserStatsState.user_id == self.user_id).values(next_due_at=snapshots.utcnow() - timedelta(seconds=1)))
            await db.commit()

    async def state(self):
        async with self.Session() as db:
            return await db.get(UserStatsState, self.user_id)

    async def test_runtime_backfill_deduplicates_history_and_refreshes_statistics(self):
        from core.runtime_backfill import backfill_history_runtimes
        async with self.Session() as db:
            movie = await db.get(Media, self.media_id)
            movie.runtime, movie.tmdb_id = None, 10
            db.add(WatchEvent(user_id=self.user_id, media_id=movie.id, completed=True, play_count=3))
            unwatched = Media(title="Incomplete watch", media_type=MediaType.movie, tmdb_id=11)
            db.add(unwatched)
            await db.flush()
            db.add(WatchEvent(user_id=self.user_id, media_id=unwatched.id, completed=False))
            await db.commit()
        _, before = await self.build()
        self.assertEqual(before["payload"]["all"]["coverage"]["runtime_missing_plays"], 4)
        async with self.Session() as db:
            with patch("core.runtime_backfill.tmdb.get_movie", AsyncMock(return_value={"id": 10, "runtime": 120})) as fetch:
                result = await backfill_history_runtimes(db, "fixture-key", limit=1)
            fetch.assert_awaited_once()
            self.assertEqual(result["recovered"], 1)
            self.assertIsNone((await db.get(Media, unwatched.id)).runtime)
            events = (await db.scalars(select(WatchEvent).where(WatchEvent.media_id == self.media_id))).all()
            self.assertEqual(sum(e.play_count for e in events), 4)
        await self.force_due()
        _, after = await self.build()
        self.assertGreater(after["metadata_revision"], before["metadata_revision"])
        self.assertEqual(after["payload"]["all"]["coverage"]["runtime_missing_plays"], 0)
        self.assertEqual(after["payload"]["all"]["totals"]["watch_minutes"], 480)

    async def test_first_request_is_pending_and_worker_publishes_all_scopes_together(self):
        response = await self.client.get("/tracking/profile/statistics-fixture/stats/overview")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.json()["status"], "pending")
        self.assertIsNone(response.json()["overview"])
        await self.build()
        generations = set()
        for scope, titles in (("all", 1), ("movie", 1), ("series", 0)):
            r = await self.client.get("/tracking/profile/statistics-fixture/stats/overview", params={"media_type": scope})
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            generations.add(data["generation"])
            self.assertEqual(data["overview"]["totals"]["watched_titles"], titles)
        self.assertEqual(len(generations), 1)
        self.assertEqual((await self.client.get("/tracking/profile/statistics-fixture/stats/overview", params={"media_type": "game"})).status_code, 422)

    async def test_two_workers_cannot_claim_same_job(self):
        claims = await asyncio.gather(snapshots.claim_next(self.Session), snapshots.claim_next(self.Session))
        self.assertEqual(sum(c is not None for c in claims), 1)

    async def test_non_utc_database_timezone_does_not_delay_initial_or_purge_work(self):
        await self.build()
        async with self.Session() as db:
            await db.execute(text("SET LOCAL TIME ZONE 'Europe/Berlin'"))
            other = User(email="timezone@example.org", username="timezone", api_key="timezone-fixture")
            db.add(other)
            await db.flush()
            state = await db.get(UserStatsState, other.id)
            self.assertLess(abs((state.next_due_at - snapshots.utcnow()).total_seconds()), 5)
            await db.execute(delete(WatchEvent).where(WatchEvent.user_id == self.user_id))
            await db.commit()
        self.assertLess(abs(((await self.state()).next_due_at - snapshots.utcnow()).total_seconds()), 5)

    async def test_expired_worker_cannot_publish_after_lease_reassignment(self):
        first = await snapshots.claim_next(self.Session)
        result = await snapshots.compute(first, self.Session)
        async with self.Session() as db:
            await db.execute(update(UserStatsState).values(lease_until=snapshots.utcnow() - timedelta(seconds=1)))
            await db.commit()
        second = await snapshots.claim_next(self.Session)
        self.assertNotEqual(first["token"], second["token"])
        self.assertFalse(await snapshots.publish(first, result, self.Session))
        self.assertTrue(await snapshots.publish(second, await snapshots.compute(second, self.Session), self.Session))

    async def test_fact_revision_race_keeps_dirty_and_does_not_publish_stale_snapshot(self):
        claim = await snapshots.claim_next(self.Session)
        result = await snapshots.compute(claim, self.Session)
        async with self.Session() as db:
            await db.execute(update(TrackedEntry).values(manual_score=6))
            await db.commit()
        self.assertFalse(await snapshots.publish(claim, result, self.Session))
        state = await self.state()
        self.assertTrue(state.dirty)
        self.assertIsNone(state.active_snapshot_id)

    async def test_shared_metadata_revision_rejects_stale_computation(self):
        claim = await snapshots.claim_next(self.Session)
        result = await snapshots.compute(claim, self.Session)
        async with self.Session() as db:
            await db.execute(update(Media).where(Media.id == self.media_id).values(runtime=120))
            await db.commit()
        self.assertFalse(await snapshots.publish(claim, result, self.Session))
        self.assertTrue((await self.state()).dirty)

    async def test_completion_removal_invalidates_immediately(self):
        await self.build()
        async with self.Session() as db:
            await db.execute(update(WatchEvent).where(WatchEvent.user_id == self.user_id).values(completed=False))
            await db.commit()
        self.assertIsNone((await self.state()).active_snapshot_id)

    async def test_failed_build_retains_last_good_generation_and_retries(self):
        await self.build()
        previous = (await self.state()).active_snapshot_id
        async with self.Session() as db:
            await db.execute(update(TrackedEntry).values(manual_score=6))
            await db.commit()
        await self.force_due()
        claim = await snapshots.claim_next(self.Session)
        await snapshots.record_failure(claim, self.Session)
        state = await self.state()
        self.assertEqual(state.active_snapshot_id, previous)
        self.assertEqual(state.error_code, "calculation_failed")
        self.assertGreater(state.next_due_at, snapshots.utcnow())
        self.assertIsNone(state.lease_token)

    async def test_unchanged_due_check_skips_calculation_without_new_compute_time(self):
        await self.build()
        previous = (await self.state()).active_snapshot_id
        await self.force_due()
        self.assertEqual(await snapshots.claim_next(self.Session), {"skipped": True})
        self.assertEqual((await self.state()).active_snapshot_id, previous)

    async def test_bulk_sensitive_removal_purges_snapshot_immediately(self):
        await self.build()
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.user_id == self.user_id))
            await db.commit()
        self.assertIsNone((await self.state()).active_snapshot_id)
        async with self.Session() as db:
            self.assertEqual(list(await db.scalars(select(UserStatsSnapshot))), [])
        response = await self.client.get("/tracking/profile/statistics-fixture/stats/overview")
        self.assertEqual(response.json()["status"], "pending")

    async def test_rolled_back_fact_change_does_not_invalidate_published_generation(self):
        await self.build()
        before = await self.state()
        async with self.Session() as db:
            await db.execute(delete(WatchEvent))
            await db.rollback()
        after = await self.state()
        self.assertEqual(after.source_revision, before.source_revision)
        self.assertEqual(after.active_snapshot_id, before.active_snapshot_id)

    async def test_private_profile_access_changes_immediately_for_cached_data(self):
        await self.build()
        async with self.Session() as db:
            await db.execute(update(UserProfileData).values(privacy_level=PrivacyLevel.private))
            await db.commit()
            self.viewer = await db.get(User, self.user_id)
        self.assertEqual((await self.client.get("/tracking/profile/statistics-fixture/stats/overview")).status_code, 200)
        self.viewer = None
        self.assertEqual((await self.client.get("/tracking/profile/statistics-fixture/stats/overview")).status_code, 403)

    async def test_visibility_change_invalidates_existing_generation(self):
        await self.build()
        async with self.Session() as db:
            await db.execute(update(GlobalSettings).values(show_anime=False))
            await db.commit()
        self.assertIsNone((await self.state()).active_snapshot_id)

    async def test_snapshot_pointer_rejects_another_users_generation(self):
        await self.build()
        previous = (await self.state()).active_snapshot_id
        async with self.Session() as db:
            other = User(email="other@example.org", username="other", api_key="other-key")
            db.add(other)
            await db.flush()
            await db.execute(update(UserStatsState).where(UserStatsState.user_id == other.id).values(active_snapshot_id=previous))
            with self.assertRaises(IntegrityError):
                await db.commit()

    async def test_user_deletion_cleans_state_and_snapshots(self):
        await self.build()
        async with self.Session() as db:
            await db.execute(delete(User).where(User.id == self.user_id))
            await db.commit()
            self.assertIsNone(await db.get(UserStatsState, self.user_id))
            self.assertEqual(list(await db.scalars(select(UserStatsSnapshot))), [])
