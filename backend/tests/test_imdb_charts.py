"""IMDb ordering and outage behavior are independent of TMDB scores."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault("SECRET_KEY", "imdb-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import imdb_charts


def page(edges):
    data = {"props": {"pageProps": {"pageData": {"chartTitles": {"edges": edges}}}}}
    return (
        '<script type="application/json" id="__NEXT_DATA__">'
        + json.dumps(data)
        + "</script>"
    )


def edge(identifier, rank=1, votes=100, rating=8):
    return {
        "currentRank": rank,
        "node": {
            "id": identifier,
            "ratingsSummary": {"aggregateRating": rating, "voteCount": votes},
        },
    }


class ChartTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.setting = patch.object(
            imdb_charts.settings, "data_dir", Path(self.directory.name)
        )
        self.setting.start()
        imdb_charts._cache.clear()
        imdb_charts._locks.clear()
        imdb_charts._retry_after.clear()

    def tearDown(self):
        self.setting.stop()
        self.directory.cleanup()
        imdb_charts._cache.clear()
        imdb_charts._locks.clear()
        imdb_charts._retry_after.clear()

    def test_parser_preserves_chart_rank_not_rating_or_vote_order(self):
        rows = imdb_charts.parse_chart(
            page(
                [
                    edge("tt1", rank=2, votes=999, rating=9.9),
                    edge("tt2", rank=1, votes=2, rating=6),
                    edge("tt2", rank=3),
                ]
            )
        )
        self.assertEqual([row["imdb_id"] for row in rows], ["tt2", "tt1"])
        self.assertEqual(rows[0]["rating"], 6)
        for html in [
            "challenge",
            page([]),
            page([edge("tt1", votes=-1)]),
            page([edge("tt1", rating=99)]),
        ]:
            with self.assertRaises(imdb_charts.IMDbUnavailable):
                imdb_charts.parse_chart(html)

    async def test_feed_urls_memory_and_persistent_cache(self):
        get = AsyncMock(
            return_value=httpx.Response(
                200,
                text=page([edge("tt1")]),
                request=httpx.Request("GET", "https://www.imdb.com/"),
            )
        )
        with patch("httpx.AsyncClient.get", get):
            rows, stale = await imdb_charts.ranked_rows("series", "trending")
            again, _ = await imdb_charts.ranked_rows("series", "trending")
            imdb_charts._cache.clear()
            persisted, _ = await imdb_charts.ranked_rows("series", "trending")
        self.assertFalse(stale)
        self.assertEqual(rows, again)
        self.assertEqual(rows, persisted)
        get.assert_awaited_once()
        self.assertEqual(get.call_args.args[0], "https://www.imdb.com/chart/tvmeter/")

    async def test_stale_chart_survives_outage_but_is_reported_and_bounded(self):
        rows = [{"imdb_id": "tt1", "rank": 1, "rating": 8, "votes": 100}]
        imdb_charts._cache[("movie", "top")] = (1000, rows)
        with (
            patch(
                "core.imdb_charts.time.time", return_value=1000 + imdb_charts.TTL + 1
            ),
            patch(
                "httpx.AsyncClient.get",
                AsyncMock(side_effect=httpx.ConnectError("offline")),
            ) as get,
        ):
            self.assertEqual(
                await imdb_charts.ranked_rows("movie", "score"), (rows, True)
            )
            self.assertEqual(
                await imdb_charts.ranked_rows("movie", "score"), (rows, True)
            )
            get.assert_awaited_once()
        with (
            patch(
                "core.imdb_charts.time.time",
                return_value=1000 + imdb_charts.MAX_STALE + 1,
            ),
            patch(
                "httpx.AsyncClient.get",
                AsyncMock(side_effect=httpx.ConnectError("offline")),
            ),
        ):
            with self.assertRaises(imdb_charts.IMDbUnavailable):
                await imdb_charts.ranked_rows("movie", "score")

    async def test_popularity_uses_imdb_votes_and_deduplicates_chart_union(self):
        popular = [{"imdb_id": "tt1", "rank": 1, "rating": 6, "votes": 10}]
        top = [
            {"imdb_id": "tt2", "rank": 1, "rating": 8, "votes": 100},
            {"imdb_id": "tt1", "rank": 2, "rating": 6, "votes": 10},
        ]
        with patch(
            "core.imdb_charts._chart",
            AsyncMock(side_effect=[(popular, False), (top, False)]),
        ):
            rows, stale = await imdb_charts.ranked_rows("movie", "popular")
        self.assertEqual([row["imdb_id"] for row in rows], ["tt2", "tt1"])
        self.assertFalse(stale)

    async def test_unavailable_chart_never_substitutes_tmdb_rankings(self):
        with (
            patch(
                "httpx.AsyncClient.get",
                AsyncMock(side_effect=httpx.ConnectError("offline")),
            ),
            patch("core.tmdb._get", AsyncMock()) as tmdb,
        ):
            with self.assertRaises(imdb_charts.IMDbUnavailable):
                await imdb_charts.ranked_rows("movie", "popular")
        tmdb.assert_not_awaited()
