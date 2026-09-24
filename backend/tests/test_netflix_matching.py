import os
import unittest
from unittest.mock import AsyncMock

import httpx

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core.netflix_import import parse_netflix_csv, prepare_netflix_import


class NetflixCsvParserTests(unittest.TestCase):
    def test_english_export_preserves_quoted_colons_and_collapses_duplicate_events(self) -> None:
        csv_text = (
            "\ufeffTitle,Date\r\n"
            '"Stranger Things: Stranger Things 5: Chapter Eight: The Rightside Up",6/1/26\r\n'
            '"Stranger Things: Stranger Things 5: Chapter Eight: The Rightside Up",6/1/26\r\n'
            '"Stranger Things: Stranger Things 5: Chapter Eight: The Rightside Up",6/2/26\r\n'
            '"The Hangover: Part II",3/1/26\r\n'
        )

        history = parse_netflix_csv(csv_text)

        self.assertEqual(history.total_rows, 4)
        self.assertEqual(history.duplicate_rows, 1)
        self.assertEqual(len(history.rows), 3)
        episode = history.rows[0]
        self.assertEqual(episode.show_title, "Stranger Things")
        self.assertEqual(episode.season_label, "Stranger Things 5")
        self.assertEqual(episode.season_number, 5)
        self.assertEqual(episode.episode_title, "Chapter Eight: The Rightside Up")
        self.assertEqual(episode.source_rows, [2, 3])
        self.assertEqual(episode.watched_at, "2026-06-01")
        self.assertEqual(history.rows[1].watched_at, "2026-06-02")
        self.assertEqual(history.rows[2].show_title, "The Hangover")

    def test_german_headers_and_day_first_dates(self) -> None:
        history = parse_netflix_csv(
            "Titel,Datum\n"
            '"Dark: Staffel 1: Geheimnisse",29.08.2026\n'
            '"Lola rennt",31/12/2024\n',
            language="de-DE",
        )

        self.assertEqual(history.errors, [])
        self.assertEqual(history.rows[0].watched_at, "2026-08-29")
        self.assertEqual(history.rows[0].season_number, 1)
        self.assertEqual(history.rows[0].episode_title, "Geheimnisse")
        self.assertEqual(history.rows[1].watched_at, "2024-12-31")
        self.assertEqual(history.language, "de-DE")

    def test_invalid_dates_are_recoverable_row_errors(self) -> None:
        history = parse_netflix_csv("Title,Date\nValid movie,2/28/26\nBad date,19/99/2026\n")

        self.assertEqual(len(history.rows), 1)
        self.assertEqual(history.rows[0].watched_at, "2026-02-28")
        self.assertEqual(history.errors, [{"row": 3, "message": "Invalid viewing date: 19/99/2026"}])

    def test_only_titleless_episode_rows_are_excluded(self) -> None:
        history = parse_netflix_csv(
            'Title,Date\n": Episode 2",3/10/21\n": episode 12",3/11/21\n'
            '"Show: Episode 2",3/12/21\n": Episode 2: Extra",3/13/21\n'
        )
        self.assertEqual(history.total_rows, 4)
        self.assertEqual(history.excluded_rows, 2)
        self.assertEqual([row.source_title for row in history.rows], ["Show: Episode 2", ": Episode 2: Extra"])
        self.assertEqual(history.errors, [])

    def test_rejects_wrong_headers_bad_encoding_and_malformed_csv(self) -> None:
        with self.assertRaisesRegex(ValueError, "Title and Date"):
            parse_netflix_csv("Name,Watched\nExample,1/1/26\n")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            parse_netflix_csv(b"Title,Date\n\xff,1/1/26\n")
        with self.assertRaisesRegex(ValueError, "malformed"):
            parse_netflix_csv('Title,Date\n"unfinished,1/1/26\n')


