"""Account-name changes against an isolated in-memory database."""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from db import get_db
from dependencies import get_current_user
from models.users import User
from routers.profile import router


class AccountNameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection:
            await connection.run_sync(User.__table__.create)
        self.db = AsyncSession(self.engine, expire_on_commit=False)
        self.user = User(email='owner@example.com', username='owner', password_hash='hash', api_key='owner-key')
        self.other = User(email='other@example.com', username='taken', api_key='other-key')
        self.db.add_all([self.user, self.other])
        await self.db.commit()
        app = FastAPI()
        app.include_router(router, prefix='/profile')
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.app = app
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.db.close()
        await self.engine.dispose()

    async def test_regular_and_google_accounts_can_rename_and_keep_identity(self):
        for password_hash, requested in [('hash', 'regular-name'), (None, 'google-name')]:
            with self.subTest(password_account=password_hash is not None):
                self.user.password_hash = password_hash
                identity = (self.user.id, self.user.email, self.user.api_key)
                response = await self.client.patch('/profile/me/account-name', json={'username': f'  {requested}  '})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {'username': requested})
                await self.db.refresh(self.user)
                self.assertEqual(self.user.username, requested)
                self.assertEqual((self.user.id, self.user.email, self.user.api_key), identity)
                self.assertEqual(self.user.password_hash, password_hash)
                self.assertEqual(self.user.display_name, requested)

    async def test_taken_name_is_rejected_without_changing_either_account(self):
        response = await self.client.patch('/profile/me/account-name', json={'username': 'taken'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('already taken', response.json()['detail'])
        await self.db.refresh(self.user)
        await self.db.refresh(self.other)
        self.assertEqual(self.user.username, 'owner')
        self.assertEqual(self.other.username, 'taken')

    async def test_current_name_is_available_to_its_owner(self):
        response = await self.client.patch('/profile/me/account-name', json={'username': 'owner'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['username'], 'owner')

    async def test_invalid_names_are_rejected_before_mutation(self):
        for value in ['', '   ', None, 'x' * 101, '../name', 'a/b', 'a b', '<script>', '.']:
            with self.subTest(value=value):
                response = await self.client.patch('/profile/me/account-name', json={'username': value})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.user.username, 'owner')
        response = await self.client.patch('/profile/me/account-name', json={'username': 'x' * 100})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['username'], 'x' * 100)

    async def test_database_conflict_rolls_back_rename(self):
        with patch.object(self.db, 'commit', AsyncMock(side_effect=IntegrityError('update users', {}, Exception('unique')))):
            response = await self.client.patch('/profile/me/account-name', json={'username': 'raced-name'})
        self.assertEqual(response.status_code, 409)
        persisted = (await self.db.execute(select(User.username).where(User.email == 'owner@example.com'))).scalar_one()
        self.assertEqual(persisted, 'owner')

    async def test_authentication_is_required(self):
        self.app.dependency_overrides.pop(get_current_user)
        response = await self.client.patch('/profile/me/account-name', json={'username': 'new-name'})
        self.assertEqual(response.status_code, 401)

    async def test_legacy_display_name_is_not_used_or_editable(self):
        from models.profile import UserProfileData
        from schemas import UserProfileUpdate, UserProfileResponse
        self.user.profile = UserProfileData(display_name='Old label')
        self.assertEqual(self.user.display_name, 'owner')
        self.assertNotIn('display_name', UserProfileUpdate.model_fields)
        self.assertNotIn('display_name', UserProfileResponse.model_fields)
