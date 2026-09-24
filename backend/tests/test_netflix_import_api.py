"""Focused regression checks for Netflix import decisions and episode resolution.

The database case uses the disposable local PostgreSQL instance only when
TRACKING_TEST_DATABASE_URL is explicitly configured.
"""
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "local-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from models import Media, NetflixImportSession, Rating, User, WatchEvent
from models.base import MediaType
from models.tracking import TrackedEntry
from routers.netflix_import import (
    _apply_staged_rating,
    _apply_import,
    _default_show_status,
    _existing_state,
    _normalise_groups,
    _summary,
    _show_import_positions,
    _public_session,
    _prepare_remapped_item,
    _resolve_draft_items,
    update_netflix_import_item,
)


def _episode(number, *, watched=False, watched_date="2020-01-03"):
    row = {
        "season_number": 1,
        "episode_number": number,
        "title": f"Episode {number}",
        "tmdb_episode_id": 990000 + number,
        "release_date": f"2020-01-0{number}",
    }
    if watched:
        row.update({
            "matched": True,
            "dates": [watched_date],
            "source_title": f"Fixture Show: Season 1: Episode {number}",
            "source_id": f"source-{number}",
            "decision": {"action": "confirm"},
        })
    return row


def _show_item():
    return {
        "id": "fixture-show",
        "kind": "show",
        "source_title": "Fixture Show",
        "source_dates": ["2020-01-02", "2020-01-03"],
        "match": {"state": "matched", "candidate": {
            "tmdb_id": 990001, "media_type": "show", "title": "Fixture Show",
            "details": {"name": "Fixture Show", "first_air_date": "2020-01-01"},
        }},
        "decision": {"action": "confirm"},
        "outcome": {"status": "partial", "latest_season": 1, "latest_episode": 2,
                    "tracking_status": "watching", "status_overridden": False},
        "episodes": [_episode(2, watched=True, watched_date="2020-01-02"),
                     _episode(3, watched=True)],
        "catalog_episodes": [_episode(1), _episode(2), _episode(3)],
        "seasons": [{"season_number": 1, "represented": 2,
                     "total_released": 3, "catalogue_complete": True}],
    }


class _MemoryResult:
    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


class _MemoryDb:
    def __init__(self):
        self.added = []
        self.executed = 0

    async def execute(self, *_args, **_kwargs):
        self.executed += 1
        return _MemoryResult()

    def add(self, value):
        self.added.append(value)


