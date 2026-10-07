"""Discovery filter, paging and viewer-state regression tests."""

import os
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "browse-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from core.browse import (
    matches,
    metadata_fields,
    remote_page,
    remote_params,
    section_specs,
)
from db import get_db
from dependencies import get_optional_user
from models import Media, User
from models.base import MediaType
from models.tracking import TrackedEntry
from routers.tracking import browse_ids, router

FILTERS = dict(
    genres=[],
    tags=[],
    start=None,
    end=None,
    status="",
    provider=None,
    region="US",
    sort="popular",
)


class FilterTests(unittest.TestCase):
    def test_season_boundaries_include_leap_days_and_year_rollover(self):
        for today, start, end in [
            (date(2024, 2, 29), "2024-01-01", "2024-03-31"),
            (date(2026, 12, 31), "2026-10-01", "2026-12-31"),
        ]:
            values = section_specs(today)[1][1]
            self.assertEqual((values["start"], values["end"]), (start, end))

    def test_filters_use_and_for_multiple_genres_and_tags(self):
        params = remote_params(
            "series",
            page=3,
            **{
                **FILTERS,
                "genres": [18, 35],
                "tags": [1, 2],
                "status": "airing",
                "provider": 8,
                "region": "DE",
                "start": "2020-01-01",
                "end": "2025-12-31",
                "sort": "newest",
            },
        )
        self.assertEqual(params["with_genres"], "18,35")
        self.assertEqual(params["with_keywords"], "1,2")
        self.assertEqual(params["first_air_date.gte"], "2020-01-01")
        self.assertEqual(params["with_status"], "0")
        self.assertEqual(params["with_watch_providers"], 8)
        self.assertEqual(params["watch_region"], "DE")
        self.assertEqual(params["sort_by"], "first_air_date.desc")

    def test_upcoming_movies_start_after_today(self):
        params = remote_params("movie", page=1, **{**FILTERS, "status": "upcoming"})
        self.assertEqual(
            params["primary_release_date.gte"],
            (date.today() + timedelta(days=1)).isoformat(),
        )

    def test_detail_search_respects_every_filter_and_excludes_rentals(self):
        item = {
            "genres": [{"id": 18}, {"id": 35}],
            "keywords": {"results": [{"id": 1, "name": "space"}]},
            "first_air_date": "2021-02-03",
            "status": "Returning Series",
            "watch/providers": {
                "results": {
                    "DE": {"flatrate": [{"provider_id": 8}]},
                    "US": {"rent": [{"provider_id": 8}]},
                }
            },
        }
        predicate = {k: v for k, v in FILTERS.items() if k != "sort"}
        predicate.update(
            genres=[18, 35],
            tags=[1],
            start="2020-01-01",
            end="2022-01-01",
            status="airing",
            provider=8,
            region="DE",
        )
        self.assertTrue(matches(item, media_type="series", **predicate))
        for changes in [
            {"genres": [18, 80]},
            {"tags": [2]},
            {"region": "US"},
            {"start": "2022-01-01"},
            {"status": "finished"},
        ]:
            self.assertFalse(
                matches(item, media_type="series", **{**predicate, **changes})
            )

    def test_metadata_keeps_tags_and_regions_for_local_browsing(self):
        result = metadata_fields(
            {
                "keywords": {"keywords": [{"id": 1, "name": "space"}]},
                "watch/providers": {
                    "results": {"US": {"flatrate": [{"provider_id": 8}]}}
                },
            }
        )
        self.assertEqual(result["keywords"], [{"id": 1, "name": "space"}])
        self.assertEqual(
            result["watch_providers"]["US"]["flatrate"][0]["provider_id"], 8
        )

    def test_filter_ids_are_bounded_and_deduplicated(self):
        self.assertEqual(browse_ids("18,35,18"), [18, 35])
        from fastapi import HTTPException

        for value in [
            "0",
            "-1",
            "hello",
            "2147483647",
            ",".join(str(i) for i in range(1, 14)),
        ]:
            with self.assertRaises(HTTPException):
                browse_ids(value)


