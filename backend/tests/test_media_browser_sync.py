import os
import unittest
from contextlib import ExitStack
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from sqlalchemy.sql import Select
from core.sync_jobs import SyncCancelled
from models.base import CollectionSource, MediaType
from models.connections import MediaServerConnection
from models.users import UserSettings
from routers import sync


class _Session:
    def __init__(self, connection, selected=()):
        self.connection = connection
        self.selected = [SimpleNamespace(library_id=key) for key in selected]
        self.writes = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        if isinstance(statement, Select):
            entity = statement.column_descriptions[0]["entity"]
            value = self.connection if entity is MediaServerConnection else SimpleNamespace() if entity is UserSettings else self.selected
            return SimpleNamespace(scalar_one_or_none=lambda: value, scalars=lambda: SimpleNamespace(all=lambda: value))
        self.writes.append(deepcopy(statement.compile().params))
        return SimpleNamespace()


class MediaBrowserPullTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, provider, *, key="tmdb-key", connection=True, selected=(), libraries=(), movies=(), shows=(), episodes=(),
                   movie_limit=0, show_limit=0, cancelled=False, fetch_error=None):
        conn = SimpleNamespace(id=7, type=provider, url="http://provider.local", token="token", server_user_id="remote-user",
                               sync_collection=True, sync_watched=True, sync_ratings=True) if connection else None
        db = _Session(conn, selected)
        adapter = getattr(sync, provider)
        mocks = {
            "libraries": AsyncMock(return_value=list(libraries), side_effect=fetch_error),
            "movies": AsyncMock(return_value=list(movies)),
            "shows": AsyncMock(return_value=list(shows)),
            "episodes": AsyncMock(return_value=list(episodes)),
            "items": AsyncMock(return_value=[]),
            "map": AsyncMock(return_value=({"matched": SimpleNamespace(id=10)}, {"matched": 42})),
            "remove": AsyncMock(return_value=[]),
            "push": AsyncMock(return_value=0),
            "reconcile": AsyncMock(return_value=({10}, {})),
            "propagate": AsyncMock(),
        }
        with ExitStack() as stack:
            stack.enter_context(patch.object(sync, "async_sessionmaker", return_value=lambda: db))
            stack.enter_context(patch.object(sync, "mark_job_running_unless_cancelled", AsyncMock(return_value=not cancelled)))
            stack.enter_context(patch.object(sync.settings_store, "get_effective_tmdb_key", AsyncMock(return_value=key)))
            for method in ["libraries", "movies", "shows", "episodes"]:
                stack.enter_context(patch.object(adapter, f"get_{method}", mocks[method]))
            for method, name in [("sync_items", "items"), ("sync_shows_batch", "map"),
                                 ("_remove_stale_collection_files", "remove"), ("_push_watched_back_to_source", "push")]:
                stack.enter_context(patch.object(sync, method, mocks[name]))
            stack.enter_context(patch.object(sync, "_stamp_matched_show_warnings", AsyncMock(side_effect=lambda db, user, warnings: warnings)))
            stack.enter_context(patch("core.media_server_reconciliation.reconcile_media_server_pull", mocks["reconcile"]))
            stack.enter_context(patch("core.pull_propagation.propagate_media_server_pull", mocks["propagate"]))
            stack.enter_context(patch.object(sync.asyncio, "create_task", side_effect=lambda coroutine: coroutine.close()))
            await sync._run_media_browser_sync(provider, 1, 2, movie_limit, show_limit, 7)
        return db, mocks

    async def test_cancelled_queued_jobs_do_not_contact_either_provider(self):
        for provider in ["jellyfin", "emby"]:
            with self.subTest(provider=provider):
                db, calls = await self._run(provider, cancelled=True)
                calls["libraries"].assert_not_awaited()
                self.assertEqual(db.writes, [])

    async def test_missing_connections_fail_before_provider_calls(self):
        for provider in ["jellyfin", "emby"]:
            with self.subTest(provider=provider):
                db, calls = await self._run(provider, connection=False)
                calls["libraries"].assert_not_awaited()
                self.assertEqual(db.writes[-1]["status"], sync.SyncStatus.failed)

    async def test_provider_specific_tmdb_requirements_are_preserved(self):
        db, calls = await self._run("jellyfin", key=None)
        calls["libraries"].assert_not_awaited()
        self.assertEqual(db.writes[-1]["error_message"], "Missing Jellyfin connection or TMDB API key")
        db, calls = await self._run("emby", key=None)
        calls["libraries"].assert_awaited_once()
        self.assertEqual(db.writes[-1]["status"], sync.SyncStatus.completed)

    async def test_selected_movie_libraries_and_limits_preserve_source_and_partial_pull(self):
        for provider in ["jellyfin", "emby"]:
            with self.subTest(provider=provider):
                movies = [{"Id": "one", "ProviderIds": {"Tmdb": "42"}}, {"Id": "two", "ProviderIds": {"Tmdb": "43"}}]
                _, calls = await self._run(provider, selected=["included"], movie_limit=1, movies=movies, libraries=[
                    {"Id": "excluded", "CollectionType": "movies"}, {"Id": "included", "CollectionType": "movies"},
                ])
                calls["movies"].assert_awaited_once_with("included", "http://provider.local", "token", "remote-user")
                self.assertEqual(calls["items"].call_args.args[:3], (movies[:1], MediaType.movie, CollectionSource(provider)))
                calls["remove"].assert_not_awaited()
                calls["push"].assert_not_awaited()
                self.assertFalse(calls["reconcile"].call_args.kwargs["complete"])

    async def test_matched_and_unmatched_episodes_reach_separate_batches(self):
        for provider in ["jellyfin", "emby"]:
            with self.subTest(provider=provider):
                episodes = [{"SeriesId": "matched"}, {"SeriesId": "unmatched"}, {"SeriesId": "other"}]
                db, calls = await self._run(provider, libraries=[{"Id": "tv", "CollectionType": "tvshows"}],
                    shows=[{"Id": "matched", "ProviderIds": {"Tmdb": "42"}}, {"Id": "unmatched", "Name": "Unknown"}], episodes=episodes)
                batches = calls["items"].call_args_list
                self.assertEqual([call.args[0] for call in batches], [[episodes[0]], [episodes[1]]])
                self.assertTrue(all(call.args[2] == CollectionSource(provider) for call in batches))
                self.assertEqual(batches[1].args[7], {})
                self.assertEqual(db.writes[-1]["warnings"][0]["source_id"], "unmatched")
                calls["remove"].assert_awaited_once()
                calls["propagate"].assert_awaited_once()

    async def test_cancellation_during_library_fetch_persists_zero_stats(self):
        for provider in ["jellyfin", "emby"]:
            with self.subTest(provider=provider):
                db, calls = await self._run(provider, fetch_error=SyncCancelled())
                self.assertEqual(db.writes[-1]["status"], sync.SyncStatus.cancelled)
                self.assertEqual(db.writes[-1]["stats"], {"movies": 0, "episodes": 0, "skipped": 0, "errors": 0})
                db.rollback.assert_awaited_once()
                calls["items"].assert_not_awaited()
