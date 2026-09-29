"""Imported ratings preserve local precision and normalized event times."""
import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core.import_ratings import apply_imported_rating
from models.media import Media
from models.ratings import Rating


class ImportedRatingTests(unittest.TestCase):
    def test_provider_echo_preserves_precise_local_score_and_original_time(self):
        original = datetime(2026, 9, 20, 12)
        media = Media(id=7)
        rating = Rating(user_id=1, media_id=7, rating=7.5, rated_at=original)
        existing = {(7, None): rating}
        changed = {}
        db = MagicMock()
        self.assertFalse(apply_imported_rating(db, 1, media, None,
            {"rating": 8, "rated_at": "2026-09-30T12:00:00Z"}, existing, changed))
        self.assertEqual(rating.rating, 7.5)
        self.assertEqual(rating.rated_at, original)
        self.assertEqual(changed, {})
        db.add.assert_not_called()

    def test_new_season_rating_records_utc_time_and_only_changes_that_season(self):
        existing = {}
        changed = {}
        db = MagicMock()
        media = Media(id=7)
        self.assertTrue(apply_imported_rating(db, 1, media, 2,
            {"rating": 6, "rated_at": "2026-09-30T14:30:00+02:00"}, existing, changed))
        self.assertEqual(changed, {(7, 2): 6.0})
        rating = existing[(7, 2)]
        self.assertEqual(rating.user_id, 1)
        self.assertEqual(rating.media_id, 7)
        self.assertEqual(rating.season_number, 2)
        self.assertEqual(rating.rated_at, datetime(2026, 9, 30, 12, 30))
        db.add.assert_called_once_with(rating)
