import copy
import unittest
from datetime import datetime
from types import SimpleNamespace

from core import tracking_projection
from models.base import MediaType
from models.media import Media


def _media(**overrides):
    values = dict(
        id=5,
        title="Title",
        media_type=MediaType.series,
        release_date="2026-01-01",
        tmdb_data={},
    )
    values.update(overrides)
    return Media(**values)


class TrackingProjectionTests(unittest.TestCase):
    def test_cached_metadata_fallbacks_ignore_special_seasons_and_empty_names(self):
        metadata = {
            "runtime": 42,
            "tagline": "Cached tagline",
            "status": "Returning Series",
            "seasons": [
                {"season_number": 0},
                {"season_number": 1},
                {"season_number": 2},
            ],
            "genres": ["Drama", {"name": "Animation"}, {}, None],
            "networks": [{"name": "Network"}, {}],
            "episode_run_time": [None, 24],
        }
        media = _media(tmdb_data=metadata)
        before = copy.deepcopy(metadata)
        result = tracking_projection.media_data(media)
        self.assertEqual(result["runtime"], 42)
        self.assertEqual(result["tagline"], "Cached tagline")
        self.assertEqual(result["season_count"], 2)
        self.assertEqual(result["genres"], ["Drama", "Animation"])
        self.assertEqual(result["networks"], [{"name": "Network"}])
        self.assertEqual(result["episode_runtime"], 24)
        self.assertEqual(result["release_status"], "Returning Series")
        self.assertEqual(metadata, before)

    def test_anime_requires_animation_and_japanese_language_or_origin(self):
        for data, expected in (
            ({"genres": ["Animation"], "original_language": "ja"}, True),
            (
                {
                    "genres": [{"name": "Animation"}],
                    "production_countries": [{"iso_3166_1": "JP"}],
                },
                True,
            ),
            ({"genres": ["Animation"], "original_language": "en"}, False),
            ({"genres": ["Drama"], "origin_country": ["JP"]}, False),
        ):
            with self.subTest(data=data):
                self.assertEqual(
                    tracking_projection.is_anime(_media(tmdb_data=data)), expected
                )

    def test_entry_preserves_public_notes_but_only_exposes_manual_score_to_owner(self):
        entry = SimpleNamespace(
            status="completed",
            rating_mode="calculated",
            manual_score=9,
            season_scores={"0": 10, "1": 7, "2": 8},
            progress=16,
            favorite=True,
            start_date=None,
            finish_date=None,
            rewatch_count=1,
            updated_at=datetime(2026, 9, 30),
            notes="Public note",
        )
        public = tracking_projection.entry_data(entry, _media())
        owner = tracking_projection.entry_data(entry, _media(), owner=True)
        self.assertEqual(public["score"], 7.5)
        self.assertEqual(public["notes"], "Public note")
        self.assertNotIn("manual_score", public)
        self.assertEqual(owner["manual_score"], 9)

    def test_activity_merges_daily_rows_without_mutating_persisted_payloads(self):
        def activity(user_id, day, payload):
            return SimpleNamespace(
                user_id=user_id,
                created_at=day,
                status="completed",
                score=None,
                payload=payload,
            )

        newest = activity(7, datetime(2026, 9, 30, 12), {"episodes_watched": 2})
        older = activity(7, datetime(2026, 9, 30, 10), {"episodes_watched": 1})
        other_user = activity(8, datetime(2026, 9, 30, 9), {"episodes_watched": 4})
        previous_day = activity(7, datetime(2026, 9, 29, 12), {"episodes_watched": 5})
        media = _media()
        rows = [(row, media) for row in (newest, older, other_user, previous_day)]
        before = copy.deepcopy([row.payload for row, _ in rows])
        result = tracking_projection.activity_data(rows)
        self.assertEqual([r["payload"]["episodes_watched"] for r in result], [3, 4, 5])
        self.assertEqual(result[0]["created_at"], newest.created_at)
        self.assertEqual([row.payload for row, _ in rows], before)

    def test_fixed_windows_remain_distinct_when_latest_events_share_a_date(self):
        media = _media()
        rows = [
            (SimpleNamespace(user_id=7, status='completed', score=8,
                             created_at=datetime(2026, 10, 2, 10),
                             payload={'rating_changed': True, 'window_started_at': '2026-10-02T10:00:00'}), media),
            (SimpleNamespace(user_id=7, status='completed', score=None,
                             created_at=datetime(2026, 10, 2, 9),
                             payload={'status_changed': True, 'window_started_at': '2026-10-01T10:00:00'}), media),
        ]
        cards = tracking_projection.activity_data(rows)
        self.assertEqual(len(cards), 2)
        self.assertNotEqual(cards[0]['key'], cards[1]['key'])
        self.assertEqual(cards[1]['created_at'], datetime(2026, 10, 2, 9))

    def test_upgraded_legacy_window_keeps_its_original_daily_key(self):
        row = SimpleNamespace(user_id=7, status='completed', score=8,
                              created_at=datetime(2026, 10, 2, 10),
                              payload={'rating_changed': True, 'window_started_at': '2026-10-02T00:00:00',
                                       'window_key': '2026-10-02'})
        cards = tracking_projection.activity_data([(row, _media())])
        self.assertEqual(cards[0]['key'], '7:5:2026-10-02')
