from __future__ import annotations

import unittest
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "local-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _JobResult:
    def __init__(self, job):
        self.job = job

    def scalar_one_or_none(self):
        return self.job


class TrackingDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_stream_action_error_fails_receipt_with_safe_metadata(self):
        from core.tracking_delivery import finish_tracking_delivery_job

        job = SimpleNamespace(
            state="dispatching",
            changes={"stream_actions": [{"id": 12}, {"id": 13}]},
            detail=None,
            updated_at=None,
        )
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[
                _JobResult(job),
                _RowsResult([
                    ("pending", "TimeoutError"),
                    ("pending", "TimeoutError: bearer-secret"),
                ]),
            ]),
            commit=AsyncMock(),
        )
        factory = lambda: _SessionContext(db)

        with patch("db.async_sessionmaker", return_value=factory):
            await finish_tracking_delivery_job(4)

        self.assertEqual(job.state, "failed")
        self.assertEqual(
            job.detail,
            "One or more connected provider actions failed (ProviderError, TimeoutError).",
        )
        self.assertNotIn("bearer-secret", job.detail)
        db.commit.assert_awaited_once()

    async def test_pending_stream_action_keeps_receipt_queued(self):
        from core.tracking_delivery import finish_tracking_delivery_job

        job = SimpleNamespace(
            state="dispatching",
            changes={"stream_actions": [{"id": 12}]},
            detail=None,
            updated_at=None,
        )
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[
                _JobResult(job),
                _RowsResult([("pending", None)]),
            ]),
            commit=AsyncMock(),
        )
        factory = lambda: _SessionContext(db)

        with patch("db.async_sessionmaker", return_value=factory):
            await finish_tracking_delivery_job(4)

        self.assertEqual(job.state, "queued")
        self.assertEqual(job.detail, "Provider work remains queued and has not been acknowledged.")
        db.commit.assert_awaited_once()

    async def test_conflicted_stream_action_fails_receipt_pending_review(self):
        from core.tracking_delivery import finish_tracking_delivery_job

        job = SimpleNamespace(
            state="dispatching",
            changes={"stream_actions": [{"id": 12}]},
            detail=None,
            updated_at=None,
        )
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[
                _JobResult(job),
                _RowsResult([("conflict", "Playback changed since the last import")]),
            ]),
            commit=AsyncMock(),
        )
        factory = lambda: _SessionContext(db)

        with patch("db.async_sessionmaker", return_value=factory):
            await finish_tracking_delivery_job(4)

        self.assertEqual(job.state, "failed")
        self.assertEqual(
            job.detail,
            "One or more connected provider actions need review before they can complete.",
        )
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
