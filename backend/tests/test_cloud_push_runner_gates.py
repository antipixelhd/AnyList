import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost/test",
)

from routers import mdblist, simkl, trakt


class _Result:
    def scalar_one_or_none(self):
        return None


class _Session:
    def __init__(self) -> None:
        self.execute = AsyncMock(return_value=_Result())
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class CloudPushRunnerGateTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_runner_is_gated(self, module, runner, factory_name: str) -> None:
        session = _Session()
        gate = AsyncMock(
            side_effect=HTTPException(
                status_code=409,
                detail="Review the first import before exporting",
            )
        )

        with (
            patch.object(module, factory_name, return_value=lambda: session),
            patch.object(module, "require_cloud_reconciliation", gate),
        ):
            await runner(user_id=7, job_id=19)

        gate.assert_awaited_once_with(session, 7, module.__name__.split(".")[-1])
        # The only database write after denial records the failed job; no settings
        # or payload query can run before reconciliation is approved.
        self.assertEqual(session.execute.await_count, 1)
        session.commit.assert_awaited_once()

    async def test_trakt_background_runner_cannot_bypass_reconciliation(self) -> None:
        await self._assert_runner_is_gated(
            trakt,
            trakt._run_trakt_push,
            "async_sessionmaker",
        )

    async def test_simkl_background_runner_cannot_bypass_reconciliation(self) -> None:
        await self._assert_runner_is_gated(
            simkl,
            simkl._run_simkl_push,
            "async_sessionmaker",
        )

    async def test_mdblist_background_runner_cannot_bypass_reconciliation(self) -> None:
        await self._assert_runner_is_gated(
            mdblist,
            mdblist.run_mdblist_push,
            "async_sessionmaker",
        )