class NetflixMatchingTests(unittest.IsolatedAsyncioTestCase):
    async def test_episode_without_show_title_is_excluded_before_matching(self) -> None:
        movie_search = AsyncMock(return_value={"results": []})
        result = await prepare_netflix_import(
            'Title,Date\n": Episode 2",3/10/21\n',
            search_movies_fn=movie_search,
            search_shows_fn=AsyncMock(return_value={"results": []}),
        )
        movie_search.assert_not_awaited()
        self.assertEqual(result["movies"], [])
        self.assertEqual(result["unmatched"], [])
        self.assertEqual(result["counts"]["excluded_rows"], 1)

    async def test_dominant_same_title_movie_matches_without_manual_review(self) -> None:
        async def search_movies(query: str, **_kwargs):
            return {"results": [
                {"id": 438631, "title": query, "release_date": "2021-09-15", "vote_count": 15576, "popularity": 42.6},
                {"id": 841, "title": query, "release_date": "1984-12-14", "vote_count": 3551, "popularity": 16.8},
            ]}

        result = await prepare_netflix_import(
            'Title,Date\nDune,2/7/24\n',
            search_movies_fn=search_movies,
            search_shows_fn=AsyncMock(return_value={"results": []}),
        )
        self.assertEqual(result["movies"][0]["tmdb_id"], 438631)
        self.assertEqual(result["movies"][0]["status"], "matched")

    async def test_close_same_title_movies_still_need_review(self) -> None:
        async def search_movies(query: str, **_kwargs):
            return {"results": [
                {"id": 1, "title": query, "release_date": "2020-01-01", "vote_count": 5000, "popularity": 25},
                {"id": 2, "title": query, "release_date": "1980-01-01", "vote_count": 3000, "popularity": 20},
            ]}

        result = await prepare_netflix_import(
            'Title,Date\nShared Title,2/7/24\n',
            search_movies_fn=search_movies,
            search_shows_fn=AsyncMock(return_value={"results": []}),
        )
        self.assertEqual(result["movies"][0]["status"], "review")

    async def test_colon_bearing_show_title_uses_longest_exact_catalog_title(self) -> None:
        searches = []

        async def search_shows(query: str, **_kwargs):
            searches.append(query)
            if query == "Star Wars: The Clone Wars":
                return {"results": [{"id": 4194, "name": query}]}
            return {"results": [{"id": 9, "name": "Star Wars"}]}

        async def show_details(tmdb_id: int, **_kwargs):
            return {"id": tmdb_id, "name": "Star Wars: The Clone Wars", "seasons": [{"season_number": 1, "episode_count": 1}]}

        async def get_season(_tmdb_id: int, _season_number: int, **_kwargs):
            return {"episode_count": 1, "episodes": [{"id": 71, "episode_number": 1, "name": "Ambush", "air_date": "2008-10-03"}]}

        result = await prepare_netflix_import(
            'Title,Date\n"Star Wars: The Clone Wars: Season 1: Ambush",1/1/24\n',
            search_movies_fn=AsyncMock(return_value={"results": []}),
            search_shows_fn=search_shows,
            show_details_fn=show_details,
            season_fn=get_season,
        )
        self.assertEqual(set(searches), {"Star Wars", "Star Wars: The Clone Wars"})
        self.assertEqual(result["shows"][0]["source_title"], "Star Wars: The Clone Wars")
        self.assertEqual(result["shows"][0]["tmdb_id"], 4194)
        self.assertTrue(result["shows"][0]["episodes"][0]["matched"])

    async def test_colon_inside_episode_title_is_checked_as_one_title(self) -> None:
        async def search_shows(_query: str, **_kwargs):
            return {"results": [{"id": 66732, "name": "Stranger Things"}]}

        async def show_details(tmdb_id: int, **_kwargs):
            return {"id": tmdb_id, "seasons": [{"season_number": 1, "episode_count": 1}]}

        async def get_season(_tmdb_id: int, _season_number: int, **_kwargs):
            return {"episode_count": 1, "episodes": [{"id": 72, "episode_number": 1, "name": "Chapter Four: The Body", "air_date": "2016-07-15"}]}

        result = await prepare_netflix_import(
            'Title,Date\n"Stranger Things: Chapter Four: The Body",1/1/24\n',
            search_movies_fn=AsyncMock(return_value={"results": []}),
            search_shows_fn=search_shows,
            show_details_fn=show_details,
            season_fn=get_season,
        )
        self.assertTrue(result["shows"][0]["episodes"][0]["matched"])

    async def test_exact_show_title_does_not_require_title_review_for_one_unknown_episode(self) -> None:
        async def search_shows(_query: str, **_kwargs):
            return {"results": [{"id": 42, "name": "Example Show"}]}

        async def show_details(tmdb_id: int, **_kwargs):
            return {"id": tmdb_id, "seasons": [{"season_number": 1, "episode_count": 1}]}

        async def get_season(_tmdb_id: int, _season_number: int, **_kwargs):
            return {"episode_count": 1, "episodes": [{"id": 1, "episode_number": 1, "name": "Pilot", "air_date": "2020-01-01"}]}

        result = await prepare_netflix_import(
            'Title,Date\n"Example Show: Season 1: Pilot",1/3/20\n"Example Show: Season 1: Unknown",1/4/20\n',
            search_movies_fn=AsyncMock(return_value={"results": []}),
            search_shows_fn=search_shows,
            show_details_fn=show_details,
            season_fn=get_season,
        )
        show = result["shows"][0]
        self.assertEqual(show["status"], "matched")
        self.assertEqual([episode["matched"] for episode in show["episodes"]], [True, False])

    async def test_missing_tmdb_season_leaves_episode_for_review(self) -> None:
        async def search_shows(_query: str, **_kwargs):
            return {"results": [{"id": 72304, "name": "Example Show"}]}

        async def search_movies(_query: str, **_kwargs):
            return {"results": []}

        async def show_details(_tmdb_id: int, **_kwargs):
            return {"id": 72304, "name": "Example Show", "seasons": [
                {"season_number": 1, "episode_count": 2},
                {"season_number": 2, "episode_count": 1},
            ]}

        async def get_season(_tmdb_id: int, season_number: int, **_kwargs):
            if season_number == 2:
                request = httpx.Request("GET", "https://api.themoviedb.org/3/tv/72304/season/2")
                response = httpx.Response(404, request=request)
                raise httpx.HTTPStatusError("Not Found", request=request, response=response)
            return {"episode_count": 2, "episodes": [
                {"id": 1001, "episode_number": 1, "name": "Pilot", "air_date": "2020-01-01"},
                {"id": 1002, "episode_number": 2, "name": "Finale", "air_date": "2020-01-02"},
            ]}

        result = await prepare_netflix_import(
            'Title,Date\n"Example Show: Season 1: Pilot",1/3/20\n"Example Show: Season 2: New Chapter",1/4/20\n',
            search_movies_fn=search_movies,
            search_shows_fn=search_shows,
            show_details_fn=show_details,
            season_fn=get_season,
        )

        self.assertEqual(len(result["shows"]), 1)
        show = result["shows"][0]
        self.assertEqual(show["status"], "matched")
        self.assertEqual([episode["matched"] for episode in show["episodes"]], [True, False])
        self.assertIn("TMDB has no season 2", show["episodes"][1]["reason"])
        self.assertIn({"season_number": 2, "represented": 0, "total_released": 1, "catalogue_complete": False}, show["seasons"])

    async def test_netflix_split_season_matches_unique_title_in_existing_tmdb_season(self) -> None:
        season_calls = []

        async def search_shows(_query: str, **_kwargs):
            return {"results": [{"id": 72304, "name": "Example Show"}]}

        async def search_movies(_query: str, **_kwargs):
            return {"results": []}

        async def show_details(_tmdb_id: int, **_kwargs):
            return {"id": 72304, "name": "Example Show", "seasons": [{"season_number": 1, "episode_count": 2}]}

        async def get_season(_tmdb_id: int, season_number: int, **_kwargs):
            season_calls.append(season_number)
            return {"episode_count": 2, "episodes": [
                {"id": 1001, "episode_number": 1, "name": "Pilot", "air_date": "2020-01-01"},
                {"id": 1002, "episode_number": 2, "name": "New Chapter", "air_date": "2020-01-02"},
            ]}

        result = await prepare_netflix_import(
            'Title,Date\n"Example Show: Season 2: New Chapter",1/4/20\n',
            search_movies_fn=search_movies,
            search_shows_fn=search_shows,
            show_details_fn=show_details,
            season_fn=get_season,
        )

        self.assertEqual(season_calls, [1])
        episode = result["shows"][0]["episodes"][0]
        self.assertTrue(episode["matched"])
        self.assertEqual((episode["season_number"], episode["episode_number"]), (1, 2))

    async def test_exact_show_episode_matching_fetches_each_season_once(self) -> None:
        searches: list[str] = []
        details = AsyncMock(return_value={
            "id": 42,
            "name": "Dark",
            "external_ids": {"tvdb_id": 1234},
            "seasons": [{"season_number": 1, "name": "Season 1", "episode_count": 2}],
        })
        season_calls: list[tuple[int, int]] = []

        async def search_shows(query: str, **_kwargs):
            searches.append(query)
            return {"results": [{
                "id": 42,
                "name": "Dark",
                "original_name": "Dark",
                "first_air_date": "2017-12-01",
                "poster_path": "/dark.jpg",
            }]}

        async def search_movies(_query: str, **_kwargs):
            return {"results": []}

        async def get_season(tmdb_id: int, season_number: int, **_kwargs):
            season_calls.append((tmdb_id, season_number))
            return {
                "episode_count": 2,
                "episodes": [
                    {"id": 1001, "episode_number": 1, "name": "Secrets", "air_date": "2017-12-01"},
                    {"id": 1002, "episode_number": 2, "name": "Lies", "air_date": "2017-12-01"},
                ],
            }

        progress = []
        async def record_progress(current: int, total: int, message: str):
            progress.append((current, total, message))

        result = await prepare_netflix_import(
            'Title,Date\n"Dark: Season 1: Secrets",8/29/26\n"Dark: Season 1: Lies",8/30/26\n',
            api_key="dummy",
            search_movies_fn=search_movies,
            search_shows_fn=search_shows,
            show_details_fn=details,
            season_fn=get_season,
            progress_fn=record_progress,
        )

        self.assertEqual(searches, ["Dark"])
        details.assert_awaited_once_with(42, api_key="dummy", language="en-US")
        self.assertEqual(season_calls, [(42, 1)])
        self.assertEqual(result["counts"]["shows"], 1)
        show = result["shows"][0]
        self.assertEqual(show["status"], "matched")
        self.assertEqual(show["confidence"], "high")
        self.assertEqual(show["episode_order"], "tmdb:aired")
        self.assertEqual(show["tvdb_id"], 1234)
        self.assertEqual(show["dates"], ["2026-08-29", "2026-08-30"])
        self.assertEqual([(ep["season_number"], ep["episode_number"]) for ep in show["episodes"]], [(1, 1), (1, 2)])
        self.assertEqual(show["seasons"], [{
            "season_number": 1,
            "represented": 2,
            "total_released": 2,
            "catalogue_complete": True,
        }])
        self.assertEqual(show["catalog_episodes"], [
            {"season_number": 1, "episode_number": 1, "title": "Secrets", "release_date": "2017-12-01", "tmdb_episode_id": 1001},
            {"season_number": 1, "episode_number": 2, "title": "Lies", "release_date": "2017-12-01", "tmdb_episode_id": 1002},
        ])
        self.assertTrue(progress)
        self.assertTrue(all(current <= total for current, total, _message in progress))

    async def test_fuzzy_show_and_nonmatching_episode_stays_in_review(self) -> None:
        async def search_shows(_query: str, **_kwargs):
            return {"results": [{"id": 8, "name": "Dark Matter", "first_air_date": "2024-01-01"}]}

        async def search_movies(_query: str, **_kwargs):
            return {"results": []}

        async def show_details(tmdb_id: int, **_kwargs):
            return {"id": tmdb_id, "seasons": [{"season_number": 1, "name": "Season 1"}]}

        async def get_season(_tmdb_id: int, _season_number: int, **_kwargs):
            return {"episode_count": 1, "episodes": [{"episode_number": 1, "name": "Pilot"}]}

        result = await prepare_netflix_import(
            'Title,Date\n"Dark: Season 1: Secrets",8/29/26\n',
            search_movies_fn=search_movies,
            search_shows_fn=search_shows,
            show_details_fn=show_details,
            season_fn=get_season,
        )

        self.assertEqual(result["shows"][0]["status"], "review")
        self.assertNotEqual(result["shows"][0]["confidence"], "high")
        self.assertFalse(result["shows"][0]["episodes"][0]["matched"])
        self.assertEqual(result["shows"][0]["episodes"][0]["title"], "Secrets")

    async def test_season_summary_includes_unwatched_released_seasons_and_caps_current_count(self) -> None:
        async def search_shows(_query: str, **_kwargs):
            return {"results": [{"id": 9, "name": "Archive", "first_air_date": "2020-01-01"}]}

        async def search_movies(_query: str, **_kwargs):
            return {"results": []}

        async def show_details(tmdb_id: int, **_kwargs):
            return {
                "id": tmdb_id,
                "last_episode_to_air": {"season_number": 2, "episode_number": 3},
                "seasons": [
                    {"season_number": 1, "name": "Season 1", "episode_count": 2},
                    {"season_number": 2, "name": "Season 2", "episode_count": 10},
                    {"season_number": 3, "name": "Season 3", "episode_count": 0, "air_date": "2099-01-01"},
                ],
            }

        async def get_season(_tmdb_id: int, season_number: int, **_kwargs):
            self.assertEqual(season_number, 1)
            return {
                "episode_count": 2,
                "episodes": [
                    {"id": 901, "episode_number": 1, "name": "Pilot", "air_date": "2020-01-01"},
                    {"id": 902, "episode_number": 2, "name": "Aftermath", "air_date": "2020-01-01"},
                ],
            }

        result = await prepare_netflix_import(
            'Title,Date\n"Archive: Season 1: Pilot",1/2/20\n',
            search_movies_fn=search_movies,
            search_shows_fn=search_shows,
            show_details_fn=show_details,
            season_fn=get_season,
        )

        self.assertEqual(result["shows"][0]["seasons"], [
            {"season_number": 1, "represented": 1, "total_released": 2, "catalogue_complete": True},
            {"season_number": 2, "represented": 0, "total_released": 3, "catalogue_complete": False},
        ])
        self.assertEqual([ep["season_number"] for ep in result["shows"][0]["catalog_episodes"]], [1, 1])

    async def test_exact_colon_movie_wins_when_episode_candidate_does_not_match(self) -> None:
        async def search_shows(_query: str, **_kwargs):
            return {"results": []}

        async def search_movies(query: str, **_kwargs):
            if query == "The Hangover: Part II":
                return {"results": [{"id": 20, "title": "The Hangover: Part II", "release_date": "2011-05-25"}]}
            return {"results": []}

        result = await prepare_netflix_import(
            'Title,Date\n"The Hangover: Part II",3/1/26\n',
            search_movies_fn=search_movies,
            search_shows_fn=search_shows,
            show_details_fn=AsyncMock(),
            season_fn=AsyncMock(),
        )

        self.assertEqual(len(result["movies"]), 1)
        self.assertEqual(result["movies"][0]["tmdb_id"], 20)
        self.assertEqual(result["movies"][0]["status"], "matched")
        self.assertFalse(result["shows"])


if __name__ == "__main__":
    unittest.main()
