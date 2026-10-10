"""Actor metrics count listed canonical titles, not roles or provider duplicates."""
import unittest
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace as Row

import test_statistics_facts as fixtures
from core.statistics_actors import actor_groups, load_actors
from core.catalogue_normalize import normalize_tvdb


def actor(key="catalogue:1", **values):
    return {"key": key, "label": "Actor", "image": "https://example.test/person.jpg", "character": "Alice",
            "character_image": None, "provider": "tmdb", "position": 0, **values}


def title(key, score=8, **values):
    return {"key": str(key), "name": f"Film {key:02}", "poster": None, "detail_media_id": key,
            "score": score, "minutes": 100, "runtime_missing": 0, "actors": [actor()], **values}


class ActorTests(unittest.TestCase):
    def test_multiple_roles_and_cross_provider_credits_count_title_once(self):
        one = title(1, actors=[actor(), actor(character="Bob"), actor(provider="tvdb", character_image="https://example.test/alice.jpg")])
        two = title(2, 6, minutes=300)
        data = actor_groups([one, two])[0]
        self.assertEqual((data["titles"], data["minutes"], data["rated_titles"], data["mean_score"]), (2, 400, 2, 7))
        self.assertEqual(data["top_titles"][0]["character"], "Alice / Bob")
        self.assertEqual(data["top_titles"][0]["character_image"], "https://example.test/alice.jpg")

    def test_top_twelve_personal_ratings_with_unrated_last(self):
        data = actor_groups([title(i, i / 2) for i in range(1, 20)] + [title(20, None)])[0]
        self.assertEqual(data["titles"], 20)
        self.assertEqual(data["rated_titles"], 19)
        self.assertEqual([r["score"] for r in data["top_titles"]], [i / 2 for i in range(19, 7, -1)])
        self.assertIsNone(actor_groups([title(1, None)])[0]["mean_score"])
        self.assertIsNone(actor_groups([title(1, None)])[0]["top_titles"][0]["score"])

    def test_same_name_different_persons_remain_separate(self):
        self.assertEqual(len(actor_groups([title(1, actors=[actor(), actor(key="catalogue:2")])])), 2)

    def test_only_listed_titles_contribute_including_planned_titles(self):
        film = fixtures.media(1, "movie", title="Planned", tmdb_id=1)
        other = fixtures.media(2, "movie", title="History only", tmdb_id=2)
        facts, coverage = fixtures.title_facts([(fixtures.entry(1, 9, status="planning"), film)], [(fixtures.event(), other)],
            [], [film, other], [], {}, {}, {}, {}, show_anime=True)
        for fact in facts:
            fact["actors"] = [actor()]
        data = fixtures.build_overviews(facts, coverage)["all"]["actors"][0]
        self.assertEqual((data["titles"], data["minutes"], data["mean_score"]), (1, 0, 9))

    def test_role_image_is_separate_from_person_portrait_and_identity_verified(self):
        doc = normalize_tvdb({"id": 1, "name": "Show", "characters": [{"id": 8, "peopleId": 9, "personName": "Actor", "type": 3,
            "name": "Alice", "image": "banners/actors/alice.jpg", "personImgURL": "https://example.test/person.jpg"}],
            "_people": {"9": {"remoteIds": [{"sourceName": "TheMovieDB.com", "id": "123"}]}}})
        credit = doc["credits"][0]
        self.assertEqual(credit["character_image_url"], "https://artworks.thetvdb.com/banners/actors/alice.jpg")
        self.assertEqual(credit["contributor"]["image_url"], "https://example.test/person.jpg")
        self.assertIn({"namespace": "tmdb.person", "external_id": "123"}, credit["contributor"]["identities"])
        self.assertEqual(doc["characters"], [])

    def test_tvdb_movie_projects_movie_identities_and_empty_role_image_stays_absent(self):
        doc = normalize_tvdb({"id": 42, "name": "Film", "first_release": {"date": "2020-01-01"},
            "companies": {"studio": [{"id": 7, "name": "Studio"}], "production": [], "distributor": []},
            "remoteIds": [{"sourceName": "TheMovieDB.com", "id": "123"}],
            "characters": [{"id": 1, "type": 3, "peopleId": 2, "personName": "Actor", "image": "", "personImgURL": "https://example.test/person.jpg"}]}, "movie")
        self.assertEqual(doc["work"]["kind"], "movie")
        self.assertEqual(doc["work"]["attributes"]["release_date"], "2020-01-01")
        self.assertEqual(doc["credits"][1]["role"], "producer")
        self.assertIn({"namespace": "tmdb.movie", "external_id": "123"}, doc["work"]["identities"])
        self.assertIsNone(doc["credits"][0]["character_image_url"])

    def test_candidate_bound_keeps_top_thirty_for_all_metrics(self):
        rows = [title(i, (i % 20) / 2, minutes=i, actors=[actor(key=f"catalogue:{i}", label=str(i))]) for i in range(1, 201)]
        groups = actor_groups(rows)
        self.assertLessEqual(len(groups), 90)
        self.assertTrue(all(f"catalogue:{i}" in {r["key"] for r in groups} for i in range(171, 201)))


class ActorLoadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_cast_and_verified_artwork_merge_without_name_matching(self):
        db = AsyncMock()
        credits = MagicMock()
        credits.all.return_value = [(Row(work_id=10, character_label="Alice", character_image_url=None, provider="tmdb", position=0), Row(id=1, name="Actor", image_url=None)),
            (Row(work_id=10, character_label="Alice", character_image_url="https://example.test/role.jpg", provider="tvdb", position=0), Row(id=1, name="Actor", image_url=None)),
            (Row(work_id=10, character_label="Alice", character_image_url=None, provider="tvdb", position=0), Row(id=2, name="Actor", image_url=None))]
        db.execute.return_value = credits
        fact = title(1, listed=True, entity_id=10, media=[])
        await load_actors(db, [fact])
        self.assertEqual({a["key"] for a in fact["actors"]}, {"catalogue:1"})
        self.assertEqual(actor_groups([fact])[0]["top_titles"][0]["character_image"], "https://example.test/role.jpg")
