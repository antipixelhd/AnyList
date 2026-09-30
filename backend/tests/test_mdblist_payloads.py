import os
import unittest
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import mdblist_payloads
from models.base import MediaType
from models.media import Media
from models.show import Show


class MDBListPayloadTests(unittest.TestCase):
    def test_unmapped_tvdb_episode_is_excluded_even_with_a_tmdb_parent(self):
        media = Media(media_type=MediaType.episode, tvdb_id=123, season_number=1, episode_number=2)
        show = Show(tmdb_id=456, title="Show")
        self.assertIsNone(mdblist_payloads.payload_item(media, show=show, rating=8.0))

    def test_repeated_episode_updates_merge_into_one_episode_without_losing_watch_date(self):
        watched = {"ids": {"tmdb": 456}, "seasons": [{"number": 1, "episodes": [{"number": 2, "watched_at": "2026-09-30T12:00:00Z"}]}]}
        rated = {"ids": {"tmdb": 456}, "seasons": [{"number": 1, "episodes": [{"number": 2, "rating": 8.0, "rated_at": "2026-09-30T13:00:00Z"}]}]}
        self.assertEqual(mdblist_payloads.merge_show_entries([watched, rated]), [
            {"ids": {"tmdb": 456}, "seasons": [{"number": 1, "episodes": [{
                "number": 2, "watched_at": "2026-09-30T12:00:00Z", "rating": 8.0, "rated_at": "2026-09-30T13:00:00Z",
            }]}]},
        ])

    def test_payload_item_nests_episode_under_parent_show(self) -> None:
        """Regression test: an episode's own TMDB id is a completely different
        ID namespace from shows/movies. Sending it as a standalone "episodes"
        entry (the old behavior) resolves to an unrelated, wrong item on
        MDBList. Episodes must be identified via the parent show's ids plus
        season/episode numbers, nested under "shows"."""
        media = Media(
            id=1,
            tmdb_id=62085,
            media_type=MediaType.episode,
            title="Caballo sin Nombre",
            season_number=3,
            episode_number=2,
        )
        show = Show(id=10, tmdb_id=1396, title="Breaking Bad")
        kind, item = mdblist_payloads.payload_item(media, show=show, watched_at=datetime(2026, 7, 17, 12, 0, 0))
        self.assertEqual(kind, "shows")
        self.assertEqual(
            item,
            {
                "ids": {"tmdb": 1396},
                "seasons": [
                    {
                        "number": 3,
                        "episodes": [
                            {"number": 2, "watched_at": "2026-07-17T12:00:00Z"},
                        ],
                    }
                ],
            },
        )


    def test_payload_item_drops_episode_without_parent_show(self) -> None:
        media = Media(
            id=1,
            tmdb_id=62085,
            media_type=MediaType.episode,
            title="Caballo sin Nombre",
            season_number=3,
            episode_number=2,
        )
        self.assertIsNone(mdblist_payloads.payload_item(media, watched_at=datetime(2026, 7, 17, 12, 0, 0)))
        self.assertIsNone(
            mdblist_payloads.payload_item(media, show=Show(id=10, title="Breaking Bad"), watched_at=datetime(2026, 7, 17, 12, 0, 0))
        )


    def test_merge_show_entries_combines_multiple_episodes_of_same_season(self) -> None:
        show = Show(id=10, tmdb_id=1396, title="Breaking Bad")
        media_ep1 = Media(id=1, media_type=MediaType.episode, season_number=2, episode_number=1)
        media_ep2 = Media(id=2, media_type=MediaType.episode, season_number=2, episode_number=2)

        _, ep1_item = mdblist_payloads.payload_item(media_ep1, show=show, watched_at=datetime(2026, 7, 17, 12, 0, 0))
        _, ep2_item = mdblist_payloads.payload_item(media_ep2, show=show, watched_at=datetime(2026, 7, 17, 13, 0, 0))

        merged = mdblist_payloads.merge_show_entries([ep1_item, ep2_item])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["ids"], {"tmdb": 1396})
        self.assertEqual(len(merged[0]["seasons"]), 1)
        self.assertEqual(merged[0]["seasons"][0]["number"], 2)
        self.assertEqual(
            merged[0]["seasons"][0]["episodes"],
            [
                {"number": 1, "watched_at": "2026-07-17T12:00:00Z"},
                {"number": 2, "watched_at": "2026-07-17T13:00:00Z"},
            ],
        )


    def test_payload_item_preserves_rating_timestamp(self) -> None:
        media = Media(id=1, tmdb_id=550, media_type=MediaType.movie, title="Fight Club")
        kind, item = mdblist_payloads.payload_item(
            media,
            rating=8.0,
            rated_at=datetime(2026, 7, 17, 12, 0, 0),
        )
        self.assertEqual(kind, "movies")
        self.assertEqual(item["rating"], 8.0)
        self.assertEqual(item["rated_at"], "2026-07-17T12:00:00Z")


    def test_payload_item_nests_season_under_parent_show(self) -> None:
        media = Media(
            id=1,
            tmdb_id=1396,
            media_type=MediaType.series,
            title="Breaking Bad",
        )

        kind, item = mdblist_payloads.payload_item(
            media,
            season_number=1,
            rating=8.0,
            rated_at=datetime(2026, 7, 18, 0, 0, 0, 123456),
        )

        self.assertEqual(kind, "shows")
        self.assertEqual(
            item,
            {
                "ids": {"tmdb": 1396},
                "seasons": [
                    {
                        "number": 1,
                        "rating": 8.0,
                        "rated_at": "2026-07-18T00:00:00Z",
                    }
                ],
            },
        )


    def test_rating_removal_nests_season_under_parent_show(self) -> None:
        media = Media(
            id=1,
            tmdb_id=1396,
            media_type=MediaType.series,
            title="Breaking Bad",
        )

        kind, item = mdblist_payloads.rating_removal_item(media, season_number=1)

        self.assertEqual(kind, "shows")
        self.assertEqual(
            item,
            {
                "ids": {"tmdb": 1396},
                "seasons": [{"number": 1}],
            },
        )


    def test_merge_show_entries_combines_multiple_seasons_of_same_show(self) -> None:
        """Regression test: two season ratings for one show must round-trip
        as a single show object with both seasons nested, not two separate
        entries sharing the same ids.tmdb."""
        _, season_one = mdblist_payloads.payload_item(
            Media(id=1, tmdb_id=1396, media_type=MediaType.series, title="Breaking Bad"),
            season_number=1,
            rating=8.0,
            rated_at=datetime(2026, 7, 18, 0, 0, 0),
        )
        _, season_two = mdblist_payloads.payload_item(
            Media(id=1, tmdb_id=1396, media_type=MediaType.series, title="Breaking Bad"),
            season_number=2,
            rating=9.0,
            rated_at=datetime(2026, 7, 18, 0, 0, 0),
        )

        merged = mdblist_payloads.merge_show_entries([season_one, season_two])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["ids"], {"tmdb": 1396})
        self.assertEqual(
            merged[0]["seasons"],
            [
                {"number": 1, "rating": 8.0, "rated_at": "2026-07-18T00:00:00Z"},
                {"number": 2, "rating": 9.0, "rated_at": "2026-07-18T00:00:00Z"},
            ],
        )


    def test_merge_show_entries_keeps_different_shows_separate(self) -> None:
        _, breaking_bad = mdblist_payloads.payload_item(
            Media(id=1, tmdb_id=1396, media_type=MediaType.series, title="Breaking Bad"),
            season_number=1,
            rating=8.0,
        )
        _, the_wire = mdblist_payloads.payload_item(
            Media(id=2, tmdb_id=1438, media_type=MediaType.series, title="The Wire"),
            season_number=1,
            rating=10.0,
        )

        merged = mdblist_payloads.merge_show_entries([breaking_bad, the_wire])

        self.assertEqual(len(merged), 2)
        self.assertEqual({item["ids"]["tmdb"] for item in merged}, {1396, 1438})


    def test_merge_show_entries_combines_show_rating_with_season_removal(self) -> None:
        """A show-level rating and a season removal for the same show must
        merge into one object rather than clobbering each other."""
        show_item = {"ids": {"tmdb": 1396}, "rating": 9.0, "rated_at": "2026-07-18T00:00:00Z"}
        season_removal = {"ids": {"tmdb": 1396}, "seasons": [{"number": 1}]}

        merged = mdblist_payloads.merge_show_entries([show_item, season_removal])

        self.assertEqual(len(merged), 1)
        self.assertEqual(
            merged[0],
            {
                "ids": {"tmdb": 1396},
                "rating": 9.0,
                "rated_at": "2026-07-18T00:00:00Z",
                "seasons": [{"number": 1}],
            },
        )
