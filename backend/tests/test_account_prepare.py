"""Source-library matching preserves identities without an all-pairs scan."""
import os
import random
import unittest
from types import SimpleNamespace

os.environ.setdefault('SECRET_KEY', 'account-prepare-tests')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@127.0.0.1:1/test')

from core.sync_reconciliation import baseline_library_memberships
from models.base import MediaType


class AccountPrepareTests(unittest.TestCase):
    def test_native_id_reads_grow_with_media_rows_instead_of_library_times_media(self):
        reads = 0
        class Candidate:
            media_type = MediaType.movie
            def __init__(self, identifier):
                self.id = identifier
            @property
            def tmdb_id(self):
                nonlocal reads
                reads += 1
                return self.id
        count = 300
        rows = [Candidate(i) for i in range(count)]
        snapshot = {'library': [str(i) for i in range(count*3)],
                    'mappings': {str(i): i%count for i in range(count*3)}}
        self.assertEqual(len(baseline_library_memberships(1, snapshot, rows)), count)
        self.assertLessEqual(reads, len(rows)*2)

    def test_typed_shared_native_ids_untyped_records_duplicates_and_missing_mappings(self):
        rows = [SimpleNamespace(id=1, tmdb_id=10, media_type=MediaType.movie),
                SimpleNamespace(id=2, tmdb_id=10, media_type=MediaType.series),
                SimpleNamespace(id=3, tmdb_id=20, media_type=MediaType.movie)]
        snapshot = {'library': ['movie', 'series', 'unknown', 'duplicate', 'missing', 7],
                    'mappings': {'movie': 10, 'series': 10, 'unknown': 20, 'duplicate': 20, '7': 10},
                    'records': {'library': [{'content_id': 'movie', 'content_type': 'movie'},
                        {'content_id': 'series', 'content_type': 'series'},
                        {'content_id': 7, 'content_type': ''}]}}
        self.assertEqual(baseline_library_memberships(5, snapshot, rows), {(5, 1), (5, 2), (5, 3)})
        self.assertEqual(baseline_library_memberships(6, {**snapshot, 'library': []}, rows), set())
        self.assertEqual(baseline_library_memberships(6, snapshot, []), set())

    def test_indexed_result_matches_original_reference_for_mixed_source_shapes(self):
        rng = random.Random(81)
        rows = [SimpleNamespace(id=i, tmdb_id=i//2+1,
            media_type=MediaType.movie if i%2 else MediaType.series) for i in range(1, 101)]
        for _ in range(30):
            snapshot = {'library': [str(rng.randrange(60)) for _ in range(70)],
                'mappings': {str(i): rng.randrange(55) for i in range(55)},
                'records': {'library': [{'content_id': str(i), 'content_type': rng.choice(
                    ['movie', 'series', '', None, 'unknown'])} for i in range(45)]}}
            kinds = {str(row['content_id']): row['content_type'] for row in snapshot['records']['library']}
            expected = {(9, media.id) for key in snapshot['library'] for media in rows
                if media.tmdb_id == snapshot['mappings'].get(str(key))
                and (not kinds.get(str(key)) or media.media_type.value == kinds[str(key)])}
            self.assertEqual(baseline_library_memberships(9, snapshot, rows), expected)