class NetflixAutomaticEpisodeImportTests(unittest.IsolatedAsyncioTestCase):
    async def _apply_with_memory_db(self, item):
        db = _MemoryDb()
        show = SimpleNamespace(canonical_source="tmdb")
        root = SimpleNamespace(id=990001)

        async def get_episode(_db, _show, _candidate, episode, _mappings):
            return SimpleNamespace(id=episode["season_number"] * 100 + episode["episode_number"])

        async def no_progress(*_args):
            return None

        session = SimpleNamespace(
            id="in-memory-session",
            payload={"items": [item], "errors": [], "counts": {}},
        )
        with (
            patch("routers.netflix_import._get_or_create_show_root", new=AsyncMock(return_value=(show, root))),
            patch("routers.netflix_import._get_or_create_episode", new=AsyncMock(side_effect=get_episode)),
            patch("routers.netflix_import._has_completed_event", new=AsyncMock(return_value=False)),
            patch("routers.netflix_import._track_imported_root", new=AsyncMock(return_value=(SimpleNamespace(), True))),
        ):
            stats = await _apply_import(db, 7, session, no_progress)
        return stats, db

    async def test_partial_import_writes_guessed_watch_dates_and_provisional_backfill(self):
        item = _show_item()
        guessed = item["episodes"][1]
        guessed.update({
            "resolution": "guessed", "matched": True,
            "season_number": 1, "episode_number": 3,
            "title": "Episode 3", "tmdb_episode_id": 990003,
            "dates": ["2020-01-03"],
        })
        item["outcome"].update({"status": "partial", "latest_season": 1, "latest_episode": 3,
                                "endpoint_overridden": True, "status_overridden": True})

        stats, db = await self._apply_with_memory_db(item)

        events_by_episode = {event.media_id % 100: event for event in db.added}
        self.assertEqual(set(events_by_episode), {1, 2, 3})
        self.assertTrue(events_by_episode[1].provisional)
        self.assertIsNone(events_by_episode[1].watched_at)
        self.assertFalse(events_by_episode[2].provisional)
        self.assertEqual(events_by_episode[2].watched_at.date(), date(2020, 1, 2))
        self.assertFalse(events_by_episode[3].provisional)
        self.assertEqual(events_by_episode[3].watched_at.date(), date(2020, 1, 3))
        self.assertEqual((stats["source_watches"], stats["source_episodes"], stats["inferred_episodes"], stats["guessed_episodes"]),
                         (2, 2, 1, 1))

    async def test_completed_import_fills_every_released_catalogue_position(self):
        item = _show_item()
        item["episodes"] = [_episode(1, watched=True, watched_date="2020-01-01")]
        item["outcome"].update({"status": "completed", "status_overridden": True})

        stats, db = await self._apply_with_memory_db(item)

        events_by_episode = {event.media_id % 100: event for event in db.added}
        self.assertEqual(set(events_by_episode), {1, 2, 3})
        self.assertFalse(events_by_episode[1].provisional)
        self.assertEqual(events_by_episode[1].watched_at.date(), date(2020, 1, 1))
        self.assertTrue(events_by_episode[2].provisional)
        self.assertTrue(events_by_episode[3].provisional)
        self.assertEqual((stats["source_episodes"], stats["inferred_episodes"]), (1, 2))

    async def test_completed_import_backfills_numbered_positions_without_episode_metadata(self):
        item = _show_item()
        item["episodes"] = [_episode(2, watched=True, watched_date="2020-01-02")]
        item["catalog_episodes"] = [_episode(2)]
        item["seasons"][0]["catalogue_complete"] = False
        item["outcome"].update({"status": "completed", "status_overridden": True})

        stats, db = await self._apply_with_memory_db(item)

        events_by_episode = {event.media_id % 100: event for event in db.added}
        self.assertEqual(set(events_by_episode), {1, 2, 3})
        self.assertTrue(events_by_episode[1].provisional)
        self.assertFalse(events_by_episode[2].provisional)
        self.assertTrue(events_by_episode[3].provisional)
        self.assertEqual((stats["episodes"], stats["inferred_episodes"]), (3, 2))

    async def test_only_unresolved_title_identity_blocks_import(self):
        candidate = {"tmdb_id": 990001, "media_type": "show", "title": "Fixture Show",
                     "details": {"name": "Fixture Show", "first_air_date": "2020-01-01"}}
        known_title_discarded_episode = {
            "id": "known-show", "kind": "show", "source_title": "Fixture Show",
            "match": {"state": "matched", "candidate": candidate},
            "decision": {"action": "confirm"},
            "outcome": {"status": "partial", "status_overridden": True, "tracking_status": "watching"},
            "episodes": [{"matched": False, "resolution": "discarded", "decision": {"action": None}}],
            "catalog_episodes": [], "seasons": [],
        }
        stats, db = await self._apply_with_memory_db(known_title_discarded_episode)
        self.assertEqual(stats["shows"], 1)
        self.assertEqual(stats["discarded_episodes"], 1)
        self.assertGreater(db.executed, 0)

        unresolved_title = {
            **known_title_discarded_episode,
            "id": "review-show",
            "match": {"state": "review", "candidate": candidate},
            "decision": {"action": None},
        }
        db = _MemoryDb()
        session = SimpleNamespace(payload={"items": [unresolved_title], "errors": [], "counts": {}})
        with self.assertRaisesRegex(ValueError, "uncertain title"):
            await _apply_import(db, 7, session, AsyncMock())
        self.assertEqual(db.executed, 0)

    async def test_show_remap_rebuilds_newest_first_csv_with_same_day_row_ties(self):
        item = {
            "id": "remapped-item", "kind": "show", "source_title": "Old Show",
            "source_dates": ["2020-01-01", "2020-01-02"], "source_rows": 3,
            "outcome": {"status": "partial", "status_overridden": True,
                        "latest_season": 1, "latest_episode": 2,
                        "endpoint_overridden": True, "tracking_status": "paused"},
            "episodes": [
                {
                    "source_title": "Old Show: Season 1: Beta", "season_label": "Season 1",
                    "source_episode_title": "Beta", "dates": ["2020-01-01"],
                    "source_row_numbers": [4],
                },
                {
                    "source_title": "Old Show: Season 1: Alpha", "season_label": "Season 1",
                    "source_episode_title": "Alpha", "dates": ["2020-01-01", "2020-01-02"],
                    "source_row_numbers": [3, 2],
                },
            ],
        }
        details = {"id": 4321, "name": "Selected Show", "first_air_date": "2020-01-01"}
        prepare = AsyncMock(return_value={"shows": [{
            "kind": "show", "source_title": "Selected Show", "tmdb_id": 4321,
            "title": "Selected Show", "status": "matched", "confidence": "high",
            "episodes": [], "catalog_episodes": [], "seasons": [
                {"season_number": 1, "represented": 0, "total_released": 1, "catalogue_complete": True},
            ],
        }]})

        with (
            patch("routers.netflix_import.tmdb.get_show_light", new=AsyncMock(return_value=details)),
            patch("core.netflix_import.prepare_netflix_import", new=prepare),
        ):
            refreshed = await _prepare_remapped_item(item, "show", 4321, "test-key", "en-US")

        history = prepare.await_args.args[0]
        self.assertEqual(
            [(row.episode_title, row.watched_at, row.source_rows) for row in history.rows],
            [
                ("Alpha", "2020-01-02", [2]),
                ("Alpha", "2020-01-01", [3]),
                ("Beta", "2020-01-01", [4]),
            ],
        )
        self.assertEqual(refreshed["decision"]["action"], "remap")
        self.assertEqual(refreshed["outcome"]["status"], "partial")
        self.assertEqual(refreshed["outcome"]["tracking_status"], "paused")
        self.assertEqual((refreshed["outcome"]["latest_season"], refreshed["outcome"]["latest_episode"]), (1, 1))

    async def test_single_unconfirmed_show_observation_can_remap_to_movie(self):
        item = {
            "id": "film-as-show", "kind": "show", "source_title": "El Camino",
            "source_dates": ["2024-01-01"], "source_rows": 1,
            "episodes": [{
                "source_title": "El Camino: A Breaking Bad Movie", "resolution": "guessed",
                "dates": ["2024-01-01"], "source_row_numbers": [2],
            }],
            "outcome": {"status": "partial", "latest_season": 1, "latest_episode": 1},
        }
        details = {"id": 559969, "title": "El Camino: A Breaking Bad Movie", "release_date": "2019-10-11", "genres": [{"id": 18}], "original_language": "en"}
        with patch("routers.netflix_import.tmdb.get_movie_light", new=AsyncMock(return_value=details)):
            refreshed = await _prepare_remapped_item(item, "movie", 559969, "test-key", "en-US")
        self.assertEqual(refreshed["kind"], "movie")
        self.assertEqual(refreshed["source_title"], "El Camino: A Breaking Bad Movie")
        self.assertEqual(refreshed["source_dates"], ["2024-01-01"])
        self.assertEqual(refreshed["decision"], {"action": "remap"})
        self.assertEqual(refreshed["outcome"]["status"], "completed")

        item["episodes"][0]["resolution"] = "exact"
        with self.assertRaisesRegex(ValueError, "unconfirmed episode"):
            await _prepare_remapped_item(item, "movie", 559969, "test-key", "en-US")

    async def test_movie_shaped_entry_can_be_manually_remapped_to_show(self):
        item = {
            "id": "ambiguous-title", "kind": "movie", "source_title": "Ambiguous Title",
            "source_dates": ["2024-01-01"], "source_rows": 1,
            "outcome": {"status": "completed", "manual_score": 8.0, "manual_score_staged": True},
        }
        details = {"id": 4321, "name": "Selected Show", "first_air_date": "2020-01-01"}
        prepare = AsyncMock(return_value={"shows": [{
            "kind": "show", "source_title": "Selected Show", "tmdb_id": 4321,
            "title": "Selected Show", "status": "review", "confidence": "medium",
            "episodes": [{"source_title": "Selected Show: Ambiguous Title", "dates": ["2024-01-01"]}],
            "catalog_episodes": [], "seasons": [],
        }]})
        with (
            patch("routers.netflix_import.tmdb.get_show_light", new=AsyncMock(return_value=details)),
            patch("core.netflix_import.prepare_netflix_import", new=prepare),
        ):
            refreshed = await _prepare_remapped_item(item, "show", 4321, "test-key", "en-US")

        self.assertEqual([(row.source_title, row.watched_at) for row in prepare.await_args.args[0].rows],
                         [("Selected Show: Ambiguous Title", "2024-01-01")])
        self.assertEqual(refreshed["id"], item["id"])
        self.assertEqual(refreshed["source_title"], item["source_title"])
        self.assertEqual(refreshed["decision"], {"action": "remap"})
        self.assertEqual(refreshed["outcome"]["manual_score"], 8.0)


