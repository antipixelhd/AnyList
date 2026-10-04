import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "account-security-tests")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.types import JSON

from core.config import settings
from core.security import create_session_token, get_password_hash
from db import get_db
from models.account_security import EmailChangeToken, OidcIdentity
from models.email_activation import EmailActivation
from models.password_reset import PasswordResetToken
from models.profile import UserProfileData
from models.users import User, TotpBackupCode
from routers import admin, auth, oidc


class AccountSecurityCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        metadata = MetaData()
        for model in (
            User,
            UserProfileData,
            EmailActivation,
            PasswordResetToken,
            EmailChangeToken,
            OidcIdentity,
            TotpBackupCode,
        ):
            table = model.__table__.to_metadata(metadata)
            for column in table.columns:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        self.db = AsyncSession(self.engine, expire_on_commit=False)
        self.app = FastAPI()
        self.app.include_router(auth.router, prefix="/auth")
        self.app.include_router(oidc.router, prefix="/auth/oidc")
        self.app.include_router(admin.router, prefix="/admin")
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.patches = [
            patch.object(auth.limiter, "enabled", False),
            patch.multiple(
                settings,
                oidc_enabled=True,
                oidc_disable_password_login=False,
                oidc_auto_create_users=False,
                oidc_require_verified_email=True,
                oidc_identifier_field="email",
                oidc_issuer_url=None,
                oidc_auth_url="https://accounts.google.com/o/oauth2/v2/auth",
                oidc_token_url="https://oauth2.googleapis.com/token",
                oidc_userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
                oidc_client_id="test-client",
                oidc_client_secret="test-secret",
                oidc_redirect_url="https://example.com/oidc-callback",
                smtp_address="smtp.example.com",
                require_email_validation=False,
            ),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.client.aclose()
        await self.db.close()
        await self.engine.dispose()

    async def user(
        self, email="owner@example.com", *, password="old-password", admin=False
    ):
        user = User(
            email=email,
            oidc_login_email=email,
            username=email.split("@")[0],
            api_key=email,
            password_hash=get_password_hash(password) if password else None,
            is_admin=admin,
            session_version=0,
        )
        self.db.add(user)
        await self.db.commit()
        return user

    def headers(self, user, **kwargs):
        return {"Authorization": f"Bearer {create_session_token(user, **kwargs)}"}