class RemotePagingTests(unittest.IsolatedAsyncioTestCase):
    async def test_trending_uses_weekly_feed_and_applies_genre_filters(self):
        with patch(
            "core.tmdb._get",
            AsyncMock(
                return_value={
                    "results": [
                        {"id": 1, "genre_ids": [18]},
                        {"id": 2, "genre_ids": [35]},
                    ],
                    "total_pages": 5,
                }
            ),
        ) as get:
            data, items = await remote_page(
                "", "movie", "key", {**FILTERS, "sort": "trending", "genres": [18]}, 2
            )
        self.assertTrue(get.call_args.args[0].endswith("/trending/movie/week"))
        self.assertEqual(get.call_args.kwargs["params"], {"page": 2})
        self.assertEqual(data["total_pages"], 5)
        self.assertEqual([item["id"] for item in items], [1])

    async def test_filtered_search_uses_requested_page_and_keeps_empty_intermediate_pages(
        self,
    ):
        with (
            patch(
                "core.tmdb.search_shows",
                AsyncMock(return_value={"results": [{"id": 1}], "total_pages": 3}),
            ) as search,
            patch(
                "core.tmdb.get_show",
                AsyncMock(return_value={"id": 1, "status": "Ended"}),
            ),
        ):
            data, items = await remote_page(
                "series", "series", "key", {**FILTERS, "status": "airing"}, 2
            )
        search.assert_awaited_once_with("series", page=2, api_key="key")
        self.assertEqual(data["total_pages"], 3)
        self.assertEqual(items, [])


