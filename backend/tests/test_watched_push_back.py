import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from routers import sync, webhooks


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class WatchedPushBackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = SimpleNamespace(
            id=12, type="jellyfin", push_watched=True, url="http://local",
            token="token", server_user_id="remote-user",
        )
        self.db = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(approved=True)),
            execute=AsyncMock(return_value=_Rows([("file-1", 2), ("file-1", 3)])),
        )

    async def test_pull_waits_for_approved_initial_import(self):
        self.db.get.return_value = SimpleNamespace(approved=False)
        with patch.object(sync.jellyfin, "mark_watched", new_callable=AsyncMock) as push:
            count = await sync._push_watched_back_to_source(self.db, 1, self.conn, {2: "file-1"})
        self.assertEqual(count, 0)
        self.db.execute.assert_not_awaited()
        push.assert_not_awaited()

    async def test_combined_file_requires_every_episode_watched(self):
        with (
            patch.object(sync, "_latest_watched_at", new_callable=AsyncMock, return_value={2: datetime(2026, 1, 1)}),
            patch.object(sync.jellyfin, "mark_watched", new_callable=AsyncMock) as push,
        ):
            count = await sync._push_watched_back_to_source(self.db, 1, self.conn, {2: "file-1"})
        self.assertEqual(count, 0)
        push.assert_not_awaited()

    async def test_approved_combined_file_pushes_once_with_latest_date(self):
        earlier = datetime(2026, 1, 1)
        later = datetime(2026, 1, 2)
        with (
            patch.object(sync, "_latest_watched_at", new_callable=AsyncMock, return_value={2: earlier, 3: later}),
            patch.object(sync.jellyfin, "mark_watched", new_callable=AsyncMock, return_value=True) as push,
            patch.object(webhooks, "mark_pushed_watched") as mark_echo,
        ):
            count = await sync._push_watched_back_to_source(self.db, 1, self.conn, {2: "file-1", 3: "file-1"})
        self.assertEqual(count, 1)
        push.assert_awaited_once_with("http://local", "token", "remote-user", "file-1", played_at=later)
        self.assertEqual(mark_echo.call_count, 2)

    async def test_webhook_waits_for_approved_initial_import(self):
        self.db.get.return_value = SimpleNamespace(approved=False)
        with patch.object(sync, "_latest_watched_at", new_callable=AsyncMock) as watches:
            pushed = await webhooks._push_watched_for_new_item(
                self.db, 1, self.conn, "file-1", [SimpleNamespace(id=2)]
            )
        self.assertFalse(pushed)
        watches.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
