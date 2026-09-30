import json
import unittest
from datetime import datetime

from core.arvio_payloads import parse_episode_info, parse_timestamp


class ArvioTimestampTests(unittest.TestCase):
    def test_seconds_milliseconds_and_iso_offsets_return_naive_utc(self):
        expected = datetime(2026, 8, 14, 10)
        for value in (1786701600, 1786701600000, "1786701600000", "2026-08-14T12:00:00+02:00", "2026-08-14T10:00:00"):
            with self.subTest(value=value):
                self.assertEqual(parse_timestamp(value), expected)

    def test_invalid_or_absent_timestamps_are_ignored(self):
        for value in (None, "", "invalid-date", [], {}, 1e100):
            with self.subTest(value=value):
                self.assertIsNone(parse_timestamp(value))


class ArvioEpisodeIdentityTests(unittest.TestCase):
    def test_compound_ids_are_consistent_across_representations(self):
        for value in ("tv:42:2:3", "SERIES:42-S2-E3", "tmdb:42 season2 episode3"):
            for item in (value, *({field: value} for field in ("id", "mediaId", "episodeId", "item_id", "itemId"))):
                with self.subTest(item=item):
                    self.assertEqual(parse_episode_info(item), (42, 2, 3))

    def test_field_aliases_and_json_episode_objects(self):
        for show in ("showTmdbId", "show_tmdb_id", "showId", "seriesTmdbId", "series_tmdb_id", "seriesId", "series_id", "tmdbId", "tmdb_id"):
            for season in ("season", "seasonNumber", "season_number", "seasonIndex", "s"):
                for episode in ("episode", "episodeNumber", "episode_number", "episodeIndex", "e"):
                    item = {show: "42", season: "2", episode: "3"}
                    with self.subTest(item=item):
                        self.assertEqual(parse_episode_info(item), (42, 2, 3))
                        self.assertEqual(parse_episode_info(json.dumps(item)), (42, 2, 3))

    def test_specials_preserve_season_zero_and_alias_precedence(self):
        for field in ("season", "seasonNumber", "season_number", "seasonIndex", "s"):
            for zero in (0, "0"):
                item = {"showTmdbId": 42, field: zero, "episode": 3}
                with self.subTest(item=item):
                    self.assertEqual(parse_episode_info(item), (42, 0, 3))
                    self.assertEqual(parse_episode_info(json.dumps(item)), (42, 0, 3))
        self.assertEqual(parse_episode_info({"showTmdbId": 42, "season": 0, "seasonNumber": 2, "episode": 3}), (42, 0, 3))
        self.assertEqual(parse_episode_info({"showTmdbId": 42, "season": None, "seasonNumber": 0, "episode": 3}), (42, 0, 3))

    def test_missing_or_invalid_episode_identity_is_ignored(self):
        for item in (None, 42, "invalid JSON", "[]", {}, {"showTmdbId": 42}, {"showTmdbId": "invalid", "season": 2, "episode": 3}):
            with self.subTest(item=item):
                self.assertIsNone(parse_episode_info(item))
