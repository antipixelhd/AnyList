import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from sqlalchemy.sql import Select as _SASelect
from sqlalchemy.dialects import postgresql as _pg
from core import plex_watchlist
from models.connections import MediaServerConnection


class PlexWatchlistMappingTests(unittest.TestCase):
    def test_remote_keys_distinguish_movies_and_shows_and_ignore_unmapped_items(self):
        movie = _remote_item("movie", 42, "movie-key")
        show = _remote_item("show", 42, "show-key")
        mapped = plex_watchlist._plex_watchlist_remote_map([
            movie, show, _remote_item("movie", 42, "duplicate-key"),
            {"type": "movie", "Guid": [{"id": "tmdb://invalid"}]},
            {"type": "show", "Guid": [{"id": "imdb://tt123"}]},
            _remote_item("episode", 43),
        ])
        self.assertEqual(mapped, {"movie:42": movie, "show:42": show})


class PlexWatchlistRemoteChangesTests(unittest.IsolatedAsyncioTestCase):
    async def test_unresolved_add_is_reported_for_retry_without_writing_to_plex(self):
        plan = SimpleNamespace(push_add={"movie:42"}, push_remove=set())
        conn = SimpleNamespace(id=1, plex_account_token="test-token")
        with patch.object(plex_watchlist.plex, "resolve_tmdb_ratingkey", AsyncMock(return_value=None)), \
             patch.object(plex_watchlist.plex, "add_to_watchlist", AsyncMock()) as add, \
             self.assertLogs("uvicorn.error", level="WARNING"):
            failures = await plex_watchlist._apply_remote_watchlist_changes(
                conn, plan, {"movie:42": (7, "Title")}, {},
            )
        self.assertEqual(failures, ({"movie:42"}, set()))
        add.assert_not_awaited()

    async def test_known_removal_uses_fetched_key_and_failed_fallback_remains_pending(self):
        plan = SimpleNamespace(push_add=set(), push_remove={"movie:42", "show:43"})
        conn = SimpleNamespace(id=1, plex_account_token="test-token")
        remote = {"movie:42": {"ratingKey": "known-key"}, "show:43": {"title": "Show"}}
        with patch.object(plex_watchlist.plex, "resolve_tmdb_ratingkey", AsyncMock(side_effect=RuntimeError("offline"))) as resolve, \
             patch.object(plex_watchlist.plex, "remove_from_watchlist", AsyncMock(return_value=True)) as remove, \
             self.assertLogs("uvicorn.error", level="WARNING"):
            failures = await plex_watchlist._apply_remote_watchlist_changes(conn, plan, {}, remote)
        self.assertEqual(failures, (set(), {"show:43"}))
        remove.assert_awaited_once_with("test-token", "known-key")
        resolve.assert_awaited_once_with("test-token", 43, "show", "Show")


class _ReconcileFakeDB:
    """Serves only what reconcile_watchlist itself executes: the
    connection load (db.get, now that the function opens its own session),
    the baseline re-read (SELECT), and the baseline write (UPDATE).
    Everything heavier sits behind the patched load/apply helpers."""

    def __init__(self, baseline, conn):
        self._baseline = baseline
        self.conn = conn
        self.baseline_writes = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, model, obj_id):
        return self.conn

    async def execute(self, stmt):
        if isinstance(stmt, _SASelect):
            return SimpleNamespace(scalar_one_or_none=lambda: self._baseline)
        params = stmt.compile(dialect=_pg.dialect()).params
        self.baseline_writes.append(params.get("plex_watchlist_synced_keys"))
        return SimpleNamespace()


def _wl_conn(pull=True, push=True):
    return MediaServerConnection(
        id=1, user_id=1, type="plex", token="tok",
        plex_sync_watchlist=pull, plex_push_watchlist=push,
    )


def _remote_item(kind, tmdb_id, rating_key="rk", title="Title"):
    return {"type": kind, "Guid": [{"id": f"tmdb://{tmdb_id}"}], "ratingKey": rating_key, "title": title}


