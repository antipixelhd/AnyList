"""Intent previews read light provider metadata without importing media."""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'preview-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

import httpx
from fastapi import FastAPI
from db import get_db
from dependencies import get_optional_user
from routers.tracking import router


class CatalogPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMock()
        app = FastAPI()
        app.include_router(router, prefix='/tracking')
        async def session():
            yield self.db
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_optional_user] = lambda: None
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        for target, value in [('routers.tracking.catalog_access', None),
                              ('routers.tracking.anime_is_visible', True),
                              ('core.settings_store.get_user_tmdb_key', 'private-key')]:
            replacement = patch(target, AsyncMock(return_value=value))
            replacement.start()
            self.addCleanup(replacement.stop)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_light_preview_never_imports_or_writes(self):
        for media_type, provider in [('movie', 'get_movie_light'), ('series', 'get_show_light')]:
            with patch('core.tmdb.' + provider, AsyncMock(return_value={
                    'id': 123, 'title': 'Film', 'poster_path': '/primary.jpg',
                    'backdrop_path': '/primary-background.jpg', 'genres': [{'id': 18}],
                    'overview': 'Description', 'vote_average': 8})) as light, \
                    patch('core.enrichment.create_media_safely', AsyncMock()) as create, \
                    patch('core.enrichment.enrich_media', AsyncMock()) as enrich:
                response = await self.client.get(f'/tracking/catalog/{media_type}/123/preview')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['cache-control'], 'private, no-store')
                self.assertEqual(response.json()['id'], None)
                self.assertEqual(response.json()['genres'], ['Drama'])
                light.assert_awaited_once_with(123, api_key='private-key')
                create.assert_not_awaited()
                enrich.assert_not_awaited()
        self.db.execute.assert_not_awaited()
        self.db.commit.assert_not_awaited()
        self.db.add.assert_not_called()

    async def test_invalid_ids_adult_content_and_provider_errors_are_rejected(self):
        for value in ['0', '-1', '2147483648']:
            response = await self.client.get(f'/tracking/catalog/movie/{value}/preview')
            self.assertEqual(response.status_code, 422)
        for data in [{'id': 123, 'adult': True}, {'id': 124}]:
            with patch('core.tmdb.get_movie_light', AsyncMock(return_value=data)):
                response = await self.client.get('/tracking/catalog/movie/123/preview')
                self.assertEqual(response.status_code, 404)
        with patch('core.tmdb.get_movie_light', AsyncMock(side_effect=OSError('offline'))):
            response = await self.client.get('/tracking/catalog/movie/123/preview')
            self.assertEqual(response.status_code, 502)

    async def test_provider_key_and_catalogue_access_are_required(self):
        from fastapi import HTTPException
        with patch('routers.tracking.catalog_access', AsyncMock(side_effect=HTTPException(403))), \
                patch('core.tmdb.get_movie_light', AsyncMock()) as light:
            response = await self.client.get('/tracking/catalog/movie/123/preview')
            self.assertEqual(response.status_code, 403)
            light.assert_not_awaited()
        with patch('core.settings_store.get_user_tmdb_key', AsyncMock(return_value=None)):
            response = await self.client.get('/tracking/catalog/movie/123/preview')
            self.assertEqual(response.status_code, 409)
