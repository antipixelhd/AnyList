import unittest
from datetime import date
from types import SimpleNamespace

from core.season_releases import (
    SEASON_RELEASE_DATE_KIND,
    SEASON_RELEASE_KIND,
    _season_art,
    observation_transition,
    season_event_dedupe_key,
    season_date_notice_is_current,
    upcoming_season,
)


class UpcomingSeasonTests(unittest.TestCase):
    def test_first_season_is_not_a_new_season_release(self):
        season = {"season_number": 1, "name": "Season 1", "air_date": "2026-10-01", "episode_count": 0}
        self.assertIsNone(upcoming_season({"seasons": [season]}, date(2026, 9, 25)))

    def test_selects_next_confirmed_regular_season_even_without_episode_rows(self):
        metadata = {"seasons": [
            {"season_number": 0, "air_date": "2026-10-01"},
            {"season_number": 1, "air_date": "2024-01-01"},
            {"season_number": 2, "air_date": None},
            {"season_number": 3, "name": "Season 3", "air_date": "2026-10-03", "episode_count": 0},
            {"season_number": 4, "name": "Season 4", "air_date": "2027-01-01", "episode_count": 0},
        ]}
        self.assertEqual(
            upcoming_season(metadata, date(2026, 9, 25)),
            {"season_number": 3, "name": "Season 3", "air_date": "2026-10-03", "episode_count": 0},
        )

    def test_premiere_day_is_still_returned_for_release_day_card(self):
        metadata = {"seasons": [
            {"season_number": 1, "air_date": "2024-01-01"},
            {"season_number": 2, "air_date": "2026-09-25"},
        ]}
        self.assertEqual(upcoming_season(metadata, date(2026, 9, 25))["season_number"], 2)

    def test_season_artwork_fallback_is_explicit_and_retryable(self):
        media = SimpleNamespace(poster_path="show-poster.jpg")
        missing = _season_art(media, {"season_number": 5, "air_date": "2026-10-01"})
        self.assertEqual(missing["poster"], "show-poster.jpg")
        self.assertIsNone(missing["season_poster_path"])
        self.assertTrue(missing["season_artwork_fallback"])
        self.assertTrue(missing["artwork_retry"])

        available = _season_art(media, {
            "season_number": 5, "air_date": "2026-10-01", "poster_path": "season-5.jpg",
        })
        self.assertEqual(available["season_poster_path"], "season-5.jpg")
        self.assertFalse(available["season_artwork_fallback"])
        self.assertFalse(available["artwork_retry"])


class SeasonObservationTests(unittest.TestCase):
    def test_future_date_notifies_once_and_is_remembered_for_release(self):
        future = observation_transition(
            date(2026, 10, 1), None, False, False, date(2026, 9, 25),
        )
        self.assertTrue(future["send_date_notice"])
        self.assertFalse(future["send_release_notice"])
        self.assertTrue(future["seen_upcoming"])
        self.assertFalse(future["release_processed"])

        release = observation_transition(
            date(2026, 10, 1), "2026-10-01", future["seen_upcoming"],
            future["release_processed"], date(2026, 10, 1),
        )
        self.assertFalse(release["send_date_notice"])
        self.assertTrue(release["send_release_notice"])
        self.assertTrue(release["release_processed"])

        repeated = observation_transition(
            date(2026, 10, 1), "2026-10-01", release["seen_upcoming"],
            release["release_processed"], date(2026, 10, 2),
        )
        self.assertFalse(repeated["send_release_notice"])

    def test_first_scan_on_premiere_day_only_sends_release_notice(self):
        transition = observation_transition(
            date(2026, 9, 25), None, False, False, date(2026, 9, 25),
        )
        self.assertFalse(transition["send_date_notice"])
        self.assertTrue(transition["send_release_notice"])
        self.assertTrue(transition["release_processed"])

    def test_first_scan_after_a_past_premiere_is_quiet(self):
        transition = observation_transition(
            date(2026, 9, 20), None, False, False, date(2026, 9, 25),
        )
        self.assertFalse(transition["send_date_notice"])
        self.assertFalse(transition["send_release_notice"])
        self.assertTrue(transition["release_processed"])

    def test_corrected_past_date_reopens_release_tracking_for_future_date(self):
        corrected = observation_transition(
            date(2026, 10, 15), "2026-09-20", False, True, date(2026, 9, 25),
        )
        self.assertTrue(corrected["send_date_notice"])
        self.assertFalse(corrected["release_processed"])
        premiere = observation_transition(
            date(2026, 10, 15), "2026-10-15", corrected["seen_upcoming"],
            corrected["release_processed"], date(2026, 10, 15),
        )
        self.assertTrue(premiere["send_release_notice"])

    def test_delayed_scan_releases_only_a_previously_seen_upcoming_season(self):
        seen = observation_transition(
            date(2026, 9, 20), "2026-09-20", True, False, date(2026, 9, 25),
        )
        self.assertTrue(seen["send_release_notice"])

    def test_date_dedupe_changes_with_date_but_release_dedupe_does_not(self):
        old = season_event_dedupe_key(SEASON_RELEASE_DATE_KIND, 12, 5, "2026-10-01")
        revised = season_event_dedupe_key(SEASON_RELEASE_DATE_KIND, 12, 5, "2026-10-15")
        first_release = season_event_dedupe_key(SEASON_RELEASE_KIND, 12, 5, "2026-10-01")
        revised_release = season_event_dedupe_key(SEASON_RELEASE_KIND, 12, 5, "2026-10-15")
        self.assertNotEqual(old, revised)
        self.assertEqual(first_release, revised_release)

    def test_date_notice_is_stale_when_provider_retracts_or_changes_date(self):
        current_key = season_event_dedupe_key(SEASON_RELEASE_DATE_KIND, 12, 5, "2026-10-01")
        confirmed = {"season_number": 5, "air_date": "2026-10-01"}
        self.assertTrue(season_date_notice_is_current(12, 5, confirmed, current_key))
        self.assertFalse(season_date_notice_is_current(12, 5, None, current_key))
        self.assertFalse(season_date_notice_is_current(
            12, 5, {"season_number": 5, "air_date": None}, current_key,
        ))
        self.assertFalse(season_date_notice_is_current(
            12, 5, {"season_number": 5, "air_date": "2026-10-15"}, current_key,
        ))


if __name__ == "__main__":
    unittest.main()
