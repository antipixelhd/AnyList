import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from db import get_db
from dependencies import get_current_user
from routers import catalogue
from models.catalogue import CatalogueEntity
from core.catalogue_providers import ProviderError
from core.descriptive_metadata import tmdb_fields
from core.enrichment import enrich_series_from_show
from models.media import Media
from models.show import Show
from models.base import MediaType


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = SimpleNamespace(
            get=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock()
        )
        self.app = FastAPI()
        self.app.include_router(catalogue.router)

        async def database():
            yield self.db

        self.app.dependency_overrides[get_db] = database
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.rate = patch.object(catalogue.limiter, "enabled", False)
        self.rate.start()

    async def asyncTearDown(self):
        self.rate.stop()
        await self.client.aclose()

    def login(self, admin=False):
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=1, is_admin=admin
        )

    async def test_anonymous_requests_need_authentication(self):
        for method, path in [
            ("GET", "/catalogue/providers"),
            ("GET", "/catalogue/search?provider=igdb&kind=game&q=x"),
            ("GET", "/catalogue/entities/1"),
            ("POST", "/catalogue/backfill"),
            ("DELETE", "/catalogue/performances/1"),
        ]:
            response = await self.client.request(method, path)
            self.assertEqual(response.status_code, 401)

    async def test_metadata_corrections_require_administrator(self):
        self.login()
        for method, path, body in [
            ("POST", "/catalogue/backfill", None),
            ("PATCH", "/catalogue/entities/1", {"name": "Name"}),
            (
                "POST",
                "/catalogue/entities/1/identities",
                {"namespace": "steam.app", "external_id": "1", "reason": "reviewed"},
            ),
            ("POST", "/catalogue/performances", {"credit_id": 1, "appearance_id": 1}),
            ("DELETE", "/catalogue/performances/1", None),
        ]:
            response = await self.client.request(method, path, json=body)
            self.assertEqual(response.status_code, 403)

    async def test_detail_and_provider_sources_do_not_expose_internal_state(self):
        self.login()
        self.db.get.return_value = CatalogueEntity(
            id=1,
            kind="book",
            name="Book",
            attributes={},
            field_sources={"name": "hardcover"},
            protected_fields=["name"],
        )
        response = await self.client.get("/catalogue/entities/1")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("protected_fields", response.json())
        response = await self.client.get("/catalogue/providers")
        self.assertEqual(set(response.json()), {"providers"})
        self.assertNotIn("api_key", response.text)

    async def test_query_bounds_and_error_redaction(self):
        self.login()
        self.assertEqual(
            (
                await self.client.get(
                    "/catalogue/search?provider=igdb&kind=game&q=x&limit=1000"
                )
            ).status_code,
            422,
        )
        adapter = SimpleNamespace(
            search=AsyncMock(side_effect=ProviderError("unauthorized"))
        )
        with patch.object(catalogue, "providers", AsyncMock(return_value=adapter)):
            response = await self.client.get(
                "/catalogue/search?provider=igdb&kind=game&q=x"
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], {"code": "unauthorized"})


class RetentionTests(unittest.TestCase):
    def test_title_countries_and_stable_cast_ids_are_retained(self):
        fields = tmdb_fields(
            {
                "origin_country": ["JP"],
                "production_countries": [{"iso_3166_1": "JP"}],
                "number_of_episodes": 20,
                "credits": {"cast": [{"id": 7, "name": "Actor"}]},
            }
        )
        self.assertEqual(fields["origin_country"], ["JP"])
        self.assertEqual(fields["cast"][0]["id"], 7)
        self.assertEqual(tmdb_fields({}, fields), fields)

    def test_copying_tvdb_show_preserves_descriptive_fields_and_canonical_numbering(
        self,
    ):
        media = Media(
            media_type=MediaType.series,
            title="Old",
            tmdb_data={
                "origin_country": ["JP"],
                "number_of_episodes": 20,
                "tracking_progress": 7,
            },
        )
        show = Show(
            title="Show",
            canonical_source="tvdb",
            tvdb_id=10,
            tmdb_data={"source": "tvdb", "seasons": [{"season_number": 4}]},
        )
        enrich_series_from_show(media, show)
        self.assertEqual(media.tmdb_data["origin_country"], ["JP"])
        self.assertEqual(media.tmdb_data["tracking_progress"], 7)
        self.assertEqual(media.tmdb_data["source"], "tvdb")
        self.assertEqual(media.tmdb_data["seasons"], [{"season_number": 4}])
        self.assertEqual(show.canonical_source, "tvdb")
