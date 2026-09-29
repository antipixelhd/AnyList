import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from fastapi import HTTPException

import dependencies
from jose import jwt
from core.config import settings
from core.security import ALGORITHM


class _Result:
    def __init__(self, item=None):
        self.item = item

    def scalar_one_or_none(self):
        return self.item


class _FakeDB:
    """Matches api_key against whatever user (if any) was configured — good
    enough to exercise get_current_user_or_api_key's branching without a real
    DB, since the real lookup query itself isn't what's under test here."""

    def __init__(self, user_by_api_key=None):
        self.user_by_api_key = user_by_api_key

    async def execute(self, statement):
        return _Result(self.user_by_api_key)


class GetCurrentUserOrApiKeyTests(unittest.IsolatedAsyncioTestCase):
    async def test_jwt_user_takes_priority_over_api_key(self) -> None:
        jwt_user = SimpleNamespace(id=1)
        db = _FakeDB(user_by_api_key=None)
        user = await dependencies.get_current_user_or_api_key(
            db=db, jwt_user=jwt_user, api_key="whatever", x_api_key=None,
        )
        self.assertIs(user, jwt_user)

    async def test_falls_back_to_query_param_api_key(self) -> None:
        api_user = SimpleNamespace(id=2, api_key="secret-key")
        db = _FakeDB(user_by_api_key=api_user)
        user = await dependencies.get_current_user_or_api_key(
            db=db, jwt_user=None, api_key="secret-key", x_api_key=None,
        )
        self.assertIs(user, api_user)

    async def test_falls_back_to_x_api_key_header(self) -> None:
        api_user = SimpleNamespace(id=3, api_key="secret-key")
        db = _FakeDB(user_by_api_key=api_user)
        user = await dependencies.get_current_user_or_api_key(
            db=db, jwt_user=None, api_key=None, x_api_key="secret-key",
        )
        self.assertIs(user, api_user)

    async def test_raises_401_when_api_key_matches_no_user(self) -> None:
        db = _FakeDB(user_by_api_key=None)
        with self.assertRaises(HTTPException) as ctx:
            await dependencies.get_current_user_or_api_key(
                db=db, jwt_user=None, api_key="bad-key", x_api_key=None,
            )
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_raises_401_when_no_credentials_provided(self) -> None:
        db = _FakeDB(user_by_api_key=None)
        with self.assertRaises(HTTPException) as ctx:
            await dependencies.get_current_user_or_api_key(
                db=db, jwt_user=None, api_key=None, x_api_key=None,
            )
        self.assertEqual(ctx.exception.status_code, 401)


class SessionTokenValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_subject_is_rejected_without_a_database_lookup(self):
        from unittest.mock import AsyncMock
        from starlette.requests import Request
        from routers.media import verify_image_token

        token = jwt.encode({}, settings.secret_key, algorithm=ALGORITHM)
        db = SimpleNamespace(execute=AsyncMock())
        request = Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})
        for check in (dependencies.get_current_user(db=db, token=token), verify_image_token(request, db)):
            with self.assertRaises(HTTPException) as ctx:
                await check
            self.assertEqual(ctx.exception.status_code, 401)
        db.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
