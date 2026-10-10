import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import runtime_backfill as backfill
from models.base import MediaType


def movie(**kw):
    return SimpleNamespace(media_type=MediaType.movie, tmdb_id=10, runtime=None,
                           tmdb_data=kw.pop("tmdb_data", {"tracking_keep": True}), **kw)


def episode(identity, **kw):
    return SimpleNamespace(media_type=MediaType.episode, tmdb_id=identity, tvdb_id=None,
                           season_number=1, runtime=None, tmdb_data={}, **kw)


def session(rows):
    return SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows)), commit=AsyncMock())


class RuntimeBackfillTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_runtime_needs_no_provider_and_retains_metadata(self):
        row = movie(tmdb_data={"runtime": 98, "tracking_keep": True})
        db = session([(row, None)])
        with patch.object(backfill.tmdb, "get_movie", AsyncMock()) as fetch:
            result = await backfill.backfill_history_runtimes(db)
        self.assertEqual(row.runtime, 98)
        self.assertTrue(row.tmdb_data["tracking_keep"])
        self.assertEqual(result["recovered"], 1)
        fetch.assert_not_awaited()
        db.commit.assert_awaited_once()

    async def test_movie_identity_must_match_and_zero_is_unresolved(self):
        for response in ({"id": 11, "runtime": 100}, {"id": 10, "runtime": 0}):
            row = movie()
            with patch.object(backfill.tmdb, "get_movie", AsyncMock(return_value=response)):
                result = await backfill.backfill_history_runtimes(session([(row, None)]), "key")
            self.assertIsNone(row.runtime)
            self.assertEqual(result["unresolved"], 1)

    async def test_shared_season_matches_episode_id_not_number(self):
        rows = [episode(100), episode(101)]
        show = SimpleNamespace(tmdb_id=20, tvdb_id=None)
        response = {"episodes": [{"id": 100, "episode_number": 9, "runtime": 25},
                                 {"id": 101, "episode_number": 1, "runtime": 45}]}
        with patch.object(backfill.tmdb, "get_season", AsyncMock(return_value=response)) as fetch:
            result = await backfill.backfill_history_runtimes(session([(r, show) for r in rows]), "key")
        fetch.assert_awaited_once()
        self.assertEqual([r.runtime for r in rows], [25, 45])
        self.assertEqual(result["recovered"], 2)

    async def test_tvdb_uses_stable_episode_identity(self):
        row = episode(None)
        row.tvdb_id = 300
        show = SimpleNamespace(tmdb_id=None, tvdb_id=20)
        with patch.object(backfill.tvdb, "get_series_episodes", AsyncMock(return_value=[
            {"id": 301, "runtime": 99}, {"id": 300, "runtime": 40},
        ])):
            await backfill.backfill_history_runtimes(session([(row, show)]), tvdb_api_key="key")
        self.assertEqual(row.runtime, 40)

    async def test_failure_is_isolated_and_retry_is_delayed(self):
        rows = [movie(), movie()]
        rows[1].tmdb_id = 11
        with patch.object(backfill.tmdb, "get_movie", AsyncMock(side_effect=[
            RuntimeError("private provider error"), {"id": 11, "runtime": 100},
        ])):
            result = await backfill.backfill_history_runtimes(session([(r, None) for r in rows]), "key")
        self.assertEqual((result["failed"], result["recovered"]), (1, 1))
        self.assertFalse(backfill.due(rows[0].tmdb_data, datetime.now(timezone.utc)))
        self.assertTrue(backfill.due(rows[0].tmdb_data, datetime.now(timezone.utc) + timedelta(days=8)))

    async def test_recent_attempt_is_not_requested_again(self):
        row = movie(tmdb_data={"runtime_metadata_version": backfill.VERSION, "runtime_backfill_attempted_at": datetime.now(timezone.utc).isoformat()})
        with patch.object(backfill.tmdb, "get_movie", AsyncMock()) as fetch:
            result = await backfill.backfill_history_runtimes(session([(row, None)]), "key")
        fetch.assert_not_awaited()
        self.assertEqual(result["examined"], 0)

    async def test_unmatched_episode_uses_show_estimate_without_inventing_exact_runtime(self):
        rows = [episode(None), episode(None)]
        show = SimpleNamespace(tmdb_id=20, tvdb_id=30, tmdb_data={"tracking_keep": True})
        with patch.object(backfill.tvdb, "get_series", AsyncMock(return_value={"id": 30, "averageRuntime": 45})) as fetch:
            result = await backfill.backfill_history_runtimes(session([(r, show) for r in rows]), tvdb_api_key="key")
        fetch.assert_awaited_once()
        self.assertEqual(show.tmdb_data["episode_run_time"], [45])
        self.assertTrue(show.tmdb_data["tracking_keep"])
        self.assertTrue(all(r.runtime is None for r in rows))
        self.assertEqual(result["estimated_shows"], 1)

    async def test_force_can_retry_recent_attempt_with_improved_provider_configuration(self):
        row = movie(tmdb_data={"runtime_backfill_attempted_at": datetime.now(timezone.utc).isoformat()})
        with patch.object(backfill.tmdb, "get_movie", AsyncMock(return_value={"id": 10, "runtime": 100})):
            result = await backfill.backfill_history_runtimes(session([(row, None)]), "key", force=True)
        self.assertEqual(result["recovered"], 1)

    async def test_tvdb_recovers_exact_runtime_after_tmdb_failure(self):
        row = episode(100)
        row.tvdb_id = 300
        show = SimpleNamespace(tmdb_id=20, tvdb_id=30)
        with patch.object(backfill.tmdb, 'get_season', AsyncMock(side_effect=RuntimeError('private'))), patch.object(
            backfill.tvdb, 'get_series_episodes', AsyncMock(return_value=[{'id':300,'runtime':42}])) as fetch:
            result = await backfill.backfill_history_runtimes(session([(row,show)]), 'tmdb', tvdb_api_key='tvdb')
        fetch.assert_awaited_once()
        self.assertEqual(row.runtime,42)
        self.assertEqual((result['recovered'],result['failed']),(1,1))

    async def test_new_backfill_version_retries_recent_older_attempt(self):
        row = movie(tmdb_data={'runtime_backfill_attempted_at':datetime.now(timezone.utc).isoformat()})
        with patch.object(backfill.tmdb,'get_movie',AsyncMock(return_value={'id':10,'runtime':100})):
            result = await backfill.backfill_history_runtimes(session([(row,None)]),'key')
        self.assertEqual(result['recovered'],1)

    async def test_tvdb_movie_duration_requires_matching_native_id(self):
        row=movie(tvdb_id=30)
        with patch.object(backfill.tvdb,'get_movie',AsyncMock(return_value={'id':30,'runtime':99})):
            result=await backfill.backfill_history_runtimes(session([(row,None)]),tvdb_api_key='key')
        self.assertEqual(row.runtime,99)
        self.assertEqual(result['recovered'],1)

    async def test_series_average_is_available_even_when_episode_endpoint_fails(self):
        row=episode(None);row.tvdb_id=30
        show=SimpleNamespace(tmdb_id=None,tvdb_id=20,tmdb_data={})
        with patch.object(backfill.tvdb,'get_series_episodes',AsyncMock(side_effect=RuntimeError('private'))), patch.object(
            backfill.tvdb,'get_series',AsyncMock(return_value={'id':20,'averageRuntime':45})):
            result=await backfill.backfill_history_runtimes(session([(row,show)]),tvdb_api_key='key')
        self.assertEqual(show.tmdb_data['episode_run_time'],[45])
        self.assertIsNone(row.runtime)
        self.assertEqual(result['estimated_shows'],1)
