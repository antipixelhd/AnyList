import os
import unittest
from datetime import datetime
from types import SimpleNamespace as Row

os.environ.setdefault("SECRET_KEY", "statistics-test-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core.statistics_facts import build_overviews, title_facts, regular_total
from models.base import MediaType


def media(id, kind, **values):
    return Row(**{**dict(id=id, media_type=MediaType(kind), tmdb_id=None, tvdb_id=None, show_id=None,
                        runtime=None, tmdb_data={}, release_date=None, season_number=None), **values})


def entry(id, score=None, **values):
    return Row(**{**dict(id=id, updated_at=datetime(2026, 1, 1), status="completed", rating_mode="manual",
                        manual_score=score, season_scores={}, progress=0), **values})


def event(when=None, plays=1, **values):
    return Row(**{**dict(watched_at=when, play_count=plays, date_inferred=False, date_shared=False, provisional=False), **values})


class StatisticsFactsTests(unittest.TestCase):
    def build(self, entries, events, shows=(), media_rows=(), episodes=(), links=None, show_links=None, identities=None, entities=None, show_anime=True):
        rows = list(media_rows) or list({m.id: m for _, m in [*entries, *events] if m.media_type != MediaType.episode}.values())
        facts, coverage = title_facts(entries, events, list(shows), rows, list(episodes), links or {}, show_links or {}, identities or {}, entities or {}, show_anime=show_anime)
        return build_overviews(facts, coverage)

    def test_all_charts_share_canonical_watched_title_cohort_and_exact_scores(self):
        movie = media(1, "movie", runtime=100, release_date="2020-03-01", tmdb_data={"production_countries": ["US", "GB"]})
        show = Row(id=7, tmdb_id=10, tvdb_id=20, first_air_date="2010-01-01", tmdb_data={"episode_run_time": [30]})
        series = media(2, "series", tmdb_id=10, tmdb_data={"origin_country": ["JP"]})
        planned = media(3, "movie", runtime=60)
        ep1 = media(4, "episode", show_id=7, runtime=40, season_number=1)
        ep2 = media(5, "episode", show_id=7, season_number=1)
        special = media(6, "episode", show_id=7, runtime=20, season_number=0)
        root = Row(id=100, kind="series", attributes={"regular_episode_count": 12, "release_date": "2010-01-01"})
        result = self.build([(entry(1, 8), movie), (entry(2, rating_mode="season", season_scores={"1": 7, "2": 7, "3": 8}), series),
                             (entry(3, status="planning"), planned)],
                            [(event(datetime(2025, 1, 1), 3), movie), (event(datetime(2026, 1, 1), 2), ep1),
                             (event(datetime(2024, 1, 1), date_inferred=True), ep2), (event(), special), (event(), ep1)],
                            [show], episodes=[ep1, ep2, special], links={2: 100}, show_links={7: 100}, entities={100: root})
        data = result["all"]
        self.assertEqual(data["totals"]["listed_titles"], 3)
        self.assertEqual(data["totals"]["watched_titles"], 2)
        self.assertEqual(data["totals"]["episode_plays"], 5)
        self.assertEqual(data["totals"]["distinct_episodes"], 3)
        self.assertEqual(data["totals"]["watch_minutes"], 470)
        self.assertEqual(data["totals"]["planned_minutes"], 60)
        self.assertAlmostEqual(data["totals"]["mean_score"], 23 / 3)
        self.assertAlmostEqual(data["totals"]["standard_deviation"], 1 / 3)
        self.assertEqual(data["scores"][14]["titles"], 1)  # (7, 7.5], season mean 7.333...
        self.assertEqual(data["scores"][15]["minutes"], 300)
        self.assertEqual(data["episode_counts"][2]["titles"], 1)
        self.assertEqual(data["coverage"]["runtime_estimated_plays"], 1)
        self.assertEqual(data["coverage"]["unattributed_date_plays"], 6)
        self.assertEqual(sum(x["share"] for x in data["countries"]), 2)
        self.assertEqual(data["watch_years"], [
            {"key": "2025", "label": "2025", "titles": 1, "minutes": 100.0, "mean_score": 8.0, "rated_titles": 1},
            {"key": "2026", "label": "2026", "titles": 1, "minutes": 40.0, "mean_score": 22 / 3, "rated_titles": 1},
        ])
        self.assertEqual(data["release_years"][0]["minutes"], 170)
        self.assertEqual(result["movie"]["totals"]["watch_minutes"] + result["series"]["totals"]["watch_minutes"], 470)

    def test_missing_metadata_and_history_only_titles_are_not_invented(self):
        movie = media(1, "movie")
        data = self.build([], [(event(), movie)])["all"]
        self.assertEqual(data["totals"]["listed_titles"], 0)
        self.assertEqual(data["totals"]["watched_titles"], 1)
        self.assertIsNone(data["totals"]["mean_score"])
        self.assertEqual(data["coverage"]["runtime_missing_plays"], 1)
        self.assertEqual(data["countries"][0]["key"], "Unknown")
        self.assertEqual(data["release_years"][0]["key"], "Unknown")
        self.assertEqual(data["watch_years"], [])

    def test_verified_provider_aliases_deduplicate_titles_and_current_rating(self):
        one = media(1, "series", tmdb_id=10)
        two = media(2, "series", tvdb_id=20)
        show = Row(id=7, tmdb_id=10, tvdb_id=20, first_air_date=None, tmdb_data={})
        ep = media(3, "episode", show_id=7, runtime=30)
        result = self.build([(entry(1, 6), one), (entry(2, 9, updated_at=datetime(2026, 2, 1)), two)],
                            [(event(), ep)], [show])["all"]
        self.assertEqual(result["totals"]["listed_titles"], 1)
        self.assertEqual(result["totals"]["watched_titles"], 1)
        self.assertEqual(result["totals"]["mean_score"], 9)
        self.assertEqual(result["totals"]["standard_deviation"], 0)

    def test_hidden_anime_is_excluded_from_list_and_play_metrics(self):
        hidden = media(1, "movie", runtime=80, tmdb_data={"genres": ["Animation"], "origin_country": ["jpn"]})
        data = self.build([(entry(1, 8), hidden)], [(event(datetime(2026, 1, 1)), hidden)], show_anime=False)["all"]
        self.assertEqual(data["totals"]["listed_titles"], 0)
        self.assertEqual(data["totals"]["watch_minutes"], 0)
        self.assertEqual(data["countries"], [])

    def test_planning_uses_complete_regular_catalogue_and_unique_completed_membership(self):
        series = media(1, "series", tmdb_id=10, tmdb_data={"tracking_episode_ids": [11, 12, 13], "tracking_catalogue_refreshed_at": "2026-01-01"})
        show = Row(id=7, tmdb_id=10, tvdb_id=None, first_air_date=None, tmdb_data={})
        completed = media(11, "episode", show_id=7, tmdb_id=11, season_number=1, runtime=20, release_date="2020-01-01")
        remaining = media(12, "episode", show_id=7, tmdb_id=12, season_number=1, runtime=30, release_date="2020-01-01")
        unaired = media(13, "episode", show_id=7, tmdb_id=13, season_number=1, runtime=40, release_date="2199-01-01")
        special = media(14, "episode", show_id=7, tmdb_id=14, season_number=0, runtime=50, release_date="2020-01-01")
        inputs = ([(entry(1, status="planning", progress=1), series)], [(event(plays=3), completed)], [show])
        data = self.build(*inputs, episodes=[completed, remaining, unaired, special])["all"]
        self.assertEqual(data["totals"]["planned_minutes"], 30)
        self.assertEqual(data["coverage"]["planned_unknown_titles"], 0)
        data = self.build(*inputs, episodes=[completed, unaired])["all"]
        self.assertEqual(data["coverage"]["planned_unknown_titles"], 1)

    def test_progress_without_known_episode_membership_has_unknown_planned_time(self):
        series = media(1, "series", tmdb_id=10, tmdb_data={"tracking_episode_ids": [], "tracking_catalogue_refreshed_at": "2026-01-01"})
        data = self.build([(entry(1, status="planning", progress=4), series)], [])["all"]
        self.assertEqual(data["coverage"]["planned_unknown_titles"], 1)

    def test_unknown_release_dates_do_not_claim_complete_planned_workload(self):
        series = media(1, "series", tmdb_id=10, tmdb_data={"tracking_episode_ids": [11, 12], "tracking_catalogue_refreshed_at": "2026-01-01"})
        show = Row(id=7, tmdb_id=10, tvdb_id=None, first_air_date=None, tmdb_data={})
        known = media(11, "episode", show_id=7, tmdb_id=11, season_number=1, runtime=30, release_date="2020-01-01")
        unknown = media(12, "episode", show_id=7, tmdb_id=12, season_number=1, runtime=40)
        data = self.build([(entry(1, status="planning"), series)], [], [show], episodes=[known, unknown])["all"]
        self.assertEqual(data["totals"]["planned_minutes"], 30)
        self.assertEqual(data["coverage"]["planned_unknown_titles"], 1)

    def test_empty_years_and_unrated_means_remain_distinct(self):
        movie = media(1, "movie", runtime=50)
        data = self.build([], [(event(datetime(2024, 1, 1)), movie), (event(datetime(2026, 1, 1)), movie)])["all"]
        self.assertEqual(data["watch_years"][1]["titles"], 0)
        self.assertEqual(data["watch_years"][1]["minutes"], 0)
        self.assertIsNone(data["watch_years"][1]["mean_score"])

    def test_partial_local_episode_rows_never_establish_total_length(self):
        self.assertIsNone(regular_total({"number_of_episodes": 12}))
        self.assertEqual(regular_total({"seasons": [{"season_number": 0, "episode_count": 8}, {"season_number": 1, "episode_count": 12}]}), 12)
