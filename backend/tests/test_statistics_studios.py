"""Studio cards share Genres' watched cohort and personal-rating semantics."""
import unittest
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock, MagicMock

import test_statistics_facts as fixtures
from core.statistics_studios import load_studios, studio_groups
from core.catalogue_normalize import normalize_tmdb


def title(i, **values):
    return {"key": str(i), "name": f"Film {i:02}", "score": 8, "minutes": 90, "runtime_missing": 0,
            "poster": None, "detail_media_id": i, "studios": [{"key": "catalogue:1", "label": "Studio"}], **values}


class StudioTests(unittest.TestCase):
    def test_duplicates_count_one_title_and_repeats_use_personal_score(self):
        one = title(1, minutes=270)
        one["studios"] *= 2
        group = studio_groups([one, title(2, score=None, minutes=60)])[0]
        self.assertEqual((group["titles"], group["minutes"], group["rated_titles"], group["mean_score"]), (2, 330, 1, 8))
        self.assertEqual(group["href"], "/studio/catalogue%3A1")
        self.assertEqual(len(group["top_titles"]), 1)

    def test_twelve_best_rated_titles_and_missing_details(self):
        rows = [title(i, score=i / 2) for i in range(1, 21)]
        rows += [title(21, score=10, detail_media_id=None)]
        group = studio_groups(rows)[0]
        self.assertEqual([r["score"] for r in group["top_titles"]], [i / 2 for i in range(20, 8, -1)])
        self.assertEqual(group["titles"], 21)

    def test_same_name_different_companies_are_not_merged(self):
        groups = studio_groups([title(1), title(2, studios=[{"key": "catalogue:2", "label": "Studio"}])])
        self.assertEqual(len(groups), 2)

    def test_top_eighteen_candidates_for_each_sort_are_preserved(self):
        rows = [title(i, score=i % 20 / 2, minutes=i, studios=[{"key": str(i), "label": str(i)}]) for i in range(1, 101)]
        groups = studio_groups(rows)
        self.assertLessEqual(len(groups), 54)
        self.assertTrue({str(i) for i in range(83, 101)} <= {r["key"] for r in groups})

    def test_planning_is_excluded_and_history_only_watched_is_included(self):
        planned = fixtures.media(1, "movie", title="Planning")
        watched = fixtures.media(2, "movie", title="History", runtime=90)
        facts, coverage = fixtures.title_facts([(fixtures.entry(1, 10, status="planning"), planned)], [(fixtures.event(plays=2), watched)],
            [], [planned, watched], [], {}, {}, {}, {}, show_anime=True)
        for row in facts:
            row["studios"] = [{"key": "catalogue:1", "label": "Studio"}]
        data = fixtures.build_overviews(facts, coverage)
        self.assertEqual((data["all"]["studios"][0]["titles"], data["all"]["studios"][0]["minutes"]), (1, 180))
        self.assertIsNone(data["all"]["studios"][0]["mean_score"])
        self.assertEqual(data["series"]["studios"], [])

    def test_company_logo_and_country_are_projected_without_networks_becoming_producers(self):
        doc = normalize_tmdb({"id": 1, "title": "Film", "production_companies": [{"id": 2, "name": "Studio", "origin_country": "US", "logo_path": "/studio.png"}],
                              "networks": [{"id": 3, "name": "Network"}]}, "movie")
        company = doc["credits"][0]
        self.assertEqual(company["role"], "producer")
        self.assertEqual(company["contributor"]["attributes"], {"origin_country": "US"})
        self.assertEqual(company["contributor"]["image_url"], "https://image.tmdb.org/t/p/original/studio.png")
        self.assertEqual(doc["credits"][1]["role"], "broadcaster")


class StudioLoadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_authoritative_companies_prevent_secondary_provider_duplicates(self):
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = [(Row(work_id=10, provider="tmdb"), Row(id=1, name="Studio")),
                                   (Row(work_id=10, provider="tvdb"), Row(id=2, name="Studio"))]
        db.execute.return_value = result
        db.scalars.return_value = []
        fact = title(1, entity_id=10, plays=1, kind="movie", media=[], data={})
        await load_studios(db, [fact])
        self.assertEqual([r["key"] for r in fact["studios"]], ["catalogue:1"])

    async def test_legacy_company_ids_resolve_verified_aliases_without_name_matching(self):
        db = AsyncMock()
        result = MagicMock(); result.all.return_value = []
        db.execute.return_value = result
        db.scalars.return_value = [Row(external_id="2", entity_id=20)]
        fact = title(1, entity_id=None, plays=1, kind="movie", media=[], data={"production_companies": [{"id": 2, "name": "Studio"}, {"id": 3, "name": "Studio"}]})
        await load_studios(db, [fact])
        self.assertEqual({r["key"] for r in fact["studios"]}, {"catalogue:20", "tmdb:3"})
