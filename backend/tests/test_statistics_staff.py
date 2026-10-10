"""Staff aggregation deduplicates people, jobs, and canonical listed works."""
import unittest
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock, MagicMock

from core.statistics_staff import load_staff, staff_groups
from core.catalogue_normalize import normalize_tmdb


def person(key="catalogue:1", role="Director", **extra):
    return {"key": key, "label": "Person", "image": None, "role": role, "provider": "tmdb", **extra}


def title(id, score=8, **extra):
    return {"key": str(id), "name": f"Film {id:02}", "poster": None, "detail_media_id": id,
            "score": score, "minutes": 100, "runtime_missing": 0, "staff": [person()], **extra}


class StaffTests(unittest.TestCase):
    def test_jobs_and_provider_duplicates_count_once_and_keep_role_labels(self):
        group = staff_groups([title(1, staff=[person(), person(role="Writer"), person(provider="tvdb")]),
                              title(2, 6, minutes=300), title(3, None, minutes=0)])[0]
        self.assertEqual((group["titles"], group["minutes"], group["mean_score"], group["rated_titles"]), (3, 400, 7, 2))
        self.assertEqual(group["top_titles"][0]["roles"], ["Director", "Writer"])
        self.assertEqual(group["href"], "/person/catalogue%3A1")
        self.assertIsNone(group["top_titles"][-1]["score"])

    def test_personal_ratings_select_twelve_and_same_name_people_stay_separate(self):
        rows = staff_groups([title(i, i / 2) for i in range(1, 20)])
        self.assertEqual([r["score"] for r in rows[0]["top_titles"]], [i / 2 for i in range(19, 7, -1)])
        self.assertEqual(len(staff_groups([title(1, staff=[person(), person(key="catalogue:2")])])), 2)

    def test_candidate_bound_and_prominence_preserve_creative_people_on_ties(self):
        rows = [title(i, staff=[person(key=f"catalogue:{i}", role="Assistant", label=f"A{i:03}")]) for i in range(1, 201)]
        rows.append(title(201, staff=[person(key="catalogue:201", label="Z Director")]))
        selected = staff_groups(rows)
        self.assertLessEqual(len(selected), 90)
        self.assertIn("catalogue:201", {r["key"] for r in selected})

    def test_tmdb_creators_and_available_person_metadata_are_projected(self):
        doc = normalize_tmdb({"id": 1, "name": "Show", "created_by": [{"id": 9, "name": "Creator"}],
            "credits": {"crew": [{"id": 10, "name": "Director", "job": "Director", "biography": "Biography", "birthday": "1970-01-01"}]}}, "series")
        creator = next(c for c in doc["credits"] if c["role_label"] == "Creator")
        self.assertEqual(creator["contributor"]["name"], "Creator")
        director = next(c for c in doc["credits"] if c["role_label"] == "Director")
        self.assertEqual(director["contributor"]["description"], "Biography")
        self.assertEqual(director["contributor"]["attributes"]["birthday"], "1970-01-01")


class StaffLoadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_acting_labels_excluded_verified_ids_merge_creators_and_primary_crew(self):
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = [(Row(work_id=10, role="director", role_label="Director", provider="tmdb"), Row(id=1, name="Director", image_url=None)),
            (Row(work_id=10, role="contributor", role_label="Guest Star", provider="tvdb"), Row(id=2, name="Actor", image_url=None)),
            (Row(work_id=10, role="director", role_label="Director", provider="tvdb"), Row(id=3, name="Director", image_url=None))]
        db.execute.return_value = result
        db.scalars.return_value = [Row(external_id="9", entity_id=1)]
        fact = title(1, listed=True, entity_id=10, media=[], data={"created_by": [{"id": 9, "name": "Director"}]})
        history = title(2, listed=False, entity_id=11, media=[], data={})
        await load_staff(db, [fact, history])
        self.assertEqual({p["key"] for p in fact["staff"]}, {"catalogue:1"})
        self.assertEqual(staff_groups([fact])[0]["roles"], ["Director", "Creator"])
        self.assertNotIn("Creator", [p["role"] for p in history["staff"]])
