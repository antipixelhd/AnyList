"""Discovery snapshots consume quota per refresh, not per viewer or filter."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "discovery-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import mdblist, mdblist_discovery as discovery


def title(identifier=1, *, rating=8, votes=100000, rank=1):
    return {
        "id": identifier,
        "title": f"Title {identifier}",
        "imdb_id": f"tt{identifier}",
        "rank": rank,
        "genres": ["drama", "sci-fi"],
        "language": "en",
        "country": "us",
        "release_date": "2026-01-01",
        "poster": "https://image.tmdb.org/t/p/w200/poster.jpg",
        "ratings": [
            {"source": "imdb", "value": rating, "votes": votes},
            {"source": "tmdb", "value": 75},
        ],
    }


def response(items, cursor=None):
    return {
        "movies": items,
        "shows": [],
        "pagination": {"next_cursor": cursor, "has_more": bool(cursor)},
    }


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.setting = patch.object(
            discovery.settings, "data_dir", Path(self.directory.name)
        )
        self.setting.start()
        for cache in (discovery._cache, discovery._locks, discovery._retry_after):
            cache.clear()

    def tearDown(self):
        self.setting.stop()
        self.directory.cleanup()
        for cache in (discovery._cache, discovery._locks, discovery._retry_after):
            cache.clear()

    async def test_cursor_snapshot_shared_by_rating_and_vote_sorts_and_persisted(self):
        request = AsyncMock(
            side_effect=[
                response([title(1, rating=9)], "next"),
                response([title(2, votes=200000)]),
            ]
        )
        with patch("core.mdblist._request", request):
            popular, stale = await discovery.ranked_rows("movie", "popular", "key")
            scored, _ = await discovery.ranked_rows("movie", "score", "key")
            discovery._cache.clear()
            persisted, _ = await discovery.ranked_rows("movie", "popular", "key")
        self.assertFalse(stale)
        self.assertEqual([r["id"] for r in popular], [2, 1])
        self.assertEqual([r["id"] for r in scored], [1, 2])
        self.assertEqual(popular, persisted)
        self.assertEqual(request.await_count, 2)
        self.assertEqual(request.call_args.kwargs["params"]["cursor"], "next")
        self.assertEqual(popular[0]["vote_average"], 7.5)
        self.assertEqual(popular[0]["genre_ids"], [18, 878])

    async def test_combined_moviemeter_feed_is_shared_across_types_and_concurrent_viewers(
        self,
    ):
        data = response([title(1, rank=2), title(2, rank=1)])
        data["shows"] = [title(3)]
        with patch("core.mdblist._request", AsyncMock(return_value=data)) as request:
            results = await asyncio.gather(
                discovery.ranked_rows("movie", "trending", "key"),
                discovery.ranked_rows("series", "trending", "key"),
                discovery.ranked_rows("movie", "trending", "key"),
            )
        request.assert_awaited_once()
        self.assertEqual(request.call_args.args[1], "/lists/official/moviemeter/items")
        self.assertEqual([r["id"] for r in results[0][0]], [2, 1])
        self.assertEqual(results[1][0][0]["genre_ids"], [18, 10765])

    async def test_separate_accounts_do_not_reuse_credentials_or_cache(self):
        with patch(
            "core.mdblist._request", AsyncMock(return_value=response([title()]))
        ) as request:
            await discovery.ranked_rows("movie", "popular", "secret-one")
            await discovery.ranked_rows("movie", "popular", "secret-two")
        self.assertEqual(request.await_count, 2)
        for path in Path(self.directory.name).rglob("*.json"):
            self.assertNotIn("secret-", path.name + path.read_text())

    async def test_rating_sort_rejects_tiny_vote_samples(self):
        with patch(
            "core.mdblist._request",
            AsyncMock(
                return_value=response(
                    [
                        title(1, rating=10, votes=3),
                        title(2, rating=8, votes=30000),
                    ]
                )
            ),
        ):
            rows, _ = await discovery.ranked_rows("movie", "score", "key")
        self.assertEqual([r["id"] for r in rows], [2])

    async def test_daily_refresh_stale_fallback_and_quota_cooldown(self):
        with patch(
            "core.mdblist._request", AsyncMock(return_value=response([title()]))
        ):
            with patch("core.mdblist_discovery.time.time", return_value=1000):
                await discovery.ranked_rows("movie", "popular", "key")
        later = 1000 + discovery.CATALOGUE_TTL + 1
        with (
            patch("core.mdblist_discovery.time.time", return_value=later),
            patch(
                "core.mdblist._request",
                AsyncMock(side_effect=mdblist.MDBListDailyLimitError("quota")),
            ) as request,
        ):
            self.assertTrue((await discovery.ranked_rows("movie", "popular", "key"))[1])
            self.assertTrue((await discovery.ranked_rows("movie", "score", "key"))[1])
            request.assert_awaited_once()
        with (
            patch(
                "core.mdblist_discovery.time.time",
                return_value=1000 + discovery.MAX_STALE + 1,
            ),
            patch(
                "core.mdblist._request",
                AsyncMock(side_effect=mdblist.MDBListAPIError("offline")),
            ),
        ):
            with self.assertRaises(discovery.DiscoveryUnavailable):
                await discovery.ranked_rows("movie", "popular", "key")

    async def test_repeated_cursor_does_not_publish_partial_snapshot(self):
        with patch(
            "core.mdblist._request", AsyncMock(return_value=response([title()], "same"))
        ) as request:
            with self.assertRaises(discovery.DiscoveryUnavailable):
                await discovery.ranked_rows("movie", "popular", "key")
            with self.assertRaises(discovery.DiscoveryUnavailable):
                await discovery.ranked_rows("movie", "popular", "key")
        self.assertEqual(request.await_count, 2)
        self.assertFalse(discovery._cache)

    async def test_missing_key_makes_no_network_request(self):
        with patch("core.mdblist._request", AsyncMock()) as request:
            with self.assertRaises(discovery.DiscoveryUnavailable):
                await discovery.ranked_rows("movie", "popular", None)
        request.assert_not_awaited()

    async def test_trending_refresh_does_not_expire_daily_catalogue(self):
        with patch(
            "core.mdblist._request", AsyncMock(return_value=response([title()]))
        ) as request:
            with patch("core.mdblist_discovery.time.time", return_value=1000):
                await discovery.ranked_rows("movie", "trending", "key")
                await discovery.ranked_rows("movie", "popular", "key")
            with patch(
                "core.mdblist_discovery.time.time",
                return_value=1000 + discovery.TRENDING_TTL + 1,
            ):
                await discovery.ranked_rows("movie", "trending", "key")
                await discovery.ranked_rows("movie", "score", "key")
        self.assertEqual(request.await_count, 3)

    async def test_invalid_snapshot_is_not_published(self):
        with patch("core.mdblist._request", AsyncMock(return_value={"movies": []})):
            with self.assertRaises(discovery.DiscoveryUnavailable):
                await discovery.ranked_rows("movie", "popular", "key")
        self.assertFalse(discovery._cache)

    def test_invalid_scores_and_ids_are_not_used(self):
        self.assertIsNone(discovery.normalize({"id": -1}, "movie"))
        self.assertIsNone(discovery.normalize({"id": True}, "movie"))
        row = discovery.normalize(title(rating=float("nan"), votes=-1), "movie")
        self.assertIsNone(row["imdb_score"])
        self.assertEqual(row["imdb_votes"], 0)
