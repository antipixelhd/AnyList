import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from fastapi import HTTPException
from core import watch_delivery
from models.base import CollectionSource, MediaType


class _FakeSession:
    def __init__(self, results):
        self.execute = AsyncMock(side_effect=[SimpleNamespace(
            scalars=lambda item=item: SimpleNamespace(all=lambda: item),
            scalar_one_or_none=lambda item=item: item,
            all=lambda item=item: item,
        ) for item in results])
        self.commit = AsyncMock()


class PushWatchStateExcludeConnectionTests(unittest.IsolatedAsyncioTestCase):
    """Regression tests for #190: a two-way-sync connection (webhook in +
    push_watched out) can self-trigger an unbounded loop - Scrob pushes
    "unwatched" to Jellyfin, Jellyfin's own UserData change re-fires its
    webhook back into Scrob, which pushes "unwatched" again, forever. The
    fix is exclude_connection_id: the connection whose webhook triggered
    this push must be skipped, while other connections still get it."""

    def setUp(self):
        approval = patch('core.tracking_snapshot.require_stream_reconciliation', AsyncMock())
        approval.start()
        self.addCleanup(approval.stop)

    def _connections(self):
        conn_origin = SimpleNamespace(
            id=1, type="jellyfin", url="http://origin.local", token="tok1", server_user_id="u1",
        )
        conn_other = SimpleNamespace(
            id=2, type="jellyfin", url="http://other.local", token="tok2", server_user_id="u2",
        )
        return conn_origin, conn_other

    def _coll_files(self):
        # (CollectionFile, Collection.media_id) - push_watch_state selects
        # both columns, joining in the owning collection's media_id.
        cf1 = SimpleNamespace(source=CollectionSource.jellyfin, source_id="item-1", connection_id=1)
        cf2 = SimpleNamespace(source=CollectionSource.jellyfin, source_id="item-2", connection_id=2)
        return (cf1, 10), (cf2, 10)

    async def test_excludes_only_the_originating_connection(self) -> None:
        conn_origin, conn_other = self._connections()
        row1, row2 = self._coll_files()
        # Query order in push_watch_state (settings=None short-circuits every
        # later trakt/mdblist/simkl query, keeping this fixture minimal):
        # 1. connections, 2. settings, 3. collection files.
        db = _FakeSession([[conn_origin, conn_other], None, [row1, row2]])

        calls: list[str] = []

        async def fake_mark_unwatched(url, token, user_id, source_id):
            calls.append(url)
            return True

        with patch("core.watch_delivery.jellyfin_client.mark_unwatched", fake_mark_unwatched):
            await watch_delivery.push_watch_state(
                db, user_id=7, media_ids=[10], watched=False,
                exclude_connection_id=conn_origin.id,
            )

        self.assertNotIn("http://origin.local", calls)
        self.assertIn("http://other.local", calls)

    async def test_strict_delivery_surfaces_false_and_exception_results(self):
        origin, other = self._connections()
        for failure in (False, RuntimeError("offline")):
            with self.subTest(failure=type(failure).__name__):
                db = _FakeSession([[origin, other], None, list(self._coll_files())])
                push = AsyncMock(side_effect=[failure, True])
                with patch("core.watch_delivery.jellyfin_client.mark_unwatched", push):
                    with self.assertRaises(RuntimeError):
                        await watch_delivery.push_watch_state(db, 7, [10], watched=False, require_success=True)
                self.assertEqual(push.await_count, 2)
                self.assertEqual([(call.args[0], call.args[-1]) for call in push.await_args_list],
                                 [(origin.url, "item-1"), (other.url, "item-2")])

    async def test_no_exclusion_pushes_to_every_connection(self) -> None:
        # Baseline: without exclude_connection_id (e.g. a manual UI mark, not
        # webhook-triggered), behavior is unchanged - every push_watched
        # connection still gets it, including what would be conn_origin above.
        conn_origin, conn_other = self._connections()
        row1, row2 = self._coll_files()
        db = _FakeSession([[conn_origin, conn_other], None, [row1, row2]])

        calls: list[str] = []

        async def fake_mark_unwatched(url, token, user_id, source_id):
            calls.append(url)
            return True

        with patch("core.watch_delivery.jellyfin_client.mark_unwatched", fake_mark_unwatched):
            await watch_delivery.push_watch_state(db, user_id=7, media_ids=[10], watched=False)

        self.assertIn("http://origin.local", calls)
        self.assertIn("http://other.local", calls)

    async def test_unreviewed_connection_never_receives_watch_correction(self) -> None:
        conn_origin, _ = self._connections()
        db = _FakeSession([[conn_origin], None])
        with patch('core.tracking_snapshot.require_stream_reconciliation',
                   AsyncMock(side_effect=HTTPException(409, 'Review first'))), \
             patch('core.watch_delivery.jellyfin_client.mark_unwatched', AsyncMock()) as push:
            await watch_delivery.push_watch_state(db, user_id=7, media_ids=[10], watched=False)
        push.assert_not_awaited()


