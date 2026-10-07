"""Discovery filter, paging and viewer-state regression tests."""

import os
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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
from dependencies import get_current_user, get_optional_user
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
    def test_vote_floor_has_an_inclusive_boundary_and_explicit_zero(self):
        predicate = {k: v for k, v in FILTERS.items() if k != "sort"}
        for votes, expected in [(249, False), (250, True), (251, True)]:
            self.assertEqual(matches({"vote_count": votes}, media_type="movie", min_votes=250, **predicate), expected)
        self.assertFalse(matches({}, media_type="movie", min_votes=250, **predicate))
        self.assertTrue(matches({}, media_type="movie", min_votes=0, **predicate))
        self.assertFalse(matches({"imdb_votes": 200, "vote_count": 10000}, media_type="movie", min_votes=250, **predicate))
        params = remote_params("movie", page=1, **{**FILTERS, "sort": "newest", "min_votes": 250})
        self.assertEqual(params["vote_count.gte"], 250)

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
        params = remote_params(
            "movie", page=1, **{**FILTERS, "status": "upcoming", "sort": "oldest"}
        )
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
                "vote_count": 250,
                "keywords": {"keywords": [{"id": 1, "name": "space"}]},
                "watch/providers": {
                    "results": {"US": {"flatrate": [{"provider_id": 8}]}}
                },
            }
        )
        self.assertEqual(result["keywords"], [{"id": 1, "name": "space"}])
        self.assertEqual(result["vote_count"], 250)
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
    async def test_vote_floor_is_applied_before_feed_pagination_and_survives_detail_fetch(self):
        rows = [{"id": i, "imdb_id": f"tt{i}", "imdb_score": 8, "imdb_votes": 249 if i < 30 else 250} for i in range(60)]
        async def detail(identifier, api_key):
            return {"id": identifier, "vote_count": 1, "watch/providers": {"results": {"US": {"flatrate": [{"provider_id": 8}]}}}}
        with patch("core.mdblist_discovery.ranked_rows", AsyncMock(return_value=(rows, False))), patch("core.tmdb.get_movie", AsyncMock(side_effect=detail)):
            first, items = await remote_page("", "movie", "key", {**FILTERS, "provider": 8, "min_votes": 250}, 1, mdblist_key="mdb")
            second, more = await remote_page("", "movie", "key", {**FILTERS, "provider": 8, "min_votes": 250}, 2, mdblist_key="mdb")
        self.assertEqual([item["id"] for item in items], list(range(30, 54)))
        self.assertEqual([item["id"] for item in more], list(range(54, 60)))
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])

    async def test_browse_search_excludes_low_vote_and_unknown_counts(self):
        with patch("core.tmdb.search_movies", AsyncMock(return_value={"results": [
            {"id": 1, "vote_count": 249}, {"id": 2, "vote_count": 250}, {"id": 3}], "total_pages": 2})):
            data, items = await remote_page("title", "movie", "key", {**FILTERS, "min_votes": 250}, 1)
        self.assertEqual([item["id"] for item in items], [2])
        self.assertEqual(data["total_pages"], 2)

    async def test_filtered_feed_is_paged_after_matching_and_excluding_anime(self):
        rows = [
            {"id": i, "type": "movie", "imdb_id": f"tt{i}", "imdb_score": 8,
             "imdb_votes": 100000, "genre_ids": [18] if i >= 30 else [35],
             "release_date": "2026-01-01", "original_language": "en"}
            for i in range(80)
        ]
        rows[30].update(genre_ids=[18, 16], original_language="ja")
        rows[31]["adult"] = True
        with patch("core.mdblist_discovery.ranked_rows", AsyncMock(return_value=(rows, False))) as feed:
            pages = [await remote_page("", "movie", None,
                     {**FILTERS, "genres": [18]}, page, mdblist_key="mdb", show_anime=False)
                     for page in (1, 2)]
        feed.assert_awaited_with("movie", "popular", "mdb")
        self.assertEqual([i["id"] for i in pages[0][1]], list(range(32, 56)))
        self.assertEqual([i["id"] for i in pages[1][1]], list(range(56, 80)))
        self.assertTrue(pages[0][0]["has_more"])
        self.assertFalse(pages[1][0]["has_more"])

    async def test_streaming_filter_scans_past_unmatched_first_page(self):
        rows = [{"id": i, "type": "movie", "imdb_id": f"tt{i}", "imdb_score": 8,
                 "imdb_votes": 100000, "genre_ids": []} for i in range(1, 60)]
        async def detail(identifier, api_key):
            return {"id": identifier, "watch/providers": {"results": {"US": {
                "flatrate": [{"provider_id": 8 if identifier > 24 else 9}]}}}}
        with patch("core.mdblist_discovery.ranked_rows", AsyncMock(return_value=(rows, False))), patch(
            "core.tmdb.get_movie", AsyncMock(side_effect=detail)
        ):
            first, items = await remote_page("", "movie", "tmdb", {**FILTERS, "provider": 8},
                                             1, mdblist_key="mdb")
            second, more = await remote_page("", "movie", "tmdb", {**FILTERS, "provider": 8},
                                             2, mdblist_key="mdb")
        self.assertEqual([i["id"] for i in items], list(range(25, 49)))
        self.assertEqual([i["id"] for i in more], list(range(49, 60)))
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])

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


class VoteThresholdApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        self.db.execute.return_value = result
        app = FastAPI()
        app.include_router(router, prefix="/tracking")
        async def session():
            yield self.db
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_optional_user] = lambda: None
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        for target, value in [
            ("routers.tracking.catalog_access", None),
            ("routers.tracking.anime_is_visible", True),
            ("core.settings_store.get_user_tmdb_key", "key"),
            ("core.external_scores.effective_mdblist_key", None),
            ("core.tracking_editor.attach_editor_context", None),
        ]:
            replacement = patch(target, AsyncMock(return_value=value))
            replacement.start()
            self.addCleanup(replacement.stop)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_browse_and_sections_share_default_custom_zero_and_validation(self):
        for endpoint, target in [("browse", "core.browse.browse_page"), ("browse/sections", "core.browse.browse_sections")]:
            with patch(target, AsyncMock(return_value={"results": []})) as browse:
                for params, expected in [({}, 250), ({"min_votes": 500}, 500), ({"min_votes": 0}, 0)]:
                    response = await self.client.get(f"/tracking/{endpoint}", params=params)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(browse.await_args.kwargs["min_votes"], expected)
                for value in [-1, 1000001, "invalid"]:
                    response = await self.client.get(f"/tracking/{endpoint}", params={"min_votes": value})
                    self.assertEqual(response.status_code, 422)

    async def test_quick_search_filters_remote_and_local_queries_on_backend_only(self):
        with patch("core.tmdb.search_movies", AsyncMock(return_value={"results": [
            {"id": 1, "title": "Low vote title", "vote_count": 249},
            {"id": 2, "title": "Qualified title", "vote_count": 250},
            {"id": 3, "title": "Unknown vote title"},
        ]})):
            response = await self.client.get("/tracking/catalog", params={"q": "title"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["tmdb_id"] for row in response.json()["results"]], [2])
        query = self.db.execute.await_args_list[0].args[0]
        self.assertIn(250, query.compile().params.values())
        self.assertNotIn("votes", response.text)


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
                imdb_id=f"tt{400000 + i}",
                imdb_rating=6 + i / 100,
                tmdb_rating=9 - i / 100,
                tmdb_data={
                    "genres": ["Drama", "Comedy"],
                    "popularity": i,
                    "imdb": {"votes": 1000 - i, "popularity_rank": i + 1},
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
        app.dependency_overrides[get_current_user] = lambda: self.viewer
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.key_patch = patch(
            "core.settings_store.get_user_tmdb_key", AsyncMock(return_value=None)
        )
        self.key_patch.start()
        self.chart_patch = patch("core.mdblist_discovery.cached_rows", return_value=[])
        self.chart_patch.start()
        self.mdblist_patch = patch("core.external_scores.effective_mdblist_key", AsyncMock(return_value=None))
        self.mdblist_patch.start()
        self.access_patch = patch("routers.tracking.catalog_access", AsyncMock())
        self.access_patch.start()
        self.anime_patch = patch(
            "routers.tracking.anime_is_visible", AsyncMock(return_value=True)
        )
        self.anime_patch.start()

    async def asyncTearDown(self):
        self.key_patch.stop()
        self.chart_patch.stop()
        self.mdblist_patch.stop()
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

    async def test_highest_rated_uses_imdb_not_tmdb_and_excludes_missing_scores(self):
        self.movies[-1].imdb_rating = None
        await self.db.flush()
        response = await self.client.get("/tracking/browse", params={"sort": "score"})
        rows = response.json()["results"]
        self.assertEqual(rows[0]["id"], self.movies[-2].id)
        self.assertNotIn(self.movies[-1].id, [row["id"] for row in rows])
        self.assertNotEqual(rows[0]["id"], self.movies[0].id)

    async def test_popularity_and_trending_ignore_tmdb_order(self):
        for sort in ("popular", "trending"):
            rows = (
                await self.client.get("/tracking/browse", params={"sort": sort})
            ).json()["results"]
            self.assertEqual(rows[0]["id"], self.movies[0].id)
            self.assertEqual(rows[1]["id"], self.movies[1].id)

    async def test_cached_chart_supplies_missing_local_imdb_rating_and_rank(self):
        self.movies[0].imdb_rating = None
        self.movies[1].imdb_rating = None
        await self.db.flush()
        rows = [
            {
                "imdb_id": self.movies[1].imdb_id,
                "rating": 9.9,
                "votes": 10000,
                "rank": 1,
            },
            {
                "imdb_id": self.movies[0].imdb_id,
                "rating": None,
                "votes": 100,
                "rank": 2,
            },
        ]
        with patch("core.mdblist_discovery.cached_rows", return_value=rows):
            for sort in ("score", "trending", "popular"):
                result = (
                    await self.client.get("/tracking/browse", params={"sort": sort})
                ).json()
                self.assertEqual(result["results"][0]["id"], self.movies[1].id)
        with patch("core.mdblist_discovery.cached_rows", return_value=[rows[1]]):
            response = await self.client.get(
                "/tracking/browse", params={"sort": "score"}
            )
            self.assertEqual(response.status_code, 200, response.text)

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

    async def test_broad_cached_feed_stays_below_database_parameter_limit(self):
        rows = [{"imdb_id": f"tt{i}", "rating": 8, "votes": i, "rank": i}
                for i in range(1, 10001)]
        rows.append({"imdb_id": self.movies[0].imdb_id, "rating": 10,
                     "votes": 1000000, "rank": 1})
        with patch("core.mdblist_discovery.cached_rows", return_value=rows):
            response = await self.client.get("/tracking/browse", params={"sort": "score"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["results"][0]["id"], self.movies[0].id)

    async def test_mdblist_browse_works_without_tmdb_and_retains_private_viewer_state(self):
        item = {"id": self.movies[0].tmdb_id, "type": "movie", "title": "MDBList title",
                "imdb_id": self.movies[0].imdb_id, "imdb_score": 9.3, "imdb_votes": 100000,
                "genre_ids": [18], "release_date": "2021-02-03"}
        with patch("core.external_scores.effective_mdblist_key", AsyncMock(return_value="mdb")), patch(
            "core.mdblist_discovery.ranked_rows", AsyncMock(return_value=([item], False))
        ), patch("core.tmdb.find_by_external_id", AsyncMock()) as resolve:
            response = await self.client.get("/tracking/browse", params={"sort": "score"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["source"], "remote")
        self.assertFalse(result["has_more"])
        self.assertEqual(result["results"][0]["imdb_score"], 9.3)
        self.assertEqual(result["results"][0]["list_status"], "watching")
        resolve.assert_not_awaited()

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

    async def test_editor_snapshots_include_complete_viewer_fields_without_provider_calls(self):
        from sqlalchemy import select
        entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.owner.id,
            TrackedEntry.media_id == self.movies[0].id,
        ))).scalar_one()
        entry.notes = "Viewer notes"
        entry.favorite = True
        entry.manual_score = 8.5
        entry.start_date = date(2021, 2, 3)
        entry.rewatch_count = 2
        self.movies[0].backdrop_path = "/background.jpg"
        await self.db.commit()
        with patch("core.external_scores.refresh_external_scores", AsyncMock()) as refresh:
            response = await self.client.get(f"/tracking/editor/{self.movies[0].id}")
        self.assertEqual(response.status_code, 200, response.text)
        refresh.assert_not_awaited()
        row = response.json()
        self.assertEqual(row["entry"]["notes"], "Viewer notes")
        self.assertEqual(row["entry"]["manual_score"], 8.5)
        self.assertTrue(row["entry"]["favorite"])
        self.assertEqual(row["entry"]["start_date"], "2021-02-03")
        self.assertEqual(row["entry"]["rewatch_count"], 2)
        self.assertEqual(row["backdrop"], "/background.jpg")
        self.assertFalse(row["editor_library"]["available"])
        self.viewer = self.other
        response = await self.client.get(f"/tracking/editor/{self.movies[0].id}")
        self.assertIsNone(response.json()["entry"])
        self.assertNotIn("Viewer notes", response.text)

    async def test_browse_and_search_seed_private_editor_snapshots_for_known_and_new_titles(self):
        response = await self.client.get("/tracking/browse", params={"sort": "oldest"})
        rows = response.json()["results"]
        own = next(row for row in rows if row["id"] == self.movies[0].id)
        other = next(row for row in rows if row["id"] == self.movies[1].id)
        self.assertEqual(own["entry"]["status"], "watching")
        self.assertIn("notes", own["entry"])
        self.assertIsNone(other["entry"])
        with (
            patch("core.settings_store.get_user_tmdb_key", AsyncMock(return_value="key")),
            patch("core.tmdb.search_movies", AsyncMock(return_value={"results":[
                {"id":400000,"title":"Known remote","vote_count":250},
                {"id":999999,"title":"New remote","vote_count":250},
            ]})),
        ):
            response = await self.client.get("/tracking/catalog", params={"q":"Remote"})
        rows = response.json()["results"]
        known = next(row for row in rows if row["tmdb_id"] == 400000)
        new = next(row for row in rows if row["tmdb_id"] == 999999)
        self.assertEqual(known["id"], self.movies[0].id)
        self.assertEqual(known["entry"]["status"], "watching")
        self.assertIsNone(new["id"])
        self.assertIsNone(new["entry"])
        self.viewer = None
        response = await self.client.get("/tracking/browse")
        self.assertTrue(all("entry" not in row for row in response.json()["results"]))

    async def test_editor_library_snapshot_respects_private_intents_and_avoids_credentials(self):
        from models import MediaServerConnection
        from models.streaming_library import StreamingLibraryIntent
        self.db.add_all([
            MediaServerConnection(user_id=self.owner.id, type="stremio", name="Stream",
                                  url="https://example.test", token="private-token"),
            StreamingLibraryIntent(user_id=self.owner.id, media_id=self.movies[0].id, desired=True),
            StreamingLibraryIntent(user_id=self.other.id, media_id=self.movies[1].id, desired=True),
        ])
        await self.db.commit()
        response = await self.client.get("/tracking/browse", params={"sort":"oldest"})
        rows = response.json()["results"]
        own = next(row for row in rows if row["id"] == self.movies[0].id)
        other = next(row for row in rows if row["id"] == self.movies[1].id)
        self.assertEqual(own["editor_library"], {"available":True, "desired":True})
        self.assertEqual(other["editor_library"], {"available":True, "desired":False})
        self.assertNotIn("private-token", response.text)
        self.viewer = self.other
        response = await self.client.get(f"/tracking/editor/{self.movies[1].id}")
        self.assertEqual(response.json()["editor_library"], {"available":False, "desired":False})

    async def test_remote_cards_resolve_local_ids_and_viewer_state(self):
        with (
            patch("core.external_scores.effective_mdblist_key", AsyncMock(return_value="mdb")),
            patch(
                "core.settings_store.get_user_tmdb_key", AsyncMock(return_value="key")
            ),
            patch(
                "core.browse.remote_page",
                AsyncMock(
                    return_value=(
                        {"total_pages": 2},
                        [
                            {
                                "id": 400000,
                                "title": "Remote name",
                                "genre_ids": [18],
                                "release_date": "2021-02-03",
                            }
                        ],
                    )
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
            patch("core.external_scores.effective_mdblist_key", AsyncMock(return_value="mdb")),
            patch(
                "core.settings_store.get_user_tmdb_key", AsyncMock(return_value="key")
            ),
            patch("core.browse.remote_page", AsyncMock(side_effect=OSError("offline"))),
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
