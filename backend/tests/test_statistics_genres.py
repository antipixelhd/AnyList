"""Genre cards share canonical facts, personal scores and Browse predicates."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import test_statistics_facts as fixtures
from test_statistics_facts import media, entry, event
from core.statistics_genres import genre_groups, genre_names, poster
from core.browse import browse_page, local_query
from routers.tracking import browse_genres
from fastapi import HTTPException

FILTERS = dict(start=None, end=None, status="", provider=None, region="US", sort="popular", min_votes=0)


class GenreTests(unittest.TestCase):
    def test_canonical_watched_cohort_repeat_time_and_personal_ratings(self):
        one = media(1, "movie", tmdb_id=10, title="A", runtime=90, tmdb_data={"genres": ["Drama", {"name": "Drama"}], "vote_average": 10})
        alias = media(2, "movie", tmdb_id=10, title="A", runtime=90, tmdb_data={"genres": ["Drama"]})
        planned = media(3, "movie", title="Planned", tmdb_data={"genres": ["Drama"]})
        unrated = media(4, "movie", title="Unrated", runtime=60, tmdb_data={"genres": ["Drama"], "vote_average": 10})
        result = fixtures.StatisticsFactsTests().build([(entry(1, 6), one), (entry(2, 9), alias), (entry(3, 10, status="planning"), planned)],
                                            [(event(plays=3), one), (event(), unrated)])
        genre = result["all"]["genres"][0]
        self.assertEqual((genre["titles"], genre["minutes"], genre["rated_titles"], genre["mean_score"]), (2, 330, 1, 9))
        self.assertEqual(genre["top_titles"], [{"key": "tmdb.movie:10", "title": "A", "poster": None, "href": "/title/2", "score": 9}])
        self.assertEqual(result["series"]["genres"], [])

    def test_top_twelve_use_personal_scores_and_deterministic_ties(self):
        facts = [{"data": {"genres": ["Sci-Fi", "Science Fiction"]}, "score": i, "name": f"Film {i:02}", "key": str(i),
                  "minutes": 10, "runtime_missing": 0, "poster": None, "detail_media_id": i} for i in range(1, 13)]
        genre = genre_groups(facts)[0]
        self.assertEqual(genre["titles"], 12)
        self.assertEqual([t["score"] for t in genre["top_titles"]], list(range(12, 0, -1)))
        self.assertEqual(genre["browse_filters"], {"movie": "878", "series": "10765"})
        self.assertEqual(genre_names({"genres": [" drama ", {"name": "Drama"}, None]}), {"Drama"})

    def test_named_filter_validation_and_exact_json_predicate(self):
        self.assertEqual(browse_genres("18,name:Suspense,18"), [18, "name:Suspense"])
        for invalid in ["18,", "name:", "name: Suspense", "name:Suspense\n", "-1", ",18", ",".join(["18"] * 13)]:
            with self.subTest(invalid=invalid), self.assertRaises(HTTPException):
                browse_genres(invalid)
        query = local_query("movie", term="", genres=["name:Suspense"], show_anime=True, **FILTERS)
        params = query.compile().params.values()
        self.assertIn(["Suspense"], params)
        self.assertIn([{"name": "Suspense"}], params)

    def test_posters_do_not_expose_unsafe_urls(self):
        self.assertEqual(poster("/film.jpg"), "https://image.tmdb.org/t/p/w185/film.jpg")
        self.assertIsNone(poster("javascript:alert(1)"))
        self.assertEqual(poster("//example.test/image"), "https://example.test/image")


class NamedBrowseTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_genres_use_local_catalogue_without_remote_broadening(self):
        db = AsyncMock()
        result = MagicMock(); result.scalars.return_value.all.return_value = []
        db.execute.return_value = result
        with patch("core.browse.remote_page", AsyncMock()) as remote:
            response = await browse_page(db, None, term="", media_type="movie", key="key", genres=["name:Suspense"], source="remote", page=1, show_anime=True, **FILTERS)
        remote.assert_not_awaited()
        self.assertEqual(response["source"], "local")
        self.assertEqual(response["results"], [])
        self.assertIsNone(response["notice"])