class PushWatchStateEchoSuppressionTests(unittest.IsolatedAsyncioTestCase):
    """#324: a manual mark-watched with a backdated date got duplicated - the
    Jellyfin/Emby push echoes straight back via UserDataSaved, and the
    backdated watched_at slips past _write_watch_event's recent-event guard.
    Fix: register the push with mark_pushed_watched so the echo is caught."""

    def setUp(self):
        approval = patch('core.tracking_snapshot.require_stream_reconciliation', AsyncMock())
        approval.start()
        self.addCleanup(approval.stop)

    def _fixture(self, conn_type):
        conn = SimpleNamespace(id=1, type=conn_type, url="http://srv.local",
                               token="t", server_user_id="u1")
        cf = SimpleNamespace(source=CollectionSource[conn_type], source_id="item-1", connection_id=1)
        # query order: 1. connections, 2. settings, 3. collection files
        return _FakeSession([[conn], None, [(cf, 42)]])

    async def test_jellyfin_watched_push_registers_for_echo_suppression(self):
        registered: list[tuple[int, int]] = []

        async def fake_mark_watched(url, token, user_id, source_id, *, played_at=None):
            self.assertEqual(played_at, datetime(2020, 1, 1))
            return True

        with patch("core.watch_delivery.jellyfin_client.mark_watched", fake_mark_watched), \
             patch("core.watch_echo.mark_pushed_watched",
                   side_effect=lambda uid, mid: registered.append((uid, mid))):
            await watch_delivery.push_watch_state(
                self._fixture("jellyfin"), user_id=7, media_ids=[42], watched=True,
                watched_at_by_media={42: datetime(2020, 1, 1)},
            )

        self.assertEqual(registered, [(7, 42)])

    async def test_emby_watched_push_registers_for_echo_suppression(self):
        registered: list[tuple[int, int]] = []

        async def fake_mark_watched(url, token, user_id, source_id, *, played_at=None):
            self.assertEqual(played_at, datetime(2020, 1, 1))
            return True

        with patch("core.watch_delivery.emby_client.mark_watched", fake_mark_watched), \
             patch("core.watch_echo.mark_pushed_watched",
                   side_effect=lambda uid, mid: registered.append((uid, mid))):
            await watch_delivery.push_watch_state(
                self._fixture("emby"), user_id=7, media_ids=[42], watched=True,
                watched_at_by_media={42: datetime(2020, 1, 1)},
            )

        self.assertEqual(registered, [(7, 42)])

    async def test_unwatch_push_does_not_register(self):
        async def fake_mark_unwatched(url, token, user_id, source_id):
            return True

        with patch("core.watch_delivery.jellyfin_client.mark_unwatched", fake_mark_unwatched), \
             patch("core.watch_echo.mark_pushed_watched") as reg:
            await watch_delivery.push_watch_state(
                self._fixture("jellyfin"), user_id=7, media_ids=[42], watched=False,
            )

        reg.assert_not_called()


