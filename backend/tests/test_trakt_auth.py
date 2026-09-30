import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import trakt, trakt_auth


class EnsureValidTraktTokenTests(unittest.IsolatedAsyncioTestCase):
    """#326: every Trakt call path must validate/refresh the stored token."""

    def _settings(self, **overrides):
        base = dict(
            user_id=1,
            trakt_access_token="stored-token",
            trakt_client_id="cid",
            trakt_client_secret="csecret",
            trakt_refresh_token="rtoken",
            trakt_token_expires_at=None,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    async def test_fast_path_trusts_a_token_far_from_expiry(self):
        s = self._settings(trakt_token_expires_at=9_999_999_999)
        db = SimpleNamespace(commit=AsyncMock())
        with patch.object(trakt, "validate_token", AsyncMock()) as v, \
             patch.object(trakt, "refresh_access_token", AsyncMock()) as r:
            token = await trakt_auth.ensure_valid_trakt_token(db, s)
        self.assertEqual(token, "stored-token")
        v.assert_not_awaited()
        r.assert_not_awaited()

    async def test_force_check_bypasses_the_fast_path(self):
        s = self._settings(trakt_token_expires_at=9_999_999_999)
        db = SimpleNamespace(commit=AsyncMock())
        with patch.object(trakt, "validate_token", AsyncMock(return_value=True)) as v:
            await trakt_auth.ensure_valid_trakt_token(db, s, force_check=True)
        v.assert_awaited_once()

    async def test_expired_token_is_refreshed_and_persisted(self):
        s = self._settings()
        db = SimpleNamespace(commit=AsyncMock())
        with patch.object(trakt, "validate_token", AsyncMock(return_value=False)), \
             patch.object(trakt, "refresh_access_token",
                          AsyncMock(return_value={"access_token": "new", "refresh_token": "new-r", "expires_in": 604800})):
            token = await trakt_auth.ensure_valid_trakt_token(db, s)
        self.assertEqual(token, "new")
        self.assertEqual(s.trakt_access_token, "new")
        self.assertEqual(s.trakt_refresh_token, "new-r")
        self.assertGreater(s.trakt_token_expires_at, 0)
        db.commit.assert_awaited_once()

    async def test_no_refresh_token_raises(self):
        s = self._settings(trakt_refresh_token=None)
        db = SimpleNamespace(commit=AsyncMock())
        with patch.object(trakt, "validate_token", AsyncMock(return_value=False)):
            with self.assertRaises(trakt_auth.TraktTokenError):
                await trakt_auth.ensure_valid_trakt_token(db, s)

    async def test_refresh_failure_raises(self):
        s = self._settings()
        db = SimpleNamespace(commit=AsyncMock())
        with patch.object(trakt, "validate_token", AsyncMock(return_value=False)), \
             patch.object(trakt, "refresh_access_token",
                          AsyncMock(side_effect=RuntimeError("boom"))):
            with self.assertRaises(trakt_auth.TraktTokenError):
                await trakt_auth.ensure_valid_trakt_token(db, s)

    async def test_not_connected_raises(self):
        db = SimpleNamespace(commit=AsyncMock())
        with self.assertRaises(trakt_auth.TraktTokenError):
            await trakt_auth.ensure_valid_trakt_token(db, self._settings(trakt_access_token=None))

    async def test_near_expiry_validates_without_refreshing_or_committing(self):
        now = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
        s = self._settings(trakt_token_expires_at=int(now.timestamp()) + 3600)
        db = SimpleNamespace(commit=AsyncMock())
        with patch.object(trakt_auth, "datetime", wraps=datetime) as clock, \
             patch.object(trakt, "validate_token", AsyncMock(return_value=True)) as validate, \
             patch.object(trakt, "refresh_access_token", AsyncMock()) as refresh:
            clock.now.return_value = now
            self.assertEqual(await trakt_auth.ensure_valid_trakt_token(db, s), "stored-token")
        validate.assert_awaited_once_with("cid", "stored-token")
        refresh.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_background_lookup_uses_and_closes_its_own_session(self):
        settings = self._settings()
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: settings)))
        context = AsyncMock()
        context.__aenter__.return_value = db
        with patch.object(trakt_auth, "async_sessionmaker", return_value=lambda: context) as maker, \
             patch.object(trakt_auth, "ensure_valid_trakt_token", AsyncMock(return_value="resolved-token")) as ensure:
            self.assertEqual(await trakt_auth.ensure_valid_trakt_token_for_user(7), "resolved-token")
        maker.assert_called_once_with(trakt_auth.engine, expire_on_commit=False, class_=trakt_auth.AsyncSession)
        ensure.assert_awaited_once_with(db, settings)
        context.__aexit__.assert_awaited_once()
