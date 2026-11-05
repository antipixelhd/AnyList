"""Conservative import admission uses duration evidence, independently of outcomes."""
import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.watch_state_policy import filter_import_items, import_watch_evidence


def movie(seconds=2519, runtime=60):
    return {'kind': 'movie', 'source_title': 'Movie',
        'match': {'candidate': {'tmdb_id': 42, 'details': {'runtime': runtime}}},
        'source_evidence': {'viewings': [{'watched_seconds': seconds}]},
        'outcome': {'status': 'completed', 'status_overridden': True}}


def series(seconds):
    return {'kind': 'show', 'episodes': [{'season_number': 1, 'episode_number': index,
        'resolution': 'exact', 'matched': True, 'viewings': [{'watched_seconds': value}]}
        for index, value in enumerate(seconds, 1)],
        'catalog_episodes': [{'season_number': 1, 'episode_number': index, 'runtime': 60}
            for index in range(1, len(seconds)+1)]}


class ImportWatchPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = SimpleNamespace(scalar=AsyncMock(return_value=None))

    async def test_seventy_percent_boundary_for_prime_and_connection_movies(self):
        weak, boundary, above = movie(), movie(2520), movie(2700)
        for source in ('amazon', 'stremio', 'nuvio'):
            with self.subTest(source=source):
                self.assertEqual(await filter_import_items(self.db, 7, [weak, boundary, above], source), [boundary, above])
                self.assertEqual(await filter_import_items(self.db, 7, [weak, boundary, above], source, for_commit=True), [boundary, above])

    async def test_series_needs_one_qualified_episode_not_accumulated_partial_time(self):
        weak, confirmed = series([1000, 1000, 1000]), series([600, 2520, 600])
        for source in ('amazon', 'stremio', 'nuvio'):
            self.assertEqual(await filter_import_items(self.db, 7, [weak, confirmed], source), [confirmed])
        skipped = deepcopy(confirmed)
        skipped['episodes'][1]['decision'] = {'action': 'skip'}
        self.assertFalse(import_watch_evidence(skipped))

    async def test_immediate_and_netflix_are_unchanged(self):
        items = [movie(), series([600])]
        self.assertIs(await filter_import_items(self.db, 7, items, 'netflix', for_commit=True), items)
        self.db.scalar.assert_not_awaited()
        self.db.scalar.return_value = {'watch_state_sync': 'immediate'}
        for source in ('amazon', 'stremio', 'nuvio'):
            self.assertIs(await filter_import_items(self.db, 7, items, source, for_commit=True), items)

    async def test_unknown_runtime_remains_reviewable_but_cannot_import_without_evidence(self):
        unknown = movie(2700, runtime=None)
        self.assertIsNone(import_watch_evidence(unknown))
        self.assertEqual(await filter_import_items(self.db, 7, [unknown], 'amazon'), [unknown])
        self.assertEqual(await filter_import_items(self.db, 7, [unknown], 'amazon', for_commit=True), [])
        unknown['match']['candidate']['details']['runtime'] = 60
        self.assertEqual(await filter_import_items(self.db, 7, [unknown], 'amazon', for_commit=True), [unknown])

    async def test_explicit_watched_flag_qualifies_even_without_duration(self):
        explicit = movie(0, runtime=None)
        explicit['source_evidence']['viewings'][0]['completed_evidence'] = True
        self.assertEqual(await filter_import_items(self.db, 7, [explicit], 'stremio'), [explicit])

    async def test_never_sums_separate_partial_viewings_into_completion(self):
        item = movie(1500)
        item['source_evidence']['viewings'].append({'watched_seconds': 1500})
        self.assertFalse(import_watch_evidence(item))