class PlexWatchlistReconcileTests(unittest.IsolatedAsyncioTestCase):
    """The routine wiring around core.watchlist_reconcile: what plan reaches
    the apply helpers, and what baseline gets persisted."""

    async def _run(self, db, remote_items, local_state, applied_local, remote_failures=(set(), set())):
        apply_local = AsyncMock(return_value=applied_local)
        apply_remote = AsyncMock(return_value=remote_failures)
        with patch.object(plex_watchlist, "async_sessionmaker", return_value=lambda *a, **k: db), \
             patch.object(plex_watchlist.plex, "get_watchlist", AsyncMock(return_value=remote_items)), \
             patch.object(plex_watchlist, "_load_local_watchlist_state", AsyncMock(return_value=local_state)), \
             patch.object(plex_watchlist, "_apply_local_watchlist_changes", apply_local), \
             patch.object(plex_watchlist, "_apply_remote_watchlist_changes", apply_remote):
            await plex_watchlist.reconcile_watchlist(1, db.conn.id, "tmdb-key")
        local_plan = apply_local.call_args.args[4] if apply_local.call_args else None
        remote_plan = apply_remote.call_args.args[1] if apply_remote.call_args else None
        return local_plan, remote_plan, apply_local, apply_remote

    async def test_remote_removal_is_applied_locally_not_resurrected(self):
        """Regression: Plex auto-removed a watched item; the old additive push
        re-added it. Now the baseline turns it into a local removal."""
        db = _ReconcileFakeDB(["movie:1"], _wl_conn())
        local_plan, remote_plan, _, _ = await self._run(
            db, [], (SimpleNamespace(id=5), {"movie:1": (11, "Inception")}), applied_local=set(),
        )
        self.assertEqual(local_plan.remove_local, {"movie:1"})
        self.assertEqual(remote_plan.push_add, frozenset())
        self.assertEqual(db.baseline_writes, [[]])
        db.commit.assert_awaited()

    async def test_deleted_managed_list_resets_baseline_instead_of_wiping_plex(self):
        """Regression: with the list deleted in the UI, a stale baseline must
        not turn the entire real Plex watchlist into push_remove calls."""
        db = _ReconcileFakeDB(["movie:1", "show:2"], _wl_conn())
        remote = [_remote_item("movie", 1), _remote_item("show", 2)]
        local_plan, remote_plan, _, _ = await self._run(
            db, remote, (None, {}), applied_local={"movie:1", "show:2"},
        )
        self.assertEqual(remote_plan.push_remove, frozenset())
        self.assertEqual(local_plan.add_local, {"movie:1", "show:2"})  # bootstrap re-import
        self.assertEqual(db.baseline_writes, [["movie:1", "show:2"]])

    async def test_bootstrap_first_contact_is_purely_additive(self):
        db = _ReconcileFakeDB(None, _wl_conn())
        local_plan, remote_plan, _, _ = await self._run(
            db, [_remote_item("show", 2)],
            (SimpleNamespace(id=5), {"movie:1": (11, "Inception")}),
            applied_local={"movie:1", "show:2"},
        )
        self.assertEqual(remote_plan.push_add, {"movie:1"})
        self.assertEqual(local_plan.add_local, {"show:2"})
        self.assertEqual(local_plan.remove_local, frozenset())
        self.assertEqual(db.baseline_writes, [["movie:1", "show:2"]])

    async def test_failed_remote_add_stays_out_of_the_baseline(self):
        db = _ReconcileFakeDB(["movie:1"], _wl_conn())
        _, remote_plan, _, _ = await self._run(
            db, [_remote_item("movie", 1)],
            (SimpleNamespace(id=5), {"movie:1": (11, "A"), "movie:9": (12, "B")}),
            applied_local={"movie:1", "movie:9"},
            remote_failures=({"movie:9"}, set()),
        )
        self.assertEqual(remote_plan.push_add, {"movie:9"})
        self.assertEqual(db.baseline_writes, [["movie:1"]])  # movie:9 retries next run

    async def test_suppressed_run_changes_nothing(self):
        baseline = [f"movie:{i}" for i in range(12)]
        db = _ReconcileFakeDB(baseline, _wl_conn())
        local = {key: (i, "T") for i, key in enumerate(baseline)}
        _, _, apply_local, apply_remote = await self._run(
            db, [], (SimpleNamespace(id=5), local), applied_local=set(),
        )
        apply_local.assert_not_called()
        apply_remote.assert_not_called()
        self.assertEqual(db.baseline_writes, [])
        db.commit.assert_not_awaited()

    async def test_fetch_failure_is_swallowed_and_rolled_back(self):
        db = _ReconcileFakeDB(["movie:1"], _wl_conn())
        with patch.object(plex_watchlist, "async_sessionmaker", return_value=lambda *a, **k: db), \
             patch.object(plex_watchlist.plex, "get_watchlist", AsyncMock(side_effect=RuntimeError("plex down"))):
            await plex_watchlist.reconcile_watchlist(1, db.conn.id, "tmdb-key")
        db.rollback.assert_awaited()
        self.assertEqual(db.baseline_writes, [])

    async def test_disabled_flags_do_nothing(self):
        db = _ReconcileFakeDB(["movie:1"], _wl_conn(pull=False, push=False))
        fetch = AsyncMock()
        with patch.object(plex_watchlist, "async_sessionmaker", return_value=lambda *a, **k: db), \
             patch.object(plex_watchlist.plex, "get_watchlist", fetch):
            await plex_watchlist.reconcile_watchlist(1, db.conn.id, "tmdb-key")
        fetch.assert_not_called()