class PushWatchStateTraktTokenTests(unittest.IsolatedAsyncioTestCase):
    """#326: the Trakt history fan-out on a manual mark-watched must go through
    ensure_valid_trakt_token, not use the stored token blindly."""

    def setUp(self):
        approval = patch('core.cloud_reconciliation.cloud_push_is_approved', AsyncMock(return_value=True))
        approval.start()
        self.addCleanup(approval.stop)

    def _settings(self, **overrides):
        base = dict(
            trakt_push_watched=True,
            trakt_access_token="tok",
            trakt_client_id="cid",
            trakt_client_secret=None,
            trakt_refresh_token=None,
            trakt_token_expires_at=9_999_999_999,
            trakt_push_collection=False,
            trakt_push_ratings=False,
            simkl_push_watched=False,
            simkl_access_token=None,
            simkl_client_id=None,
            mdblist_push_watched=False,
            mdblist_api_key=None,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def _movie(self):
        return SimpleNamespace(
            id=5, tmdb_id=550, media_type=MediaType.movie,
            show_id=None, season_number=None, episode_number=None, tmdb_data=None,
        )

    async def test_valid_token_is_passed_to_the_trakt_history_push(self):
        db = _FakeSession([[], self._settings(), [self._movie()]])
        add_movie = AsyncMock()
        with patch("core.watch_delivery.trakt_client.add_movie_to_history", add_movie):
            await watch_delivery.push_watch_state(
                db, user_id=1, media_ids=[5], watched=True,
                watched_at_by_media={5: datetime(2024, 1, 1)},
            )
        add_movie.assert_awaited_once()
        self.assertEqual(add_movie.await_args.args[1], "tok")

    async def test_unrefreshable_token_skips_the_trakt_push(self):
        db = _FakeSession([[], self._settings(trakt_token_expires_at=1), [self._movie()]])
        add_movie = AsyncMock()
        with patch("core.watch_delivery.trakt_client.add_movie_to_history", add_movie), \
             patch("core.trakt_auth.trakt_client.validate_token", AsyncMock(return_value=False)):
            await watch_delivery.push_watch_state(
                db, user_id=1, media_ids=[5], watched=True,
                watched_at_by_media={5: datetime(2024, 1, 1)},
            )
        add_movie.assert_not_awaited()

    async def test_unrefreshable_token_leaves_strict_delivery_pending(self):
        db = _FakeSession([[], self._settings(trakt_token_expires_at=1)])
        with patch("core.trakt_auth.trakt_client.validate_token", AsyncMock(return_value=False)):
            with self.assertRaises(RuntimeError):
                await watch_delivery.push_watch_state(db, 1, [5], watched=False, require_success=True)

    async def test_scoped_cloud_delivery_does_not_write_other_cloud_accounts(self):
        from core.sync_delivery_targets import destination_scope
        db = _FakeSession([[], self._settings(mdblist_push_watched=True, mdblist_api_key="fixture"), [self._movie()]])
        with destination_scope("trakt"), \
             patch("core.watch_delivery.trakt_client.remove_movie_from_history", AsyncMock()) as remove, \
             patch("core.mdblist.remove_watched", AsyncMock()) as other:
            await watch_delivery.push_watch_state(db, 1, [5], watched=False, require_success=True)
        remove.assert_awaited_once()
        other.assert_not_awaited()


class StreamingWatchDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_intent_is_committed_before_dispatch_and_keeps_exclusions(self):
        for provider in ("nuvio", "stremio"):
            with self.subTest(provider=provider):
                db = _FakeSession([[SimpleNamespace(id=3, type=provider)], None, []])
                order = []
                queue = AsyncMock(side_effect=lambda *a, **kw: order.append("queue"))
                db.commit.side_effect = lambda: order.append("commit")
                dispatch = AsyncMock(side_effect=lambda *a, **kw: order.append("dispatch"))
                with (
                    patch("core.watch_intents.queue_watch_intents", queue),
                    patch("core.watch_intents.dispatch_watch_intents", dispatch),
                    patch("core.tracking_snapshot.require_stream_reconciliation", AsyncMock()),
                ):
                    await watch_delivery.push_watch_state(
                        db, 7, [42], watched=False,
                        exclude_connection_id=1, exclude_connection_ids={2},
                    )
                self.assertEqual(order, ["queue", "commit", "dispatch"])
                queue.assert_awaited_once_with(db, 7, [42], exclude_connection_ids={1, 2})
                dispatch.assert_awaited_once_with(db, 7)

    async def test_previously_dispatched_streaming_intents_are_not_repeated(self):
        db = _FakeSession([[SimpleNamespace(id=3, type="nuvio")], None, []])
        with (
            patch("core.watch_intents.queue_watch_intents", AsyncMock()) as queue,
            patch("core.watch_intents.dispatch_watch_intents", AsyncMock()) as dispatch,
            patch("core.tracking_snapshot.require_stream_reconciliation", AsyncMock()),
        ):
            await watch_delivery.push_watch_state(db, 7, [42], watched=False,
                                                  skip_stream_watch_writes=True)
        queue.assert_not_awaited()
        dispatch.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_excluded_streaming_connections_do_not_queue_delivery(self):
        db = _FakeSession([[SimpleNamespace(id=3, type="stremio")], None])
        with patch("core.watch_intents.queue_watch_intents", AsyncMock()) as queue:
            await watch_delivery.push_watch_state(db, 7, [42], watched=False,
                                                  exclude_connection_id=3)
        queue.assert_not_awaited()
        db.commit.assert_not_awaited()
