"""Startup recovery and bounded statistics backlog scheduling."""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'statistics-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core import statistics_snapshots as snapshots


class StatisticsSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_prepares_before_work_and_drains_full_batches(self):
        calls = []

        async def prepare():
            calls.append('prepare')

        async def run():
            calls.append('run')
            return 4 if calls.count('run') == 1 else 0

        async def sleep(delay):
            calls.append(delay)
            if delay == 30:
                raise asyncio.CancelledError

        with patch.object(snapshots, 'prepare_startup', prepare), patch.object(snapshots, 'run_due', run), patch.object(snapshots.asyncio, 'sleep', sleep):
            with self.assertRaises(asyncio.CancelledError):
                await snapshots.scheduler()
        self.assertEqual(calls, ['prepare', 'run', 0, 'run', 30])

    async def test_startup_database_failure_retries_preparation(self):
        prepare = AsyncMock(side_effect=[RuntimeError('database restarting'), None])
        run = AsyncMock(return_value=0)
        sleep = AsyncMock(side_effect=[None, asyncio.CancelledError])
        with patch.object(snapshots, 'prepare_startup', prepare), patch.object(snapshots, 'run_due', run), patch.object(snapshots.asyncio, 'sleep', sleep):
            with self.assertRaises(asyncio.CancelledError):
                await snapshots.scheduler()
        self.assertEqual(prepare.await_count, 2)
        run.assert_awaited_once()