@unittest.skipUnless(
    os.getenv("TRACKING_TEST_DATABASE_URL"), "Requires disposable PostgreSQL database"
)
class BrowseApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TRACKING_TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.db = AsyncSession(
            bind=self.connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        self.owner = User(
            username="browse-owner",
            email="browse-owner@example.test",
            api_key="browse-owner",
        )
        self.other = User(
            username="browse-other",
            email="browse-other@example.test",
            api_key="browse-other",
        )
        self.db.add_all([self.owner, self.other])
        await self.db.flush()
        self.movies = [
            Media(
                tmdb_id=400000 + i,
                title=f"Browse Film {i:02}",
                media_type=MediaType.movie,
                release_date="2021-02-03",
                tmdb_data={
                    "genres": ["Drama", "Comedy"],
                    "popularity": 100 - i,
                    "keywords": [{"id": 99999, "name": "space adventure"}],
                    "watch_providers": {
                        "US": {
                            "flatrate": [{"provider_id": 8, "provider_name": "Netflix"}]
                        }
                    },
                },
            )
            for i in range(55)
        ]
        self.db.add_all(self.movies)
        await self.db.flush()
        self.db.add_all(
            [
                TrackedEntry(
                    user_id=self.owner.id,
                    media_id=self.movies[0].id,
                    status="watching",
                    season_scores={},
                ),
                TrackedEntry(
                    user_id=self.other.id,
                    media_id=self.movies[1].id,
                    status="completed",
                    season_scores={},
                ),
            ]
        )
        await self.db.commit()
        self.viewer = self.owner
        app = FastAPI()
        app.include_router(router, prefix="/tracking")

        async def session():
            yield self.db

        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_optional_user] = lambda: self.viewer
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.key_patch = patch(
            "core.settings_store.get_user_tmdb_key", AsyncMock(return_value=None)
        )
        self.key_patch.start()
        self.access_patch = patch("routers.tracking.catalog_access", AsyncMock())
        self.access_patch.start()
        self.anime_patch = patch(
            "routers.tracking.anime_is_visible", AsyncMock(return_value=True)
        )
        self.anime_patch.start()

    async def asyncTearDown(self):
        self.key_patch.stop()
        self.access_patch.stop()
        self.anime_patch.stop()
        await self.client.aclose()
        await self.db.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def test_category_previews_match_view_all_and_keep_viewer_state_private(self):
        self.movies[2].release_date = date.today().isoformat()
        self.movies[3].release_date = (date.today() + timedelta(days=365)).isoformat()
        await self.db.flush()
        response = await self.client.get("/tracking/browse/sections")
        self.assertEqual(response.status_code, 200, response.text)
        sections = response.json()["sections"]
        self.assertEqual(len(sections), 5)
        self.assertEqual(sections[0]["filters"], {"sort": "trending"})
        for section in sections:
            full = (
                await self.client.get("/tracking/browse", params=section["filters"])
            ).json()
            self.assertEqual(
                [row["id"] for row in section["results"]],
                [row["id"] for row in full["results"][:6]],
            )
            for row in section["results"]:
                if row["id"] == self.movies[0].id:
                    self.assertEqual(row["list_status"], "watching")
                if row["id"] == self.movies[1].id:
                    self.assertIsNone(row["list_status"])
        self.assertEqual(
            [row["id"] for row in sections[1]["results"]], [self.movies[2].id]
        )
        self.assertIn(self.movies[3].id, [row["id"] for row in sections[-1]["results"]])
        self.viewer = None
        anonymous = (await self.client.get("/tracking/browse/sections")).json()
        self.assertTrue(
            all(
                row["list_status"] is None
                for section in anonymous["sections"]
                for row in section["results"]
            )
        )

    async def test_local_pagination_has_no_duplicates_and_ends(self):
        pages = [
            (
                await self.client.get(
                    "/tracking/browse", params={"source": "local", "page": page}
                )
            ).json()
            for page in (1, 2, 3)
        ]
        self.assertEqual([len(p["results"]) for p in pages], [24, 24, 7])
        self.assertEqual([p["has_more"] for p in pages], [True, True, False])
        self.assertEqual(len({r["id"] for p in pages for r in p["results"]}), 55)
        self.assertEqual(pages[0]["results"][0]["list_status"], "watching")
        self.assertIsNone(pages[0]["results"][1]["list_status"])

    async def test_combined_local_filters_and_regional_streaming(self):
        filters = {
            "genres": "18,35",
            "tags": "99999",
            "start": "2020-01-01",
            "end": "2022-01-01",
            "status": "released",
            "provider": 8,
            "region": "US",
        }
        response = await self.client.get("/tracking/browse", params=filters)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["results"]), 24)
        response = await self.client.get(
            "/tracking/browse", params={**filters, "region": "DE"}
        )
        self.assertEqual(response.json()["results"], [])
        response = await self.client.get(
            "/tracking/browse", params={**filters, "genres": "18,80"}
        )
        self.assertEqual(response.json()["results"], [])

    async def test_fuzzy_search_and_literal_wildcards(self):
        response = await self.client.get(
            "/tracking/browse", params={"q": "Browze Flm 00"}
        )
        self.assertTrue(response.json()["results"])
        response = await self.client.get("/tracking/browse", params={"q": "%"})
        self.assertEqual(response.json()["results"], [])

    async def test_local_facets_retain_stored_services_and_tags(self):
        response = await self.client.get("/tracking/browse/facets")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn({"id": 8, "name": "Netflix"}, response.json()["providers"])
        response = await self.client.get("/tracking/browse/tags", params={"q": "space"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(
            {"id": 99999, "name": "space adventure"}, response.json()["results"]
        )

    async def test_remote_cards_resolve_local_ids_and_viewer_state(self):
        with (
            patch(
                "core.settings_store.get_user_tmdb_key", AsyncMock(return_value="key")
            ),
            patch(
                "core.tmdb._get",
                AsyncMock(
                    return_value={
                        "results": [
                            {
                                "id": 400000,
                                "title": "Remote name",
                                "genre_ids": [18],
                                "release_date": "2021-02-03",
                            }
                        ],
                        "total_pages": 2,
                    }
                ),
            ),
        ):
            response = await self.client.get("/tracking/browse")
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["results"][0]
        self.assertEqual(row["id"], self.movies[0].id)
        self.assertEqual(row["list_status"], "watching")
        self.assertTrue(response.json()["has_more"])

    async def test_remote_failure_does_not_change_sources_mid_scroll(self):
        with (
            patch(
                "core.settings_store.get_user_tmdb_key", AsyncMock(return_value="key")
            ),
            patch("core.tmdb._get", AsyncMock(side_effect=OSError("offline"))),
        ):
            first = await self.client.get("/tracking/browse")
            second = await self.client.get(
                "/tracking/browse", params={"page": 2, "source": "remote"}
            )
        self.assertEqual(first.json()["source"], "local")
        self.assertEqual(second.status_code, 502)

    async def test_invalid_filters_are_rejected(self):
        for filters in [
            {"genres": "123456"},
            {"start": "2022-01-01", "end": "2020-01-01"},
            {"status": "airing"},
            {"page": 0},
            {"region": "../US"},
        ]:
            response = await self.client.get("/tracking/browse", params=filters)
            self.assertEqual(response.status_code, 422, response.text)
