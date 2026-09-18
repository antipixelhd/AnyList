import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from routers import oidc


class _FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._json_data


class _FakeHttpClient:
    """Stands in for httpx.AsyncClient's token-exchange + userinfo calls."""

    def __init__(self, token_response, userinfo_response):
        self._token_response = token_response
        self._userinfo_response = userinfo_response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        return self._token_response

    async def get(self, url, **kwargs):
        return self._userinfo_response


class _UserResult:
    def __init__(self, user):
        self.user = user

    def scalar_one_or_none(self):
        return self.user


class _CountResult:
    def __init__(self, count):
        self.count = count

    def scalar_one(self):
        return self.count


class _FakeSession:
    """Queues results for db.execute() in call order, mirroring the pattern
    already established in tests/test_shows.py and tests/test_history.py."""

    def __init__(self, results):
        self.execute = AsyncMock(side_effect=results)
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()


class OidcExchangeFirstUserAdminTests(unittest.IsolatedAsyncioTestCase):
    """Regression tests for #159: the first user auto-created via OIDC must be
    granted admin, mirroring the is_first_user rule routers/auth.py's local
    registration endpoint already applies. Before the fix, User(...) was
    constructed with no is_admin argument at all, so it silently defaulted to
    False regardless of how many users existed."""

    def _patched_settings(self):
        return patch.multiple(
            oidc.app_settings,
            oidc_enabled=True,
            oidc_auto_create_users=True,
            oidc_require_verified_email=False,
            oidc_identifier_field="sub",
            oidc_token_url="https://provider.example/token",
            oidc_userinfo_url="https://provider.example/userinfo",
            oidc_redirect_url="https://scrob.example/oidc-callback",
            oidc_client_id="client-id",
            oidc_client_secret="client-secret",
        )

    async def test_first_oidc_user_becomes_admin(self) -> None:
        token_response = _FakeResponse(200, {"access_token": "provider-token"})
        userinfo_response = _FakeResponse(200, {"sub": "user-1", "email": "new@example.com"})
        fake_client = _FakeHttpClient(token_response, userinfo_response)

        # Query order: 1) existing-user-by-email lookup (none), 2) username
        # uniqueness check (available), 3) count(*) for is_first_user.
        db = _FakeSession([_UserResult(None), _UserResult(None), _CountResult(0)])

        with self._patched_settings(), \
             patch("routers.oidc.httpx.AsyncClient", return_value=fake_client), \
             patch("routers.oidc.create_access_token", return_value="jwt-token"):
            await oidc.oidc_exchange(oidc.OidcExchangeRequest(code="auth-code"), db)

        created_user = db.add.call_args[0][0]
        self.assertTrue(created_user.is_admin)

    async def test_subsequent_oidc_user_is_not_admin(self) -> None:
        token_response = _FakeResponse(200, {"access_token": "provider-token"})
        userinfo_response = _FakeResponse(200, {"sub": "user-2", "email": "second@example.com"})
        fake_client = _FakeHttpClient(token_response, userinfo_response)

        db = _FakeSession([_UserResult(None), _UserResult(None), _CountResult(1)])

        with self._patched_settings(), \
             patch("routers.oidc.httpx.AsyncClient", return_value=fake_client), \
             patch("routers.oidc.create_access_token", return_value="jwt-token"):
            await oidc.oidc_exchange(oidc.OidcExchangeRequest(code="auth-code"), db)

        created_user = db.add.call_args[0][0]
        self.assertFalse(created_user.is_admin)


class OidcInviteOnlyGoogleTests(unittest.IsolatedAsyncioTestCase):
    def _patched_settings(self, **overrides):
        values = {
            "oidc_enabled": True,
            "oidc_auto_create_users": False,
            "oidc_require_verified_email": True,
            "oidc_identifier_field": "email",
            "oidc_auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "oidc_token_url": "https://oauth2.googleapis.com/token",
            "oidc_userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
            "oidc_redirect_url": "https://media.example/oidc-callback",
            "oidc_client_id": "client-id.apps.googleusercontent.com",
            "oidc_client_secret": "client-secret",
        }
        values.update(overrides)
        return patch.multiple(oidc.app_settings, **values)

    async def test_verified_google_email_matches_preprovisioned_account_case_insensitively(self) -> None:
        existing = SimpleNamespace(id=42)
        db = _FakeSession([_UserResult(existing)])
        client = _FakeHttpClient(
            _FakeResponse(200, {"access_token": "provider-token"}),
            _FakeResponse(200, {
                "sub": "google-subject",
                "email": "Friend@Example.COM",
                "email_verified": True,
            }),
        )

        with self._patched_settings(), \
             patch("routers.oidc.httpx.AsyncClient", return_value=client), \
             patch("routers.oidc.create_access_token", return_value="jwt-token") as create_token:
            result = await oidc.oidc_exchange(
                oidc.OidcExchangeRequest(code="auth-code"),
                db,
            )

        self.assertEqual(result, {"access_token": "jwt-token"})
        create_token.assert_called_once_with(subject=42)
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_unverified_google_email_is_rejected_before_account_lookup(self) -> None:
        db = _FakeSession([])
        client = _FakeHttpClient(
            _FakeResponse(200, {"access_token": "provider-token"}),
            _FakeResponse(200, {
                "email": "friend@example.com",
                "email_verified": False,
            }),
        )

        with self._patched_settings(), patch(
            "routers.oidc.httpx.AsyncClient",
            return_value=client,
        ):
            with self.assertRaises(HTTPException) as raised:
                await oidc.oidc_exchange(
                    oidc.OidcExchangeRequest(code="auth-code"),
                    db,
                )

        self.assertEqual(raised.exception.status_code, 403)
        db.execute.assert_not_awaited()

    async def test_unknown_verified_email_is_not_auto_created(self) -> None:
        db = _FakeSession([_UserResult(None)])
        client = _FakeHttpClient(
            _FakeResponse(200, {"access_token": "provider-token"}),
            _FakeResponse(200, {
                "email": "unknown@example.com",
                "email_verified": True,
            }),
        )

        with self._patched_settings(), patch(
            "routers.oidc.httpx.AsyncClient",
            return_value=client,
        ):
            with self.assertRaises(HTTPException) as raised:
                await oidc.oidc_exchange(
                    oidc.OidcExchangeRequest(code="auth-code"),
                    db,
                )

        self.assertEqual(raised.exception.status_code, 403)
        db.add.assert_not_called()

    async def test_incomplete_enabled_configuration_fails_closed(self) -> None:
        with self._patched_settings(oidc_auth_url=None):
            with self.assertRaises(HTTPException) as raised:
                await oidc.oidc_authorize()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("OIDC_AUTH_URL", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
