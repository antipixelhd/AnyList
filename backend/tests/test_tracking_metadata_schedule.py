import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost/test",
)

from core.tracking_metadata import tracking_catalogue_is_fresh


class TrackingCatalogueScheduleTests(unittest.TestCase):
    def _media(self, refreshed_at):
        return SimpleNamespace(
            tmdb_data={"tracking_catalogue_refreshed_at": refreshed_at}
        )

    def test_active_show_refreshes_daily(self):
        now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
        self.assertTrue(tracking_catalogue_is_fresh(
            self._media((now - timedelta(hours=19)).isoformat()),
            "Returning Series",
            now,
        ))
        self.assertFalse(tracking_catalogue_is_fresh(
            self._media((now - timedelta(hours=21)).isoformat()),
            "Returning Series",
            now,
        ))

    def test_finished_show_refreshes_monthly(self):
        now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
        self.assertTrue(tracking_catalogue_is_fresh(
            self._media((now - timedelta(days=29)).isoformat()),
            "Ended",
            now,
        ))
        self.assertFalse(tracking_catalogue_is_fresh(
            self._media((now - timedelta(days=31)).isoformat()),
            "Canceled",
            now,
        ))

    def test_missing_or_invalid_timestamp_is_stale(self):
        self.assertFalse(tracking_catalogue_is_fresh(SimpleNamespace(tmdb_data={}), None))
        self.assertFalse(tracking_catalogue_is_fresh(self._media("not-a-date"), None))

