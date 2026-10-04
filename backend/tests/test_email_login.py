"""Email login and signup regressions against an isolated database."""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "email-login-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from jose import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy import MetaData
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from core.config import settings
from core.security import ALGORITHM, get_password_hash
from db import get_db
from models.users import User
from models.profile import UserProfileData
from models.password_reset import PasswordResetToken
from models.email_activation import EmailActivation
from routers import auth, profile


class EmailLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        metadata = MetaData()
        for model in (User, UserProfileData, PasswordResetToken, EmailActivation):
            table = model.__table__.to_metadata(metadata)
            for column in table.columns:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        self.db = AsyncSession(self.engine, expire_on_commit=False)
        self.user = User(email="Owner@example.com", username="owner",
                         password_hash=get_password_hash("password"), api_key="owner-key")
        self.db.add(self.user)
        await self.db.commit()
        app = FastAPI()
        app.include_router(auth.router, prefix="/auth")
        app.include_router(profile.router, prefix="/profile")
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.settings_patches = [
            patch.object(auth.limiter, "enabled", False),
            patch.object(settings, "oidc_enabled", False),
            patch.object(settings, "require_email_validation", False),
            patch.object(settings, "enable_registrations", True),
            patch.object(settings, "registration_max_allowed_users", 0),
        ]
        for item in self.settings_patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.settings_patches):
            item.stop()
        await self.client.aclose()
        await self.db.close()
        await self.engine.dispose()

    async def login(self, email="owner@example.com", password="password"):
        return await self.client.post("/auth/login", data={"username": email, "password": password})

    async def test_email_login_survives_rename_and_old_session_still_works(self):
        response = await self.login("  OWNER@EXAMPLE.COM  ")
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["access_token"]
        self.assertEqual(jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])["sub"], str(self.user.id))
        headers = {"Authorization": f"Bearer {token}"}
        renamed = await self.client.patch("/profile/me/account-name", headers=headers, json={"username": "new-name"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual((await self.login()).status_code, 200)
        self.assertEqual((await self.login("owner")).status_code, 401)
        self.assertEqual((await self.login("new-name")).status_code, 401)
        me = await self.client.get("/auth/me", headers=headers)
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["id"], self.user.id)
        self.assertEqual(me.json()["username"], "new-name")

    async def test_wrong_password_and_sso_only_account_are_rejected(self):
        self.assertEqual((await self.login(password="wrong")).status_code, 401)
        self.user.password_hash = None
        await self.db.commit()
        self.assertEqual((await self.login()).status_code, 401)

    async def test_confirmation_sso_gate_and_two_factor_are_preserved(self):
        self.user.email_confirmed = False
        await self.db.commit()
        with patch.object(settings, "require_email_validation", True):
            self.assertEqual((await self.login()).status_code, 403)
        with patch.object(settings, "oidc_enabled", True), patch.object(settings, "oidc_disable_password_login", True):
            self.assertEqual((await self.login()).status_code, 403)
        self.user.totp_enabled = True
        await self.db.commit()
        response = await self.login()
        self.assertTrue(response.json()["requires_2fa"])
        payload = jwt.decode(response.json()["temp_token"], settings.secret_key, algorithms=[ALGORITHM])
        self.assertEqual(payload["sub"], str(self.user.id))
        self.assertEqual(payload["type"], "2fa_pending")

    async def test_signup_normalizes_email_and_can_login_immediately(self):
        response = await self.client.post("/auth/register", json={
            "email": "New@EXAMPLE.COM", "username": "new-user", "password": "password",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["email"], "new@example.com")
        self.assertFalse(response.json()["is_admin"])
        self.assertEqual((await self.login(response.json()["email"])).status_code, 200)

    async def test_signup_rejects_case_variant_and_closed_or_capped_registration(self):
        body = {"email": "OWNER@example.com", "username": "another", "password": "password"}
        self.assertEqual((await self.client.post("/auth/register", json=body)).status_code, 400)
        body["email"] = "another@example.com"
        with patch.object(settings, "enable_registrations", False):
            self.assertEqual((await self.client.post("/auth/register", json=body)).status_code, 403)
        with patch.object(settings, "registration_max_allowed_users", 1):
            self.assertEqual((await self.client.post("/auth/register", json=body)).status_code, 403)
        # Reopening registration permits a new account with email login.
        self.assertEqual((await self.client.post("/auth/register", json=body)).status_code, 200)
        self.assertEqual((await self.login(body["email"])).status_code, 200)

    async def test_signup_confirmation_must_complete_before_email_login(self):
        with patch.object(settings, "require_email_validation", True), patch.object(auth, "send_activation_email", AsyncMock()):
            response = await self.client.post("/auth/register", json={
                "email": "New@example.com", "username": "new-user", "password": "password",
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["email_confirmed"])
            self.assertEqual((await self.login("new@example.com")).status_code, 403)
            activation = (await self.db.execute(select(EmailActivation))).scalar_one()
            activated = await self.client.post(f"/auth/activate/{activation.token}")
            self.assertEqual(activated.status_code, 200, activated.text)
            self.assertEqual((await self.login("new@example.com")).status_code, 200)

    async def test_signup_conflicting_email_and_handle_on_different_accounts(self):
        self.db.add(User(email="other@example.com", username="other", api_key="other-key"))
        await self.db.commit()
        response = await self.client.post("/auth/register", json={
            "email": "OWNER@example.com", "username": "other", "password": "password",
        })
        self.assertEqual(response.status_code, 400, response.text)

    async def test_database_rejects_case_variant_even_without_signup_checks(self):
        self.db.add(User(email=" owner@EXAMPLE.COM ", username="another", api_key="another-key"))
        with self.assertRaises(IntegrityError):
            await self.db.commit()
        await self.db.rollback()

    async def test_password_reset_matches_email_case_insensitively(self):
        with patch.object(settings, "smtp_address", "smtp.example.com"), patch.object(auth, "send_password_reset_email", AsyncMock()) as send:
            response = await self.client.post("/auth/forgot-password", json={"email": "OWNER@example.com"})
        self.assertEqual(response.status_code, 200, response.text)
        send.assert_awaited_once()
        self.assertEqual(send.call_args.args[0], "Owner@example.com")
