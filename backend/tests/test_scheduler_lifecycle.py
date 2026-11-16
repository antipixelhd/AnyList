import asyncio
import os
import unittest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "scheduler-lifecycle-tests")

from core.scheduler import running_jobs


class SchedulerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_failure_cancels_siblings_and_reaches_owner(self):
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def companion():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        async def broken():
            await started.wait()
            raise RuntimeError("scheduler fixture failure")

        with self.assertRaisesRegex(RuntimeError, "scheduler fixture failure"):
            async with running_jobs(companion, broken):
                await asyncio.Event().wait()
        self.assertTrue(cleaned.is_set())

    async def test_shutdown_waits_for_scheduler_cleanup(self):
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def scheduler():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        async with running_jobs(scheduler):
            await started.wait()
        self.assertTrue(cleaned.is_set())
