"""Sync status and lifecycle regressions against disposable PostgreSQL."""
import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "local-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from core.sync_jobs import SyncCancelled, mark_job_running_unless_cancelled, raise_if_cancelled
from db import get_db
from dependencies import get_current_user_or_api_key
from models.base import CollectionSource
from models.sync import SyncJob, SyncStatus
from models.users import User
from routers.sync import router


@unittest.skipUnless(os.getenv("TRACKING_TEST_DATABASE_URL"), "Requires disposable PostgreSQL database")
class SyncStatusApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TRACKING_TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.db = AsyncSession(bind=self.connection, expire_on_commit=False)
        self.owner = User(email="sync-owner@test.invalid", username="sync-owner", api_key="sync-owner-test")
        self.other = User(email="sync-other@test.invalid", username="sync-other", api_key="sync-other-test")
        self.db.add_all([self.owner, self.other])
        await self.db.flush()

        async def session():
            yield self.db

        app = FastAPI()
        app.include_router(router, prefix="/sync")
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user_or_api_key] = lambda: self.owner
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.db.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    def job(self, owner, status, created_at, **values):
        job = SyncJob(user_id=owner.id, source=CollectionSource.trakt, status=status,
                      created_at=created_at, updated_at=created_at, **values)
        self.db.add(job)
        return job

    async def status(self):
        await self.db.flush()
        response = await self.client.get("/sync/status")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_old_active_jobs_survive_recent_history_and_other_users_are_isolated(self):
        now = datetime(2026, 9, 30, 12)
        running = self.job(self.owner, SyncStatus.running, now - timedelta(days=2))
        queued = self.job(self.owner, SyncStatus.pending, now - timedelta(days=1))
        history = [self.job(self.owner, SyncStatus.completed, now + timedelta(seconds=i // 2)) for i in range(25)]
        # Other users' newer history must neither appear nor occupy this user's window.
        self.job(self.other, SyncStatus.running, now + timedelta(days=1))
        for i in range(30):
            self.job(self.other, SyncStatus.failed, now + timedelta(days=2, seconds=i))

        result = await self.status()
        expected = [job.id for job in reversed(history[-20:])] + [queued.id, running.id]
        self.assertEqual([job["id"] for job in result], expected)
        self.assertEqual({job["user_id"] for job in result}, {self.owner.id})
        self.assertEqual(result[-1]["status"], "running")
        self.assertEqual(result[-2]["status"], "pending")

    async def test_active_jobs_are_not_capped_at_twenty(self):
        now = datetime(2026, 9, 30, 12)
        active = [self.job(self.owner, SyncStatus.running, now + timedelta(seconds=i)) for i in range(30)]
        result = await self.status()
        self.assertEqual([job["id"] for job in result], [job.id for job in reversed(active)])

    async def test_old_job_completion_remains_visible_after_newer_jobs_fill_the_window(self):
        now = datetime(2026, 9, 30, 12)
        old = self.job(self.owner, SyncStatus.running, now - timedelta(days=2))
        history = [self.job(self.owner, SyncStatus.completed, now + timedelta(seconds=i)) for i in range(25)]
        await self.db.flush()
        old.status = SyncStatus.completed
        old.updated_at = now + timedelta(minutes=1)
        result = await self.status()
        self.assertEqual([job["id"] for job in result], [job.id for job in reversed(history[-19:])] + [old.id])
        self.assertEqual(result[-1]["status"], "completed")

    async def test_guarded_start_preserves_cancellation_and_persists_worker_progress(self):
        now = datetime(2026, 9, 30, 12)
        cancelled = self.job(self.owner, SyncStatus.cancelled, now, error_message="Cancelled by user")
        queued = self.job(self.owner, SyncStatus.pending, now, processed_items=9)
        await self.db.flush()

        self.assertFalse(await mark_job_running_unless_cancelled(self.db, cancelled.id, processed_items=0))
        with self.assertRaises(SyncCancelled):
            await raise_if_cancelled(self.db, cancelled.id)
        self.assertTrue(await mark_job_running_unless_cancelled(self.db, queued.id, processed_items=0, current_step="Pulling history"))
        await raise_if_cancelled(self.db, queued.id)
        await self.db.refresh(cancelled)
        await self.db.refresh(queued)

        result = {job["id"]: job for job in await self.status()}
        self.assertEqual(result[cancelled.id]["status"], "cancelled")
        self.assertEqual(result[cancelled.id]["error_message"], "Cancelled by user")
        self.assertEqual(result[queued.id]["status"], "running")
        self.assertEqual(result[queued.id]["processed_items"], 0)
        self.assertEqual(result[queued.id]["current_step"], "Pulling history")
