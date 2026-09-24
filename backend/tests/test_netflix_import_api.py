"""Focused regression checks for reviewed Netflix import decisions.

The database case uses the disposable local PostgreSQL instance only when
TRACKING_TEST_DATABASE_URL is explicitly configured.
"""
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

os.environ.setdefault("SECRET_KEY", "local-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from models import Media, NetflixImportSession, User, WatchEvent
from models.base import MediaType
from models.tracking import TrackedEntry
from routers.netflix_import import (
    _apply_import,
    _normalise_groups,
    _remap_episode_to_catalog,
    _summary,
    _show_import_positions,
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


class NetflixDecisionTests(unittest.TestCase):
    def test_lower_partial_endpoint_excludes_later_source_episode(self):
        sources, chosen, excluded = _show_import_positions(_show_item(), {"status": "partial", "latest_season": 1, "latest_episode": 2}, date(2026, 9, 24))
        self.assertEqual(set(sources), {(1, 2)})
        self.assertEqual(chosen, {(1, 1), (1, 2)})
        self.assertEqual(excluded, 1)

    def test_review_summary_respects_partial_endpoint(self):
        summary = _summary([_show_item()])
        self.assertEqual((summary["source_watches"], summary["source_episodes"], summary["inferred_episodes"], summary["cutoff_exclusions"]), (1, 1, 1, 1))

    def test_episode_remap_uses_target_catalog_identity(self):
        item = _show_item()
        source = item["episodes"][0]
        original_id = source["tmdb_episode_id"]
        _remap_episode_to_catalog(item, source, 1, 1)
        self.assertNotEqual(source["tmdb_episode_id"], original_id)
        self.assertEqual(source["tmdb_episode_id"], item["catalog_episodes"][0]["tmdb_episode_id"])
        self.assertEqual(source["dates"], ["2020-01-02"])

    def test_incomplete_catalogue_cannot_complete(self):
        item = _show_item()
        item["seasons"][0]["catalogue_complete"] = False
        with self.assertRaisesRegex(ValueError, "catalogue data is incomplete"):
            _show_import_positions(item, {"status": "completed"}, date(2026, 9, 24))

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

    def test_incomplete_show_defaults_to_partial_even_at_latest_released_episode(self):
        raw = {"shows": [{
            "kind": "show", "source_title": "Fixture Show", "tmdb_id": 990001,
            "title": "Fixture Show", "status": "matched", "confidence": "high",
            "episodes": [{**_episode(3, watched=True), "dates": ["2020-01-03"]}],
            "catalog_episodes": [_episode(1), _episode(2), _episode(3)],
            "seasons": [{"season_number": 1, "represented": 1,
                         "total_released": 3, "catalogue_complete": True}],
        }]}
        item = _normalise_groups(raw)[0]
        self.assertEqual(item["outcome"]["status"], "partial")
        self.assertEqual((item["outcome"]["latest_season"], item["outcome"]["latest_episode"]), (1, 3))


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
        session = SimpleNamespace(id="fixture-session", payload={"items": [_show_item()], "errors": [], "counts": {}})

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
