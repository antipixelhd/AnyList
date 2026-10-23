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
    def test_release_annotations_distinguish_watched_seasons_from_untouched_seasons(self):
        episodes = [SimpleNamespace(id=1, season_number=1), SimpleNamespace(id=2, season_number=1),
                    SimpleNamespace(id=3, season_number=2), SimpleNamespace(id=4, season_number=3)]
        self.assertEqual(tracking_projection.release_annotations(episodes[:2], {1}, 'completed'),
                         {'new_seasons': 0, 'new_episodes': 1})
        self.assertEqual(tracking_projection.release_annotations(episodes, {1}, 'completed'),
                         {'new_seasons': 2, 'new_episodes': 1})
        # Sparse history must use identities, rather than treating progress as a prefix.
        self.assertEqual(tracking_projection.release_annotations(episodes, {2, 3, 4}, 'completed'),
                         {'new_seasons': 0, 'new_episodes': 1})
        self.assertEqual(tracking_projection.release_annotations(episodes, {1, 2, 3, 4}, 'completed'),
                         {'new_seasons': 0, 'new_episodes': 0})
        for status in ('watching', 'planning', 'paused', 'dropped'):
            self.assertEqual(tracking_projection.release_annotations(episodes, {1}, status),
                             {'new_seasons': 0, 'new_episodes': 0})

    def test_movie_financials_are_projected_from_cached_metadata_only_for_movies(self):
        data = {'budget': 150_000_000, 'revenue': 825_000_000}
        movie = tracking_projection.media_data(_media(media_type=MediaType.movie, tmdb_data=data))
        self.assertEqual(movie['budget'], data['budget'])
        self.assertEqual(movie['revenue'], data['revenue'])
        series = tracking_projection.media_data(_media(tmdb_data=data))
        self.assertNotIn('budget', series)
        self.assertNotIn('revenue', series)

    def test_primary_artwork_matches_details_without_mutating_metadata(self):
        from core.media_images import IMAGE_POLICY_VERSION, BACKGROUND_POLICY_VERSION, screen_images
        metadata={'_anylist_backdrop':{'version':BACKGROUND_POLICY_VERSION,'image':'https://image.tmdb.org/t/p/original/background.jpg'},
            '_anylist_images':{'version':IMAGE_POLICY_VERSION,'primary_poster':'https://image.tmdb.org/t/p/original/new.jpg',
            'artwork':['https://artworks.thetvdb.com/banners/background.jpg']}}
        media=_media(poster_path='/old.jpg',backdrop_path='/old-background.jpg',tmdb_data=metadata)
        before=copy.deepcopy(metadata)
        projected=tracking_projection.media_data(media)
        detail=screen_images(media,{})
        self.assertEqual(projected['poster'],detail['poster'])
        self.assertEqual(projected['backdrop'],detail['backdrop'])
        self.assertEqual(metadata,before)

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

    def test_existing_streaming_cards_recover_ranges_without_reimport_or_storage_changes(self):
        payload = {'episodes_watched': 2, 'position': 'S3E6', 'streaming_events': {
            'episode:3:6': {'position': 'S3E6', 'episodes_watched': 1},
            'episode:3:5': {'position': 'S3E5', 'episodes_watched': 1},
        }}
        row = SimpleNamespace(user_id=7, status='watching', score=8, created_at=datetime(2022, 6, 17), payload=payload)
        before = copy.deepcopy(payload)
        cards = tracking_projection.activity_data([(row, _media())])
        self.assertEqual((cards[0]['payload']['position_start'], cards[0]['payload']['position']), ('S3E5', 'S3E6'))
        self.assertEqual(cards[0]['created_at'], row.created_at)
        self.assertEqual(row.payload, before)

    def test_upgraded_legacy_window_keeps_its_original_daily_key(self):
        row = SimpleNamespace(user_id=7, status='completed', score=8,
                              created_at=datetime(2026, 10, 2, 10),
                              payload={'rating_changed': True, 'window_started_at': '2026-10-02T00:00:00',
                                       'window_key': '2026-10-02'})
        cards = tracking_projection.activity_data([(row, _media())])
        self.assertEqual(cards[0]['key'], '7:5:2026-10-02')