class NetflixDecisionTests(unittest.TestCase):
    def test_sparse_seasons_default_to_skip_and_any_dense_season_defaults_to_partial(self):
        sparse = [
            {"season_number": 1, "represented": 2, "total_released": 8},
            {"season_number": 2, "represented": 1, "total_released": 6},
        ]
        self.assertEqual(_default_show_status(sparse), "skip")
        self.assertEqual(_default_show_status([sparse[0], {**sparse[1], "represented": 3}]), "partial")
        self.assertEqual(_default_show_status([{"season_number": 1, "represented": 2, "total_released": 2, "catalogue_complete": True}]), "completed")

        item = _show_item()
        item["seasons"] = sparse
        item["episodes"] = [{"resolution": "exact"}, {"resolution": "guessed"}]
        _resolve_draft_items([item])
        self.assertEqual(item["outcome"]["status"], "skip")
        item["outcome"].update(status="partial", status_overridden=True)
        _resolve_draft_items([item])
        self.assertEqual(item["outcome"]["status"], "partial")

    def test_existing_rating_flag_uses_effective_score(self):
        tracked = SimpleNamespace(rating_mode="manual", manual_score=7.5, season_scores={}, status="completed", progress=1)
        self.assertTrue(_existing_state(SimpleNamespace(), tracked)["rated"])
        tracked.rating_mode, tracked.manual_score, tracked.season_scores = "average", None, {"1": 8.0}
        self.assertTrue(_existing_state(SimpleNamespace(), tracked)["rated"])
        tracked.season_scores = {}
        self.assertFalse(_existing_state(SimpleNamespace(), tracked)["rated"])

    def test_anime_items_are_skipped_and_remain_skipped_when_episodes_resolve(self):
        prepared = {"shows": [{
            "kind": "show", "source_title": "Anime Show", "tmdb_id": 501,
            "title": "Anime Show", "status": "matched", "confidence": "high",
            "is_anime": True, "episodes": [{
                "source_title": "Anime Show: Episode 1", "source_episode_title": "Episode 1",
                "dates": ["2020-01-03"], "source_rows": [2],
            }],
            "catalog_episodes": [{
                "season_number": 1, "episode_number": 1, "title": "Pilot",
                "release_date": "2020-01-01", "tmdb_episode_id": 50101,
            }],
            "seasons": [{"season_number": 1, "represented": 0, "total_released": 1, "catalogue_complete": True}],
        }]}
        item = _normalise_groups(prepared)[0]
        self.assertEqual(item["decision"], {"action": "skip"})
        self.assertEqual(item["outcome"]["status"], "skip")
        _resolve_draft_items([item])
        self.assertEqual(item["decision"], {"action": "skip"})
        self.assertEqual(item["outcome"]["status"], "skip")

    def test_lower_partial_endpoint_excludes_later_source_episode(self):
        sources, chosen, excluded = _show_import_positions(_show_item(), {"status": "partial", "latest_season": 1, "latest_episode": 2}, date(2026, 9, 24))
        self.assertEqual(set(sources), {(1, 2)})
        self.assertEqual(chosen, {(1, 1), (1, 2)})
        self.assertEqual(excluded, 1)

    def test_review_summary_respects_partial_endpoint(self):
        summary = _summary([_show_item()])
        self.assertEqual((summary["source_watches"], summary["source_episodes"], summary["inferred_episodes"], summary["cutoff_exclusions"]), (1, 1, 1, 1))

    def test_final_episode_total_follows_progress_and_skip_choices(self):
        item = _show_item()
        self.assertEqual((_summary([item])["shows"], _summary([item])["episodes"]), (1, 2))
        item["outcome"] = {"status": "completed"}
        self.assertEqual((_summary([item])["shows"], _summary([item])["episodes"]), (1, 3))
        item["outcome"] = {"status": "skip"}
        self.assertEqual((_summary([item])["shows"], _summary([item])["episodes"]), (0, 0))

    def test_completed_fills_season_counts_when_catalogue_rows_are_missing(self):
        item = _show_item()
        item["seasons"][0]["catalogue_complete"] = False
        item["catalog_episodes"] = [_episode(2)]
        sources, chosen, excluded = _show_import_positions(item, {"status": "completed"}, date(2026, 9, 24))
        self.assertEqual(set(sources), {(1, 2), (1, 3)})
        self.assertEqual(chosen, {(1, 1), (1, 2), (1, 3)})
        self.assertEqual(excluded, 0)
        self.assertEqual(_summary([{**item, "outcome": {"status": "completed"}}])["episodes"], 3)

    def test_matcher_shape_normalizes_source_row_lists_and_suggestions(self):
        raw = {"shows": [{
            "kind": "show", "source_title": "Fixture Show", "tmdb_id": 990001,
            "title": "Fixture Show", "status": "review", "confidence": "medium",
            "suggestions": [{"tmdb_id": 990001, "title": "Fixture Show", "media_type": "show"}],
            "episodes": [{**_episode(2, watched=True), "source_rows": [2, 3],
                          "dates": ["2020-01-02"]}],
            "catalog_episodes": [_episode(1), _episode(2)],
            "seasons": [{"season_number": 1, "represented": 1,
                         "total_released": 2, "catalogue_complete": True}],
        }]}
        item = _normalise_groups(raw)[0]
        self.assertEqual(item["episodes"][0]["source_rows"], 2)
        self.assertEqual(item["episodes"][0]["source_row_numbers"], [2, 3])
        self.assertEqual(item["episodes"][0]["dates"], ["2020-01-02"])
        self.assertEqual(item["match"]["candidates"][0]["tmdb_id"], 990001)

    def test_unmatched_source_id_is_not_treated_as_tmdb_id(self):
        item = _normalise_groups({"unmatched": [{
            "id": "show:source-digest", "kind": "show",
            "source_title": ": Episode 2", "status": "review",
            "episodes": [{"source_title": ": Episode 2", "title": "Episode 2", "matched": False}],
        }]})[0]
        self.assertIsNone(item["match"]["candidate"])
        self.assertEqual(item["match"]["state"], "unmatched")

    def test_sparse_show_defaults_to_skip_even_at_latest_released_episode(self):
        raw = {"shows": [{
            "kind": "show", "source_title": "Fixture Show", "tmdb_id": 990001,
            "title": "Fixture Show", "status": "matched", "confidence": "high",
            "episodes": [{**_episode(3, watched=True), "dates": ["2020-01-03"]}],
            "catalog_episodes": [_episode(1), _episode(2), _episode(3)],
            "seasons": [{"season_number": 1, "represented": 1,
                         "total_released": 3, "catalogue_complete": True}],
        }]}
        item = _normalise_groups(raw)[0]
        self.assertEqual(item["outcome"]["status"], "skip")
        self.assertEqual((item["outcome"]["latest_season"], item["outcome"]["latest_episode"]), (1, 3))

    def test_normalization_guesses_positions_recalculates_represented_count_endpoint_and_summary(self):
        catalog = [
            {"season_number": 3, "episode_number": number, "title": f"S3E{number}",
             "release_date": "2020-01-01", "tmdb_episode_id": 3000 + number}
            for number in range(1, 9)
        ]
        episodes = [{
            "season_number": 3, "episode_number": 4, "title": "S3E4",
            "source_episode_title": "S3E4", "source_title": "Fixture Show: Season 3: S3E4",
            "dates": ["2020-01-01"], "source_rows": [10], "matched": True,
            "tmdb_episode_id": 3004,
        }]
        episodes.extend({
            "season_number": 3, "episode_number": None, "season_label": "Season 3",
            "title": f"Unknown {number}", "source_episode_title": f"Unknown {number}",
            "source_title": f"Fixture Show: Season 3: Unknown {number}",
            "dates": [f"2020-01-0{number + 1}"], "source_rows": [10 - number],
            "matched": False,
        } for number in range(1, 6))
        item = _normalise_groups({"shows": [{
            "kind": "show", "source_title": "Fixture Show", "tmdb_id": 990001,
            "title": "Fixture Show", "status": "matched", "confidence": "high",
            "episodes": episodes, "catalog_episodes": catalog,
            "seasons": [{"season_number": 3, "represented": 1, "total_released": 8, "catalogue_complete": True}],
        }]})[0]

        summary = _summary([item])
        guesses = [episode for episode in item["episodes"] if episode.get("resolution") == "guessed"]
        discarded = [episode for episode in item["episodes"] if episode.get("resolution") == "discarded"]
        self.assertEqual([(episode["season_number"], episode["episode_number"]) for episode in guesses],
                         [(3, 5), (3, 6), (3, 7), (3, 8)])
        self.assertEqual(len(discarded), 1)
        self.assertEqual(item["seasons"][0]["represented"], 5)
        self.assertEqual((item["outcome"]["latest_season"], item["outcome"]["latest_episode"]), (3, 8))
        self.assertEqual((summary["source_episodes"], summary["guessed_episodes"], summary["discarded_episodes"]), (5, 4, 1))
        self.assertEqual(summary["inferred_episodes"], 3)

    def test_existing_review_draft_resolves_episodes_and_preserves_title_decision_and_manual_endpoint(self):
        item = _show_item()
        unknown = item["episodes"][1]
        unknown.update({
            "matched": False, "episode_number": None, "tmdb_episode_id": None,
            "resolution": None, "title": "Previously uncertain episode",
            "source_episode_title": "Previously uncertain episode",
            "source_title": "Fixture Show: Season 1: Previously uncertain episode",
        })
        item["decision"] = {"action": "confirm"}
        item["outcome"]["latest_episode"] = 2
        item["outcome"]["endpoint_overridden"] = True
        session = SimpleNamespace(
            id="legacy-draft", status="review", phase="review", progress={}, revision=4,
            payload={"items": [item], "counts": {}, "errors": []}, result={}, error_message=None,
        )

        public = _public_session(session)
        upgraded = public["items"][0]
        guessed = next(episode for episode in upgraded["episodes"] if episode.get("source_episode_title") == "Previously uncertain episode")

        self.assertEqual(upgraded["decision"]["action"], "confirm")
        self.assertEqual((guessed["season_number"], guessed["episode_number"], guessed["resolution"]), (1, 3, "guessed"))
        self.assertEqual(upgraded["outcome"]["latest_episode"], 2)

    def test_default_partial_endpoint_advances_to_newly_guessed_position(self):
        item = _show_item()
        unknown = item["episodes"][1]
        unknown.update({
            "matched": False, "episode_number": None, "tmdb_episode_id": None,
            "resolution": None, "title": "Previously uncertain episode",
            "source_episode_title": "Previously uncertain episode",
            "source_title": "Fixture Show: Season 1: Previously uncertain episode",
        })
        item["outcome"].pop("endpoint_overridden", None)
        items = [item]

        _resolve_draft_items(items)

        self.assertEqual((items[0]["outcome"]["latest_season"], items[0]["outcome"]["latest_episode"]), (1, 3))


