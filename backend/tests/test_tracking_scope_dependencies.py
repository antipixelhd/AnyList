"""Authenticated integration checks for scoped external tracking credentials.

Set TRACKING_TEST_DATABASE_URL to a disposable, migrated local PostgreSQL DB.
"""
import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "local-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from core.security import create_access_token
from db import get_db
from models import Media, OAuthDeviceGrant, User
from models.base import MediaType
from routers.history import router as history_router
from routers.tracking import router as tracking_router


@unittest.skipUnless(os.getenv("TRACKING_TEST_DATABASE_URL"), "Requires disposable PostgreSQL database")
class TrackingScopeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TRACKING_TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.db = AsyncSession(bind=self.connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        self.user = User(username="tracking-scope-owner", email="tracking-scope@example.test", api_key="tracking-scope-api-key")
        self.media = Media(title="Scoped API Fixture", media_type=MediaType.movie)
        self.db.add_all([self.user, self.media])
        await self.db.flush()
        self.tracking_grant = OAuthDeviceGrant(
            device_code_hash="a" * 64, user_code="ASDF-QWER", client_name="Scoped tracking client",
            scope="tracking:write", status="approved", interval=5, user_id=self.user.id,
            expires_at=datetime.utcnow() + timedelta(days=1),
        )
        self.legacy_grant = OAuthDeviceGrant(
            device_code_hash="b" * 64, user_code="ZXCV-UIOP", client_name="Legacy client",
            scope="write", status="approved", interval=5, user_id=self.user.id,
            expires_at=datetime.utcnow() + timedelta(days=1),
        )
        self.db.add_all([self.tracking_grant, self.legacy_grant])
        await self.db.flush()
        await self.db.commit()

        app = FastAPI()
        app.include_router(tracking_router, prefix="/tracking")
        app.include_router(history_router, prefix="/history")

        async def session():
            yield self.db

        app.dependency_overrides[get_db] = session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.tracking_token = create_access_token(str(self.user.id), extra_claims={
            "type": "device", "scope": "tracking:write", "jti": str(self.tracking_grant.id),
        })
        self.legacy_token = create_access_token(str(self.user.id), extra_claims={
            "type": "device", "scope": "write", "jti": str(self.legacy_grant.id),
        })

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.db.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def test_tracking_scope_updates_entry_but_cannot_use_rich_or_legacy_writes(self):
        headers = {"Authorization": f"Bearer {self.tracking_token}"}
        updated = await self.client.patch(
            f"/tracking/entry/{self.media.id}/external", headers=headers,
            json={"status": "planning", "manual_score": 8.5},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["score"], 8.5)
        self.assertIn("delivery_job_id", updated.json())

        rich_write = await self.client.patch(
            f"/tracking/entry/{self.media.id}", headers=headers, json={"notes": "should not be writable"},
        )
        self.assertEqual(rich_write.status_code, 403)

        extra_field = await self.client.patch(
            f"/tracking/entry/{self.media.id}/external", headers=headers,
            json={"favorite": True},
        )
        self.assertEqual(extra_field.status_code, 422)

        inherited_write = await self.client.post(
            "/history", headers=headers,
            json={"media_type": "movie", "media_id": self.media.id, "completed": True},
        )
        self.assertEqual(inherited_write.status_code, 401)

    async def test_legacy_write_scope_cannot_use_external_tracking_route(self):
        response = await self.client.patch(
            f"/tracking/entry/{self.media.id}/external",
            headers={"Authorization": f"Bearer {self.legacy_token}"},
            json={"status": "watching"},
        )
        self.assertEqual(response.status_code, 403)

    async def test_revoking_scoped_grant_stops_external_writes_immediately(self):
        self.tracking_grant.revoked_at = datetime.utcnow()
        await self.db.commit()
        response = await self.client.patch(
            f"/tracking/entry/{self.media.id}/external",
            headers={"Authorization": f"Bearer {self.tracking_token}"},
            json={"status": "watching"},
        )
        self.assertEqual(response.status_code, 401)
