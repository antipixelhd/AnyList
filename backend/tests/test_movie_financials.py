import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.movie_financials import movie_financials
from core.descriptive_metadata import tmdb_fields, tvdb_fields
from core.catalogue_normalize import normalize_tmdb


class MovieFinancialsTests(unittest.TestCase):
    def test_known_amounts_survive_missing_or_unknown_refresh_values(self):
        previous = {'budget': 150_000_000, 'revenue': 825_000_000}
        retained = tmdb_fields({'budget': 0, 'revenue': None}, previous)
        self.assertEqual(movie_financials(retained), previous)
        self.assertEqual(movie_financials(tvdb_fields({}, retained)), previous)
        self.assertEqual(movie_financials(tmdb_fields({'revenue': 900_000_000}, retained)),
                         {**previous, 'revenue': 900_000_000})

    def test_unknown_and_invalid_amounts_are_not_reported_as_zero_dollars(self):
        for invalid in (None, 0, -1, True, '1000', 1.5):
            with self.subTest(invalid=invalid):
                self.assertEqual(movie_financials({'budget': invalid, 'revenue': invalid}), {})

    def test_catalogue_import_retains_movie_amounts_without_exposing_them_for_series(self):
        raw = {'id': 1, 'title': 'Movie', 'budget': 150_000_000, 'revenue': 825_000_000}
        attrs = normalize_tmdb(raw, 'movie')['work']['attributes']
        self.assertEqual(movie_financials(attrs), movie_financials(raw))
        self.assertEqual(movie_financials(normalize_tmdb(raw, 'series')['work']['attributes']), {})


class CachedMovieFinancialsTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_movies_read_financials_from_cached_tmdb_snapshots(self):
        from core.media_details import work_details
        snapshots = [SimpleNamespace(provider='tmdb', images={}, homepage=None, url=None,
                                     budget=150_000_000, revenue=825_000_000)]
        result = lambda rows: SimpleNamespace(all=lambda: rows)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[result([]), result(snapshots)]))
        work = SimpleNamespace(id=1, kind='movie', attributes={}, image_url=None)
        with (patch('core.person_identities.canonical_people', AsyncMock(return_value={})),
              patch('core.media_details.character_images', AsyncMock(return_value={})),
              patch('core.screen_cast.complete_cast_works', AsyncMock(return_value=set())),
              patch('core.media_related.catalogue_related', AsyncMock(return_value={'relations': [], 'similar': []}))):
            details = await work_details(db, work)
        self.assertEqual(details['budget'], 150_000_000)
        self.assertEqual(details['revenue'], 825_000_000)
        self.assertEqual(db.execute.await_count, 2)
