"""Concurrency checks; ACCOUNT_SECURITY_TEST_DATABASE_URL must be a disposable DB."""

import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import account_security_helpers  # noqa: F401 - initialize isolated test settings
from core.config import settings
from core.security import create_session_token, get_password_hash, hash_opaque_token
from db import get_db
from models.account_security import EmailChangeToken, OidcIdentity
from models.email_activation import EmailActivation
from models.password_reset import PasswordResetToken
from models.profile import UserProfileData
from models.tracking import TrackingPreferences
from models.users import User
from routers import admin, auth, oidc
from test_oidc import ProviderClient

import httpx
from fastapi import FastAPI
from sqlalchemy import MetaData, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@unittest.skipUnless(
    os.environ.get("ACCOUNT_SECURITY_TEST_DATABASE_URL"),
    "requires disposable PostgreSQL",
)
class AccountSecurityConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = os.environ["ACCOUNT_SECURITY_TEST_DATABASE_URL"]
        self.schema = "account_security_" + uuid.uuid4().hex
        self.admin_engine = create_async_engine(url)
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(
            url, connect_args={"server_settings": {"search_path": self.schema}}
        )
        metadata = MetaData()
        for model in (
            User,
            UserProfileData,
            TrackingPreferences,
            EmailChangeToken,
            OidcIdentity,
            EmailActivation,
            PasswordResetToken,
        ):
            model.__table__.to_metadata(metadata)
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            user = User(
                email="owner@example.com",
                oidc_login_email="owner@example.com",
                username="owner",
                api_key="owner-key",
                password_hash=get_password_hash("old"),
                session_version=0,
            )
            db.add(user)
            await db.commit()
            self.user_id = user.id
            self.headers = {"Authorization": f"Bearer {create_session_token(user)}"}
        self.app = FastAPI()
        self.app.include_router(auth.router, prefix="/auth")
        self.app.include_router(admin.router, prefix="/admin")
        self.app.include_router(oidc.router, prefix="/auth/oidc")

        async def database():
            async with self.Session() as db:
                yield db

        self.app.dependency_overrides[get_db] = database
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.limiter = patch.object(auth.limiter, "enabled", False)
        self.limiter.start()

    async def asyncTearDown(self):
        self.limiter.stop()
        await self.client.aclose()
        await self.engine.dispose()
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()

    async def test_parallel_password_recovery_consumes_token_once(self):
        async with self.Session() as db:
            db.add(PasswordResetToken(user_id=self.user_id, token="reset"))
            await db.commit()
        results = await asyncio.gather(
            *[
                self.client.post(
                    "/auth/reset-password/reset", json={"new_password": password}
                )
                for password in ("first", "second")
            ]
        )
        self.assertEqual(sorted(result.status_code for result in results), [200, 400])
        async with self.Session() as db:
            self.assertEqual((await db.get(User, self.user_id)).session_version, 1)
        self.assertEqual(
            (await self.client.get("/auth/me", headers=self.headers)).status_code, 401
        )

    async def test_parallel_email_requests_leave_only_latest_confirmation(self):
        tokens = []

        async def send(email, token):
            tokens.append(token)
            await asyncio.sleep(0.05)

        with (
            patch.object(settings, "smtp_address", "smtp.example.com"),
            patch.object(auth, "send_email_change_email", side_effect=send),
        ):
            results = await asyncio.gather(
                *[
                    self.client.post(
                        "/auth/change-email",
                        headers=self.headers,
                        json={"email": email, "current_password": "old"},
                    )
                    for email in ("first@example.com", "second@example.com")
                ]
            )
        self.assertEqual([result.status_code for result in results], [200, 200])
        self.assertEqual(
            (
                await self.client.post(
                    "/auth/confirm-email-change", json={"token": tokens[0]}
                )
            ).status_code,
            400,
        )
        self.assertEqual(
            (
                await self.client.post(
                    "/auth/confirm-email-change", json={"token": tokens[1]}
                )
            ).status_code,
            200,
        )

    async def test_parallel_confirmations_consume_link_once(self):
        async with self.Session() as db:
            db.add(
                EmailChangeToken(
                    user_id=self.user_id,
                    token_hash=hash_opaque_token("confirm"),
                    email="new@example.com",
                    previous_email="owner@example.com",
                    session_version=0,
                )
            )
            await db.commit()
        responses = await asyncio.gather(
            *[
                self.client.post(
                    "/auth/confirm-email-change", json={"token": "confirm"}
                )
                for _ in range(2)
            ]
        )
        self.assertEqual(
            sorted(response.status_code for response in responses), [200, 400]
        )

    async def test_concurrent_subjects_cannot_both_link_same_invitation(self):
        # Use the configured generic-provider namespace so this test isolates DB
        # serialization; cryptographic token validation is covered separately.
        with patch.multiple(
            settings,
            oidc_enabled=True,
            oidc_auth_url="https://idp.example/authorize",
            oidc_token_url="https://idp.example/token",
            oidc_userinfo_url="https://idp.example/userinfo",
            oidc_issuer_url=None,
            oidc_client_id="client",
            oidc_client_secret="secret",
            oidc_auto_create_users=False,
            oidc_require_verified_email=True,
            oidc_identifier_field="email",
        ):
            states = [
                (await self.client.get("/auth/oidc/authorize")).json()["state"]
                for _ in range(2)
            ]
            clients = [
                ProviderClient(
                    {
                        "sub": subject,
                        "email": "owner@example.com",
                        "email_verified": True,
                    }
                )
                for subject in ("first", "second")
            ]
            with (
                patch.object(oidc.httpx, "AsyncClient", side_effect=clients),
                patch.object(oidc, "_verify_id_token", AsyncMock(return_value=None)),
            ):
                responses = await asyncio.gather(
                    *[
                        self.client.post(
                            "/auth/oidc/exchange", json={"code": "code", "state": state}
                        )
                        for state in states
                    ]
                )
        self.assertEqual(
            sorted(response.status_code for response in responses), [200, 403]
        )
        async with self.Session() as db:
            self.assertEqual(
                len((await db.execute(select(OidcIdentity))).scalars().all()), 1
            )
