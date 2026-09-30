"""Dependency direction and background task ownership regressions."""

import ast
import asyncio
import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import scheduler


class DependencyDirectionTests(unittest.TestCase):
    def test_application_services_and_routers_do_not_import_routers(self):
        root = Path(__file__).resolve().parents[1]
        violations = []
        for folder in ("core", "routers"):
            for path in (root / folder).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    modules = []
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        modules = [node.module or ""]
                        if node.level and folder == "routers":
                            modules = ["routers"]
                    if any(module == "routers" or module.startswith("routers.") for module in modules):
                        violations.append(f"{path.relative_to(root)}:{node.lineno}")
        self.assertEqual(violations, [], "Router composition belongs in main.py")


class SchedulerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def job_patches(self, started, stopped, fail=False):
        names = (
            "_auto_sync_scheduler", "_stream_action_retry_scheduler",
            "_rating_push_scheduler", "_watchlist_poller", "_manual_session_completer",
            "_emby_progress_poller", "_show_metadata_refresher",
        )
        stack = ExitStack()
        for index, name in enumerate(names):
            async def job(name=name, index=index):
                started.add(name)
                try:
                    if fail and index == 0:
                        raise RuntimeError("job failed")
                    await asyncio.Event().wait()
                finally:
                    stopped.add(name)
            stack.enter_context(patch.object(scheduler, name, job))
        return stack

    async def test_all_jobs_start_and_finish_before_lifespan_exits(self):
        started, stopped = set(), set()
        with self.job_patches(started, stopped):
            async with scheduler.background_jobs():
                await asyncio.sleep(0)
                self.assertEqual(len(started), 7)
                self.assertEqual(stopped, set())
        self.assertEqual(stopped, started)

    async def test_lifespan_failure_still_cleans_up_jobs(self):
        started, stopped = set(), set()
        with self.job_patches(started, stopped):
            with self.assertRaisesRegex(ValueError, "startup failed"):
                async with scheduler.background_jobs():
                    await asyncio.sleep(0)
                    raise ValueError("startup failed")
        self.assertEqual(len(stopped), 7)

    async def test_job_failure_is_reported_after_all_jobs_are_cleaned_up(self):
        started, stopped = set(), set()
        with self.job_patches(started, stopped, fail=True):
            with self.assertRaisesRegex(RuntimeError, "job failed"):
                async with scheduler.background_jobs():
                    await asyncio.sleep(0)
        self.assertEqual(len(stopped), 7)