@unittest.skipUnless(os.getenv("TRACKING_TEST_DATABASE_URL"), "Requires disposable PostgreSQL database")
class NetflixCommitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TRACKING_TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.db = AsyncSession(bind=self.connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        self.user = User(username="netflix-import-fixture", email="netflix-import-fixture@example.test", api_key="netflix-import-fixture")
        self.db.add(self.user)
        await self.db.flush()

    async def asyncTearDown(self):
        await self.db.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def test_partial_cutoff_creates_only_earlier_history_and_is_idempotent(self):
        item = _show_item()
        item["outcome"]["status_overridden"] = True
        session = SimpleNamespace(id="fixture-session", payload={"items": [item], "errors": [], "counts": {}})

        async def progress(*_args):
            return None

        first = await _apply_import(self.db, self.user.id, session, progress)
        await self.db.flush()
        events = (await self.db.execute(select(WatchEvent, Media).join(Media, Media.id == WatchEvent.media_id).where(WatchEvent.user_id == self.user.id))).all()
        self.assertEqual({media.episode_number for _, media in events}, {1, 2})
        self.assertEqual({media.episode_number for event, media in events if event.provisional}, {1})
        self.assertEqual({media.episode_number for event, media in events if event.watched_at is not None}, {2})
        entry = (await self.db.execute(select(TrackedEntry).where(TrackedEntry.user_id == self.user.id))).scalar_one()
        self.assertEqual((entry.status, entry.progress, entry.start_date, entry.finish_date),
                         ("watching", 2, date(2020, 1, 2), None))
        self.assertEqual((first["source_watches"], first["inferred_episodes"], first["cutoff_exclusions"]), (1, 1, 1))

        second = await _apply_import(self.db, self.user.id, session, progress)
        await self.db.flush()
        self.assertEqual(second["source_watches"], 0)
        self.assertEqual(second["inferred_episodes"], 0)
        count = (await self.db.execute(select(WatchEvent.id).where(WatchEvent.user_id == self.user.id))).all()
        self.assertEqual(len(count), 2)

    async def test_progress_endpoint_autosaves_without_a_title_action(self):
        item = _show_item()
        draft = NetflixImportSession(
            id=str(uuid4()), user_id=self.user.id, status="review", phase="review",
            progress={}, revision=1, payload={"items": [item], "counts": {}, "errors": []},
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
        )
        self.db.add(draft)
        await self.db.flush()
        updated = await update_netflix_import_item(
            draft.id, item["id"], {"revision": 1, "latest_episode": 1}, self.db, self.user,
        )
        self.assertEqual(updated["items"][0]["outcome"]["latest_episode"], 1)
        self.assertEqual(updated["revision"], 2)

    async def test_manual_rating_is_staged_without_writing_rating_or_tracking(self):
        item = {"id": "rating-movie", "kind": "movie", "source_title": "Rating Movie",
                "match": {"state": "matched", "candidate": {"tmdb_id": 880001, "media_type": "movie", "title": "Rating Movie"}},
                "decision": {"action": "confirm"}, "outcome": {"status": "completed"},
                "source_dates": ["2020-01-01"]}
        draft = NetflixImportSession(
            id=str(uuid4()), user_id=self.user.id, status="review", phase="review",
            progress={}, revision=1, payload={"items": [item], "counts": {}, "errors": []},
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
        )
        self.db.add(draft)
        await self.db.flush()
        with self.assertRaises(HTTPException) as raised:
            await update_netflix_import_item(draft.id, item["id"], {"revision": 1, "manual_score": 7.25}, self.db, self.user)
        self.assertEqual(raised.exception.status_code, 422)
        updated = await update_netflix_import_item(
            draft.id, item["id"], {"revision": 1, "manual_score": 8.5}, self.db, self.user,
        )
        self.assertEqual(updated["items"][0]["outcome"]["manual_score"], 8.5)
        self.assertTrue(updated["items"][0]["outcome"]["manual_score_staged"])
        self.assertEqual(updated["summary"]["movies"], 1)
        cleared = await update_netflix_import_item(
            draft.id, item["id"], {"revision": 2, "manual_score": None}, self.db, self.user,
        )
        self.assertNotIn("manual_score", cleared["items"][0]["outcome"])
        self.assertNotIn("manual_score_staged", cleared["items"][0]["outcome"])
        self.assertEqual((await self.db.execute(select(Rating).where(Rating.user_id == self.user.id))).scalars().all(), [])
        self.assertEqual((await self.db.execute(select(TrackedEntry).where(TrackedEntry.user_id == self.user.id))).scalars().all(), [])

    async def test_commit_applies_staged_rating_and_preserves_existing_rating_without_choice(self):
        async def progress(*_args):
            return None

        async def commit_movie(tmdb_id: int, staged_score: float | None):
            item = {"id": f"movie-{tmdb_id}", "kind": "movie", "source_title": f"Movie {tmdb_id}",
                    "match": {"state": "matched", "candidate": {"tmdb_id": tmdb_id, "media_type": "movie", "title": f"Movie {tmdb_id}", "details": {}}},
                    "decision": {"action": "confirm"}, "outcome": {"status": "completed"},
                    "source_dates": ["2020-01-01"]}
            if staged_score is not None:
                item["outcome"].update(manual_score=staged_score, manual_score_staged=True)
            session = SimpleNamespace(id=f"session-{tmdb_id}", payload={"items": [item], "errors": [], "counts": {}})
            async def create_media(db, ident, media_type, **kwargs):
                media = Media(tmdb_id=ident, media_type=media_type,
                              **{k: v for k, v in kwargs.items() if k in {"title", "tmdb_rating", "tmdb_data", "adult"}})
                db.add(media)
                await db.flush()
                return media, True

            with patch("routers.netflix_import.create_media_safely", new=AsyncMock(side_effect=create_media)):
                await _apply_import(self.db, self.user.id, session, progress)
            return (await self.db.execute(select(Media).where(Media.tmdb_id == tmdb_id, Media.media_type == MediaType.movie))).scalar_one()

        staged = await commit_movie(880002, 9.0)
        staged_entry = (await self.db.execute(select(TrackedEntry).where(TrackedEntry.user_id == self.user.id, TrackedEntry.media_id == staged.id))).scalar_one()
        staged_rating = (await self.db.execute(select(Rating).where(Rating.user_id == self.user.id, Rating.media_id == staged.id))).scalar_one()
        self.assertEqual((staged_entry.rating_mode, staged_entry.manual_score, staged_rating.rating), ("manual", 9.0, 9.0))

        show_root = Media(tmdb_id=880004, media_type=MediaType.series, title="Rated Show")
        self.db.add(show_root)
        await self.db.flush()
        show_entry = TrackedEntry(user_id=self.user.id, media_id=show_root.id, status="watching", rating_mode="manual", season_scores={}, progress=0)
        self.db.add(show_entry)
        await self.db.flush()
        await _apply_staged_rating(self.db, self.user.id, show_root, show_entry,
                                   {"manual_score": 7.5, "manual_score_staged": True})
        show_rating = (await self.db.execute(select(Rating).where(
            Rating.user_id == self.user.id, Rating.media_id == show_root.id,
            Rating.season_number.is_(None), Rating.episode_order.is_(None),
        ))).scalar_one()
        self.assertEqual((show_entry.manual_score, show_rating.rating), (7.5, 7.5))

        existing = await commit_movie(880003, None)
        entry = (await self.db.execute(select(TrackedEntry).where(TrackedEntry.user_id == self.user.id, TrackedEntry.media_id == existing.id))).scalar_one()
        entry.manual_score = 6.5
        self.db.add(Rating(user_id=self.user.id, media_id=existing.id, rating=6.5))
        await self.db.flush()
        await _apply_import(self.db, self.user.id, SimpleNamespace(id="preserve", payload={
            "items": [{"id": "movie-880003", "kind": "movie", "source_title": "Movie 880003",
                       "match": {"state": "matched", "candidate": {"tmdb_id": 880003, "media_type": "movie", "title": "Movie 880003", "details": {}}},
                       "decision": {"action": "confirm"}, "outcome": {"status": "completed"}, "source_dates": ["2020-01-01"]}],
            "errors": [], "counts": {},
        }), progress)
        await self.db.flush()
        self.assertEqual((entry.manual_score, (await self.db.execute(select(Rating).where(Rating.user_id == self.user.id, Rating.media_id == existing.id))).scalar_one().rating), (6.5, 6.5))
