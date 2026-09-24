import os
import unittest
from unittest.mock import AsyncMock

import httpx

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core.netflix_import import parse_netflix_csv, prepare_netflix_import, resolve_netflix_episodes


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


def _catalogue(*season_sizes: int, released: str = "2020-01-01") -> list[dict]:
    return [
        {
            "season_number": season_number,
            "episode_number": episode_number,
            "title": f"S{season_number}E{episode_number}",
            "release_date": released,
            "tmdb_episode_id": season_number * 1000 + episode_number,
        }
        for season_number, count in enumerate(season_sizes, start=1)
        for episode_number in range(1, count + 1)
    ]


def _episode(
    title: str,
    watched_at: str,
    row: int,
    *,
    season: int = 1,
    matched_position: tuple[int, int] | None = None,
    season_label: str | None = None,
) -> dict:
    season_number, episode_number = matched_position or (season, None)
    return {
        "season_number": season_number,
        "episode_number": episode_number,
        "season_label": season_label or f"Season {season}",
        "source_episode_title": title,
        "title": title if matched_position else title,
        "watched_dates": [watched_at],
        "source_rows": [row],
        "matched": matched_position is not None,
    }


class NetflixEpisodeResolutionTests(unittest.TestCase):
    def test_guesses_before_between_and_after_exact_anchors_in_viewing_order(self) -> None:
        # The input follows Netflix's newest-first CSV order. Resolution must
        # sort by date first, while keeping each uncertain run inside anchors.
        episodes = [
            _episode("After", "2020-01-06", 2),
            _episode("Anchor 7", "2020-01-05", 3, matched_position=(1, 7)),
            _episode("Between later", "2020-01-04", 4),
            _episode("Between earlier", "2020-01-03", 5),
            _episode("Anchor 3", "2020-01-02", 6, matched_position=(1, 3)),
            _episode("Before", "2020-01-01", 7),
        ]

        resolve_netflix_episodes(episodes, _catalogue(8))

        positions = {episode["source_episode_title"]: (episode["season_number"], episode["episode_number"])
                     for episode in episodes if episode.get("matched")}
        self.assertEqual(positions, {
            "After": (1, 8),
            "Anchor 7": (1, 7),
            "Between later": (1, 5),
            "Between earlier": (1, 4),
            "Anchor 3": (1, 3),
            "Before": (1, 2),
        })
        self.assertTrue(all(episode["resolution"] == "guessed" for episode in episodes if episode["source_episode_title"] in {"Before", "Between later", "Between earlier", "After"}))

    def test_four_observations_after_s3e4_advance_to_s3e8_in_eight_and_twelve_episode_seasons(self) -> None:
        for season_size in (8, 12):
            with self.subTest(season_size=season_size):
                episodes = [
                    _episode(f"Unknown {number}", f"2020-01-0{number + 1}", 10 - number)
                    for number in range(4, 0, -1)
                ]
                episodes.append(_episode("Confirmed four", "2020-01-01", 11, matched_position=(3, 4), season=3))
                catalogue = [
                    {**ep, "release_date": "2020-01-01"}
                    for ep in _catalogue(1, 1, season_size)
                ]

                resolve_netflix_episodes(episodes, catalogue)

                guessed = sorted(
                    (episode["episode_number"] for episode in episodes if episode.get("resolution") == "guessed"),
                )
                self.assertEqual(guessed, [5, 6, 7, 8])

    def test_trailing_observations_roll_over_into_the_next_released_season(self) -> None:
        episodes = [
            _episode("Later observation", "2020-02-02", 2),
            _episode("First after anchor", "2020-02-01", 3),
            _episode("Season one finale", "2020-01-31", 4, matched_position=(1, 2)),
        ]

        resolve_netflix_episodes(episodes, _catalogue(2, 2))

        positions = {
            episode["source_episode_title"]: (episode["season_number"], episode["episode_number"])
            for episode in episodes if episode.get("resolution") == "guessed"
        }
        self.assertEqual(positions, {"First after anchor": (2, 1), "Later observation": (2, 2)})

    def test_without_an_anchor_starts_in_the_labelled_catalogue_season(self) -> None:
        episodes = [
            _episode("Second watched", "2020-01-02", 2, season=2),
            _episode("First watched", "2020-01-01", 3, season=2),
        ]

        resolve_netflix_episodes(episodes, _catalogue(2, 2))

        positions = {
            episode["source_episode_title"]: (episode.get("season_number"), episode.get("episode_number"))
            for episode in episodes
        }
        self.assertEqual(positions, {"Second watched": (2, 2), "First watched": (2, 1)})

    def test_exact_anchor_maps_a_split_netflix_season_label_to_the_catalogue_season(self) -> None:
        episodes = [
            _episode("Part two unknown", "2020-01-02", 2, season=2, season_label="Season 2 Part 2"),
            _episode("Part two exact", "2020-01-01", 3, season=2,
                     matched_position=(1, 2), season_label="Season 2 Part 2"),
        ]

        resolve_netflix_episodes(episodes, _catalogue(3, 2))

        guessed = next(episode for episode in episodes if episode["source_episode_title"] == "Part two unknown")
        self.assertEqual((guessed["season_number"], guessed["episode_number"]), (1, 3))
        self.assertEqual(guessed["resolution"], "guessed")

    def test_newest_first_rows_same_day_order_repeats_and_watch_dates_are_preserved(self) -> None:
        episodes = [
            # Row 2 is later in the viewing order than row 3 on the same day.
            _episode("Same day later", "2020-01-01", 2),
            {
                **_episode("Repeated episode", "2020-01-01", 3),
                "watched_dates": ["2020-01-01", "2020-01-02"],
                "dates": ["2020-01-01", "2020-01-02"],
                "source_rows": [3, 4],
            },
        ]

        resolve_netflix_episodes(episodes, _catalogue(3))

        repeated = next(episode for episode in episodes if episode["source_episode_title"] == "Repeated episode")
        later = next(episode for episode in episodes if episode["source_episode_title"] == "Same day later")
        self.assertEqual((repeated["season_number"], repeated["episode_number"]), (1, 1))
        self.assertEqual((later["season_number"], later["episode_number"]), (1, 2))
        self.assertEqual(repeated["watched_dates"], ["2020-01-01", "2020-01-02"])
        self.assertEqual(repeated["source_rows"], [3, 4])

    def test_missing_or_unreleased_catalogue_positions_are_discarded_and_excess_inside_anchors_is_covered(self) -> None:
        missing_season = [_episode("No season two catalogue", "2020-01-01", 2, season=2)]
        resolve_netflix_episodes(missing_season, _catalogue(2))
        self.assertEqual(missing_season[0]["resolution"], "discarded")
        self.assertFalse(missing_season[0]["matched"])

        unreleased = [
            _episode("Anchor one", "2020-01-01", 2, matched_position=(1, 1)),
            _episode("Aired tomorrow", "2020-01-02", 3),
        ]
        resolve_netflix_episodes(unreleased, [
            *_catalogue(1),
            {"season_number": 1, "episode_number": 2, "title": "S1E2", "release_date": "2020-01-03"},
        ])
        guessed = next(episode for episode in unreleased if episode["source_episode_title"] == "Aired tomorrow")
        self.assertEqual(guessed["resolution"], "discarded")
        self.assertFalse(guessed["matched"])

        bounded = [
            _episode("Only free position", "2020-01-02", 4),
            _episode("Excess observation", "2020-01-03", 3),
            _episode("Anchor three", "2020-01-04", 2, matched_position=(1, 3)),
            _episode("Anchor one", "2020-01-01", 5, matched_position=(1, 1)),
        ]
        resolve_netflix_episodes(bounded, _catalogue(3))
        statuses = {episode["source_episode_title"]: episode["resolution"] for episode in bounded}
        self.assertEqual(statuses["Only free position"], "guessed")
        self.assertEqual(statuses["Excess observation"], "covered")

    def test_no_anchor_excess_observations_do_not_duplicate_or_exceed_catalogue(self) -> None:
        episodes = [
            _episode("Third", "2020-01-03", 2),
            _episode("Second", "2020-01-02", 3),
            _episode("First", "2020-01-01", 4),
        ]

        resolve_netflix_episodes(episodes, _catalogue(2))

        self.assertEqual(sum(episode["resolution"] == "guessed" for episode in episodes), 2)
        self.assertEqual(sum(episode["resolution"] in {"discarded", "covered"} for episode in episodes), 1)
        self.assertEqual(len({(episode.get("season_number"), episode.get("episode_number"))
                              for episode in episodes if episode.get("matched")}), 2)


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

    async def test_guessed_episode_does_not_resolve_a_fuzzy_show_identity(self) -> None:
        async def search_shows(_query: str, **_kwargs):
            return {"results": [{"id": 8, "name": "Dark Matter", "first_air_date": "2024-01-01"}]}

        async def search_movies(_query: str, **_kwargs):
            return {"results": []}

        async def show_details(tmdb_id: int, **_kwargs):
            return {"id": tmdb_id, "seasons": [{"season_number": 1, "name": "Season 1"}]}

        async def get_season(_tmdb_id: int, _season_number: int, **_kwargs):
            return {"episode_count": 1, "episodes": [{"episode_number": 1, "name": "Pilot", "air_date": "2024-01-01"}]}

        result = await prepare_netflix_import(
            'Title,Date\n"Dark: Season 1: Secrets",8/29/26\n',
            search_movies_fn=search_movies,
            search_shows_fn=search_shows,
            show_details_fn=show_details,
            season_fn=get_season,
        )

        self.assertEqual(result["shows"][0]["status"], "review")
        self.assertNotEqual(result["shows"][0]["confidence"], "high")
        self.assertTrue(result["shows"][0]["episodes"][0]["matched"])
        self.assertEqual(result["shows"][0]["episodes"][0]["resolution"], "guessed")
        self.assertEqual(result["shows"][0]["episodes"][0]["episode_number"], 1)

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

    async def test_single_guessed_episode_does_not_override_exact_colon_movie(self) -> None:
        for title, show_name, movie_id, is_anime in (
            ("El Camino: A Breaking Bad Movie", "El Camino", 559969, False),
            ("EVANGELION: DEATH (TRUE)²", "EVANGELION", 18624, True),
        ):
            with self.subTest(title=title):
                async def search_shows(query: str, **_kwargs):
                    return {"results": [{"id": 91, "name": show_name}] if query == show_name else []}

                async def search_movies(query: str, **_kwargs):
                    return {"results": [{
                        "id": movie_id, "title": title, "release_date": "2019-01-01",
                        "genre_ids": [16] if is_anime else [18],
                        "original_language": "ja" if is_anime else "en",
                    }]} if query == title else {"results": []}

                result = await prepare_netflix_import(
                    f'Title,Date\n"{title}",1/1/24\n',
                    search_movies_fn=search_movies,
                    search_shows_fn=search_shows,
                    show_details_fn=AsyncMock(return_value={
                        "id": 91, "name": show_name,
                        "seasons": [{"season_number": 1, "episode_count": 1}],
                    }),
                    season_fn=AsyncMock(return_value={
                        "episode_count": 1,
                        "episodes": [{"id": 9101, "episode_number": 1, "name": "Pilot", "air_date": "2019-01-01"}],
                    }),
                )

                self.assertEqual(len(result["movies"]), 1)
                self.assertEqual(result["movies"][0]["tmdb_id"], movie_id)
                self.assertEqual(result["movies"][0]["is_anime"], is_anime)
                self.assertFalse(result["shows"])


if __name__ == "__main__":
    unittest.main()
