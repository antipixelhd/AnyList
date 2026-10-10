import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "country-test-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import country_backfill as backfill, catalogue_normalize, openlibrary
from core.countries import game_countries, numeric_country, publication_countries, screen_countries
from core.statistics_facts import countries_of
from models.base import MediaType


class CountryProjectionTests(unittest.TestCase):
    def test_origin_has_priority_over_production_for_movies_and_series(self):
        data = {"origin_country": ["GB"], "production_countries": [{"iso_3166_1": "US"}]}
        for kind in ("movie", "series"):
            self.assertEqual(countries_of(data, kind), ["GB"])
        self.assertEqual(screen_countries(data), {"countries": ["GB"], "country_basis": "origin"})
        self.assertEqual(countries_of({"production_countries": ["US"]}, "movie"), ["US"])

    def test_game_distinct_developers_take_priority_over_parent_publisher(self):
        raw = {"id": 1, "name": "Game", "involved_companies": [
            {"id": 1, "developer": True, "company": {"id": 10, "name": "Studio Montreal", "country": 124}},
            {"id": 2, "developer": True, "company": {"id": 11, "name": "Studio Stockholm", "country": 752}},
            {"id": 3, "publisher": True, "company": {"id": 12, "name": "Parent", "country": 250}},
        ]}
        doc = catalogue_normalize.normalize_igdb(raw)
        self.assertEqual(doc["work"]["attributes"]["countries"], ["CA", "SE"])
        self.assertEqual(doc["work"]["attributes"]["publisher_countries"], ["FR"])
        self.assertEqual(doc["credits"][0]["contributor"]["attributes"]["countries"], ["CA"])
        raw["involved_companies"][0]["company"].pop("country")
        raw["involved_companies"][1]["company"].pop("country")
        self.assertEqual(game_countries(raw)["countries"], ["FR"])
        self.assertEqual(game_countries(raw)["country_basis"], "publisher")
        for value in (True, 0, 999, "124"):
            self.assertIsNone(numeric_country(value))

    def test_publication_codes_are_marc_not_iso_and_places_are_not_guessed(self):
        for marc, code in (("nyu", "US"), ("enk", "GB"), ("gw", "DE"), ("br", "MM"),
                           ("bl", "BR"), ("ch", "TW"), ("cg", "CD"), ("onc", "CA")):
            self.assertEqual(publication_countries({"publish_country": marc}), [code])
        self.assertEqual(publication_countries({"publish_places": ["Paris, France", "Germany"]}), ["DE", "FR"])
        self.assertEqual(publication_countries({"publish_country": "xx", "publish_places": ["London", "Cambridge", "New York"]}), [])
        self.assertEqual(publication_countries({"publish_country": "nyu", "publish_places": ["France"]}), ["US"])

    def test_book_keeps_edition_country_separate_from_work_subject_places(self):
        raw = {"key": "/works/OL1W", "title": "Book", "subject_places": ["Japan"], "_editions": [
            {"key": "/books/OL1M", "works": [{"key": "/works/OL1W"}], "publish_country": "nyu"},
            {"key": "/books/OL2M", "works": [{"key": "/works/OL1W"}], "publish_country": "gw"},
        ]}
        doc = openlibrary.normalize(raw)
        self.assertEqual(doc["work"]["attributes"]["countries"], ["DE", "US"])
        self.assertEqual(doc["editions"][0]["entity"]["attributes"]["countries"], ["US"])
        self.assertEqual(doc["editions"][1]["entity"]["attributes"]["countries"], ["DE"])


class LegacyCountryBackfillTests(unittest.IsolatedAsyncioTestCase):
    def session(self, movies, shows=()):
        return Row(scalars=AsyncMock(side_effect=[movies, shows]), commit=AsyncMock())

    def movie(self, id=1):
        return Row(id=id, media_type=MediaType.movie, tmdb_id=id, tvdb_id=None,
                   tmdb_data={"runtime": 100, "tracking_keep": True})

    async def test_exact_identity_required_and_unrelated_fields_preserved(self):
        rows = [self.movie(1), self.movie(2)]
        with patch.object(backfill.tmdb, "get_movie", AsyncMock(side_effect=[
            {"id": 1, "origin_country": ["GB"], "production_countries": [{"iso_3166_1": "US"}]},
            {"id": 3, "origin_country": ["US"]},
        ])):
            result = await backfill.backfill_legacy_countries(self.session(rows), "private-key")
        self.assertEqual(result["recovered"], 1)
        self.assertEqual(rows[0].tmdb_data["origin_country"], ["GB"])
        self.assertNotIn("origin_country", rows[1].tmdb_data)
        self.assertEqual(rows[0].tmdb_data["runtime"], 100)
        self.assertTrue(rows[0].tmdb_data["tracking_keep"])
        self.assertFalse(backfill.due(rows[1].tmdb_data, datetime.now(timezone.utc)))
        self.assertTrue(backfill.due(rows[1].tmdb_data, datetime.now(timezone.utc) + timedelta(days=8)))

    async def test_failure_does_not_stop_later_records(self):
        rows = [self.movie(1), self.movie(2)]
        with patch.object(backfill.tmdb, "get_movie", AsyncMock(side_effect=[
            RuntimeError("private provider URL"), {"id": 2, "origin_country": ["US"]},
        ])):
            result = await backfill.backfill_legacy_countries(self.session(rows), "key")
        self.assertEqual((result["failed"], result["recovered"]), (1, 1))

    async def test_series_tvdb_fallback_and_shared_fetch(self):
        series = Row(id=1, media_type=MediaType.series, tmdb_id=10, tvdb_id=20, tmdb_data={})
        show = Row(id=2, tmdb_id=10, tvdb_id=20, tmdb_data={})
        with patch.object(backfill.tmdb, "get_show", AsyncMock(return_value={"id": 10})) as tmdb_fetch, \
             patch.object(backfill.tvdb, "get_series", AsyncMock(return_value={"id": 20, "originalCountry": "jpn"})) as tvdb_fetch:
            result = await backfill.backfill_legacy_countries(self.session([series], [show]), "key", tvdb_api_key="other-key")
        self.assertEqual(result["recovered"], 2)
        self.assertEqual(show.tmdb_data["origin_country"], ["JP"])
        tmdb_fetch.assert_awaited_once()
        tvdb_fetch.assert_awaited_once()

    async def test_recent_attempt_skipped_even_with_missing_country(self):
        row = self.movie()
        row.tmdb_data["country_backfill_attempted_at"] = datetime.now(timezone.utc).isoformat()
        with patch.object(backfill.tmdb, "get_movie", AsyncMock()) as fetch:
            result = await backfill.backfill_legacy_countries(self.session([row]), "key")
        self.assertEqual(result["examined"], 0)
        fetch.assert_not_awaited()
