import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
from core import server_sync

from fastapi import BackgroundTasks, HTTPException
from models.base import CollectionSource
from models.library_selections import JellyfinLibrarySelection, EmbyLibrarySelection
from models.sync import SyncStatus
from routers import sync


def session(*results):
    async def refresh(job):
        job.id = 19

    return SimpleNamespace(
        execute=AsyncMock(side_effect=[SimpleNamespace(scalar_one_or_none=lambda value=value: value) for value in results]),
        add=Mock(), commit=AsyncMock(), refresh=AsyncMock(side_effect=refresh),
    )


class SyncConnectionRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_routes_preserve_provider_job_and_limits(self):
        for provider in ("jellyfin", "emby", "plex"):
            with self.subTest(provider=provider):
                db = session(None, None, SimpleNamespace(), SimpleNamespace(id=7))
                tasks = BackgroundTasks()
                with patch.object(server_sync.settings_store, "get_effective_tmdb_key", AsyncMock(return_value="key")):
                    response = await getattr(sync, f"sync_{provider}")(tasks, 3, 5, db, SimpleNamespace(id=2))
                job = db.add.call_args.args[0]
                self.assertEqual((job.user_id, job.source, job.status), (2, CollectionSource(provider), SyncStatus.pending))
                self.assertEqual(response, {"status": "started", "job_id": 19,
                                          "message": f"{provider.capitalize()} sync is running in the background"})
                self.assertEqual(len(tasks.tasks), 1)
                self.assertIs(tasks.tasks[0].func, getattr(server_sync, f"run_{provider}_sync"))
                self.assertEqual(tasks.tasks[0].args, (2, 19, 3, 5))
                query = db.execute.call_args.args[0]
                self.assertIn(provider, query.compile().params.values())
                self.assertIn("ORDER BY media_server_connections.id ASC", str(query))
                db.commit.assert_awaited_once()
                db.refresh.assert_awaited_once_with(job)

    async def test_invalid_start_does_not_create_or_schedule_jobs(self):
        for provider in ("jellyfin", "emby", "plex"):
            for key, connection, detail in [(None, None, "TMDB API key required"),
                                            ("key", None, f"No {provider.capitalize()} connection configured")]:
                with self.subTest(provider=provider, detail=detail):
                    db = session(None, None, SimpleNamespace(), connection)
                    tasks = BackgroundTasks()
                    with patch.object(server_sync.settings_store, "get_effective_tmdb_key", AsyncMock(return_value=key)):
                        with self.assertRaises(HTTPException) as raised:
                            await getattr(sync, f"sync_{provider}")(tasks, 3, 5, db, SimpleNamespace(id=2))
                    self.assertEqual((raised.exception.status_code, raised.exception.detail), (400, detail))
                    db.add.assert_not_called()
                    db.commit.assert_not_awaited()
                    self.assertEqual(tasks.tasks, [])
                    self.assertEqual(db.execute.await_count, 3 if key is None else 4)

    async def test_normal_legacy_routes_use_the_account_cycle(self):
        for provider in ("jellyfin", "emby", "plex"):
            with patch.object(sync, "sync_account", AsyncMock(return_value={"job_id": 23})) as account:
                db, tasks, user = session(), BackgroundTasks(), SimpleNamespace(id=2)
                response = await getattr(sync, f"sync_{provider}")(tasks, 0, 0, db, user)
                self.assertEqual(response, {"job_id": 23})
                account.assert_awaited_once_with(tasks, db, user)

    async def test_library_selection_filters_supported_types_and_keeps_provider_storage(self):
        available = [{"Id": "movie", "Name": "Movies", "CollectionType": "movies"},
                     {"Id": "tv", "Name": "TV", "CollectionType": "tv"},
                     {"Id": "music", "Name": "Music", "CollectionType": "music"}]
        for provider, model in [("jellyfin", JellyfinLibrarySelection), ("emby", EmbyLibrarySelection)]:
            for selected in ([], [SimpleNamespace(library_id="tv")]):
                with self.subTest(provider=provider, selected=bool(selected)):
                    conn = SimpleNamespace(id=7, type=provider, url="url", token="token", server_user_id="remote")
                    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: selected))
                    db = SimpleNamespace(execute=AsyncMock(return_value=result))
                    with patch.object(sync, "_get_connection_or_404", AsyncMock(return_value=conn)), \
                         patch.object(getattr(sync, provider), "get_libraries", AsyncMock(return_value=available)) as fetch:
                        response = await sync.get_connection_libraries(7, db, SimpleNamespace(id=2))
                    fetch.assert_awaited_once_with("url", "token", "remote")
                    self.assertIs(db.execute.call_args.args[0].column_descriptions[0]["entity"], model)
                    self.assertEqual(response, {"all_selected": not selected, "libraries": [
                        {"id": "movie", "name": "Movies", "type": "movies", "selected": False},
                        {"id": "tv", "name": "TV", "type": "tv", "selected": bool(selected)},
                    ]})

    async def test_save_replaces_only_this_connections_selection_and_accepts_known_ids(self):
        for provider, model in [("jellyfin", JellyfinLibrarySelection), ("emby", EmbyLibrarySelection)]:
            with self.subTest(provider=provider):
                conn = SimpleNamespace(id=7, type=provider, url="url", token="token", server_user_id="remote")
                db = session()
                db.execute.side_effect = None
                with patch.object(sync, "_get_connection_or_404", AsyncMock(return_value=conn)), \
                     patch.object(getattr(sync, provider), "get_libraries", AsyncMock(return_value=[{"Id": "tv", "Name": "TV"}])):
                    response = await sync.save_connection_libraries(7, {"library_ids": ["tv", "unknown"]}, db, SimpleNamespace(id=2))
                self.assertEqual(response, {"saved": 2})
                deletion = db.execute.call_args.args[0]
                self.assertEqual(deletion.table.name, model.__tablename__)
                self.assertEqual(list(deletion.compile().params.values()), [7])
                row = db.add.call_args.args[0]
                self.assertIsInstance(row, model)
                self.assertEqual((row.user_id, row.connection_id, row.library_id, row.library_name), (2, 7, "tv", "TV"))
                db.add.assert_called_once()
                db.commit.assert_awaited_once()

    async def test_provider_failure_does_not_erase_saved_selection(self):
        for provider in ("jellyfin", "emby"):
            with self.subTest(provider=provider):
                conn = SimpleNamespace(id=7, type=provider, url="url", token="token", server_user_id="remote")
                db = session()
                with patch.object(sync, "_get_connection_or_404", AsyncMock(return_value=conn)), \
                     patch.object(getattr(sync, provider), "get_libraries", AsyncMock(side_effect=RuntimeError("offline"))):
                    with self.assertRaises(HTTPException) as raised:
                        await sync.save_connection_libraries(7, {"library_ids": []}, db, SimpleNamespace(id=2))
                self.assertEqual((raised.exception.status_code, raised.exception.detail), (502, "Could not reach server: offline"))
                db.execute.assert_not_awaited()
                db.commit.assert_not_awaited()
