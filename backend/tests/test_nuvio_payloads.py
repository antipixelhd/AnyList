"""Focused Nuvio payload behavior, independent of HTTP sync orchestration."""

import os
import unittest
from datetime import datetime, timezone

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import nuvio_payloads
from models.base import MediaType
from models.media import Media
from models.playback_progress import PlaybackProgress
from models.show import Show


class NuvioPayloadTests(unittest.TestCase):
    def test_series_library_item_uses_canonical_show_artwork(self) -> None:
        media = Media(
            id=20,
            tmdb_id=4607,
            media_type=MediaType.series,
            title="Lost : Les Disparus",
            poster_path="",
            backdrop_path="",
        )
        show = Show(
            id=5,
            tmdb_id=4607,
            title="Lost",
            poster_path="https://image.tmdb.org/t/p/w500/poster.jpg",
            backdrop_path="https://image.tmdb.org/t/p/w1280/background.jpg",
            overview="A mysterious island.",
            first_air_date="2004-09-22",
            tmdb_rating=8.0,
            tmdb_data={
                "genres": [{"name": "Drama"}],
                "external_ids": {"imdb_id": "tt0411008"},
            },
        )

        item = nuvio_payloads.library_item(
            media,
            datetime(2026, 7, 19, tzinfo=timezone.utc),
            show,
        )

        self.assertIsNotNone(item)
        self.assertEqual(item["content_id"], "tt0411008")
        self.assertEqual(item["name"], "Lost : Les Disparus")
        self.assertEqual(item["poster"], show.poster_path)
        self.assertEqual(item["background"], show.backdrop_path)
        self.assertEqual(item["description"], show.overview)
        self.assertEqual(item["release_info"], "2004")
        self.assertEqual(item["imdb_rating"], 8.0)
        self.assertEqual(item["genres"], ["Drama"])






    def test_progress_payload_maps_movies_and_episodes(self) -> None:
        updated_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        progress = PlaybackProgress(
            user_id=1,
            media_id=10,
            progress_seconds=1800,
            progress_percent=0.25,
            updated_at=updated_at,
        )
        movie = Media(
            id=10,
            tmdb_id=550,
            media_type=MediaType.movie,
            title="Fight Club",
            runtime=120,
            tmdb_data={"external_ids": {"imdb_id": "tt0137523"}},
        )
        self.assertEqual(
            nuvio_payloads.progress_item(progress, movie),
            {
                "content_id": "tt0137523",
                "content_type": "movie",
                "video_id": "tt0137523",
                "position": 1800000,
                "duration": 7200000,
                "progress_key": "tt0137523",
                "last_watched": int(updated_at.timestamp() * 1000),
            },
        )

        episode = Media(
            id=11,
            media_type=MediaType.episode,
            title="Pilot",
            show_id=5,
            season_number=1,
            episode_number=1,
        )
        show = Show(
            id=5,
            tmdb_id=1396,
            title="Breaking Bad",
            tmdb_data={"external_ids": {"imdb_id": "tt0903747"}},
        )
        self.assertEqual(
            nuvio_payloads.progress_item(progress, episode, show),
            {
                "content_id": "tt0903747",
                "content_type": "series",
                "video_id": "tt0903747:1:1",
                "season": 1,
                "episode": 1,
                "position": 1800000,
                "duration": 7200000,
                "progress_key": "tt0903747_s1e1",
                "last_watched": int(updated_at.timestamp() * 1000),
            },
        )


    def test_watched_payload_uses_bare_imdb_ids(self) -> None:
        watched_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        movie = Media(
            id=10,
            tmdb_id=550,
            media_type=MediaType.movie,
            title="Fight Club",
            tmdb_data={"external_ids": {"imdb_id": "tt0137523"}},
        )
        self.assertEqual(
            nuvio_payloads.watched_item(movie, watched_at),
            {
                "content_id": "tt0137523",
                "content_type": "movie",
                "title": "Fight Club",
                "watched_at": int(watched_at.timestamp() * 1000),
            },
        )

        episode = Media(
            id=11,
            media_type=MediaType.episode,
            title="It's All Good",
            show_id=5,
            season_number=3,
            episode_number=2,
        )
        show = Show(
            id=5,
            tmdb_id=125988,
            title="Silo",
            tmdb_data={"external_ids": {"imdb_id": "tt14688458"}},
        )
        self.assertEqual(
            nuvio_payloads.watched_item(episode, watched_at, show),
            {
                "content_id": "tt14688458",
                "content_type": "series",
                "title": "It's All Good",
                "season": 3,
                "episode": 2,
                "watched_at": int(watched_at.timestamp() * 1000),
            },
        )

        tmdb_only_movie = Media(
            id=12,
            tmdb_id=550,
            media_type=MediaType.movie,
            title="Fight Club",
        )
        self.assertIsNone(nuvio_payloads.watched_item(tmdb_only_movie, watched_at))
