"""Identity, uniqueness, and reconnect behavior for stream connections."""

import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm.exc import StaleDataError

from core import nuvio, stremio
from core.connection_identity import (
    assert_connection_identity_available,
    canonical_nuvio_url,
    reset_stream_connection_state,
)
from models.collection import CollectionFile
from models.connections import MediaServerConnection
from models.events import WatchEvent
from models.base import MediaType
from models.media import Media
from models.streaming_library import StreamingLibraryDelivery
from models.sync import SyncJob, SyncStatus
from models.tracking import StreamAction, StreamBaseline, SyncReview
from models.users import User, UserSettings
from models.watch_intent import WatchIntent
from routers import auth
from schemas import MediaServerConnectionUpdate


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kw):
    return "JSON"


class MultipleStreamConnectionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(self.engine.dispose)
        @event.listens_for(self.engine.sync_engine, "connect")
        def _sqlite_date_trunc(dbapi_connection, _connection_record):
            dbapi_connection.create_function("date_trunc", 2, lambda _precision, value: value)
        self.tables = [
            User.__table__,
            UserSettings.__table__,
            MediaServerConnection.__table__,
            Media.__table__,
            StreamBaseline.__table__,
            SyncReview.__table__,
            WatchIntent.__table__,
            StreamAction.__table__,
            StreamingLibraryDelivery.__table__,
            CollectionFile.__table__,
            SyncJob.__table__,
            WatchEvent.__table__,
        ]
        async with self.engine.begin() as conn:
            for table in self.tables:
                await conn.run_sync(table.create)
        self.db = AsyncSession(self.engine, expire_on_commit=False)
        self.addAsyncCleanup(self.db.close)
        now = datetime(2026, 9, 1)
        self.owner = User(email="owner@example.test", username="owner", api_key="owner-key", created_at=now, updated_at=now)
        self.other = User(email="other@example.test", username="other", api_key="other-key", created_at=now, updated_at=now)
        self.db.add_all([self.owner, self.other])
        await self.db.commit()

    async def _connection(
        self,
        *,
        user_id=None,
        provider="stremio",
        account_id="account-a",
        url="https://api.nuvio.tv",
        profile_id="1",
        token="refresh-token",
    ):
        conn = MediaServerConnection(
            user_id=user_id or self.owner.id,
            type=provider,
            name=f"{provider} {account_id} profile {profile_id}",
            url=url,
            token=token,
            provider_account_id=account_id,
            server_user_id=str(profile_id),
            server_username="Fixture",
            created_at=datetime(2026, 9, 1),
        )
        self.db.add(conn)
        await self.db.flush()
        return conn

    async def _count(self, model):
        return await self.db.scalar(select(func.count()).select_from(model))

    async def test_stremio_identity_is_unique_per_user_account(self):
        await self._connection(provider="stremio", account_id="stremio-account-a")

        with self.assertRaises(HTTPException) as duplicate:
            await assert_connection_identity_available(
                self.db, self.owner.id, "stremio", "stremio-account-a", stremio.DEFAULT_URL, "stremio-account-a"
            )
        self.assertEqual(duplicate.exception.status_code, 409)

        # A second remote account for the owner and the same remote account for
        # another AnyList user remain separate valid destinations.
        await assert_connection_identity_available(
            self.db, self.owner.id, "stremio", "stremio-account-b", stremio.DEFAULT_URL, "stremio-account-b"
        )
        await assert_connection_identity_available(
            self.db, self.other.id, "stremio", "stremio-account-a", stremio.DEFAULT_URL, "stremio-account-a"
        )

    async def test_nuvio_identity_uses_account_profile_and_canonical_endpoint(self):
        await self._connection(
            provider="nuvio", account_id="nuvio-account-a", url="https://nuvio.tv", profile_id="2"
        )

        self.assertEqual(canonical_nuvio_url(" https://nuvio.tv:443/// "), "https://nuvio.tv")
        with self.assertRaises(HTTPException) as duplicate:
            await assert_connection_identity_available(
                self.db, self.owner.id, "nuvio", "nuvio-account-a", "https://nuvio.tv/", "2"
            )
        self.assertEqual(duplicate.exception.status_code, 409)

        # Profiles, remote accounts, endpoints, and AnyList owners are each
        # independent parts of the Nuvio connection identity.
        for owner_id, account, url, profile in (
            (self.owner.id, "nuvio-account-a", "https://nuvio.tv", "3"),
            (self.owner.id, "nuvio-account-b", "https://nuvio.tv", "2"),
            (self.owner.id, "nuvio-account-a", "https://other-nuvio.tv", "2"),
            (self.other.id, "nuvio-account-a", "https://nuvio.tv", "2"),
        ):
            await assert_connection_identity_available(
                self.db, owner_id, "nuvio", account, url, profile
            )

    async def test_account_switch_clears_connection_state_but_keeps_user_watch_history(self):
        conn = await self._connection(provider="stremio", account_id="old-account")
        peer = await self._connection(provider="stremio", account_id="peer-account")
        await self.db.flush()
        self.db.add_all([
            StreamBaseline(connection_id=conn.id, user_id=self.owner.id, snapshot={"watched": [10]}, approved=True, observed_at=datetime(2026, 9, 1)),
            StreamBaseline(connection_id=peer.id, user_id=self.owner.id, snapshot={"watched": [20]}, approved=True, observed_at=datetime(2026, 9, 1)),
            SyncReview(user_id=self.owner.id, connection_id=conn.id, provider="stremio", kind="watched", message="review", payload={}, created_at=datetime(2026, 9, 1)),
            WatchIntent(user_id=self.owner.id, connection_id=conn.id, media_id=10, desired_watched=True, created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1)),
            StreamAction(user_id=self.owner.id, connection_id=conn.id, media_id=10, action="watched", payload={}, created_at=datetime(2026, 9, 1)),
            StreamingLibraryDelivery(intent_id=1, connection_id=conn.id, desired=True, updated_at=datetime(2026, 9, 1)),
            CollectionFile(collection_id=1, connection_id=conn.id, source="stremio", source_id="tt10", added_at=datetime(2026, 9, 1)),
            SyncJob(user_id=self.owner.id, source="stremio", status=SyncStatus.pending, connection_id=conn.id, created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1)),
            WatchEvent(user_id=self.owner.id, media_id=10, watched_at=datetime(2025, 1, 1), completed=True, created_at=datetime(2026, 9, 1)),
        ])
        await self.db.commit()

        await reset_stream_connection_state(self.db, conn)
        await self.db.commit()

        self.assertEqual(conn.identity_version, 1)
        self.assertFalse(conn.stremio_full_sync_done)
        self.assertIsNone(conn.stremio_pull_cursor_at)
        remaining_baseline = (await self.db.execute(select(StreamBaseline))).scalar_one()
        self.assertEqual(remaining_baseline.connection_id, peer.id)
        self.assertEqual(await self._count(SyncReview), 0)
        self.assertEqual(await self._count(WatchIntent), 0)
        self.assertEqual(await self._count(StreamAction), 0)
        self.assertEqual(await self._count(StreamingLibraryDelivery), 0)
        self.assertEqual(await self._count(CollectionFile), 0)
        self.assertEqual(await self._count(WatchEvent), 1)
        job = (await self.db.execute(select(SyncJob))).scalar_one()
        self.assertEqual(job.status, SyncStatus.cancelled)

    async def test_account_switch_is_rejected_while_connection_sync_is_running(self):
        conn = await self._connection(provider="stremio", account_id="old-account")
        self.db.add_all([
            StreamBaseline(
                connection_id=conn.id, user_id=self.owner.id, snapshot={"keep": True},
                approved=True, observed_at=datetime(2026, 9, 1),
            ),
            SyncJob(
                user_id=self.owner.id, source="stremio", status=SyncStatus.running,
                connection_id=conn.id, created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1),
            ),
        ])
        await self.db.commit()

        with self.assertRaises(HTTPException) as active:
            await reset_stream_connection_state(self.db, conn)
        self.assertEqual(active.exception.status_code, 409)
        self.assertEqual(conn.identity_version, 0)
        self.assertEqual(await self._count(StreamBaseline), 1)

    async def test_same_account_reauthentication_preserves_baseline(self):
        conn = await self._connection(provider="stremio", account_id="account-a", url=stremio.DEFAULT_URL)
        self.db.add(StreamBaseline(connection_id=conn.id, user_id=self.owner.id, snapshot={"watched": [10]}, approved=True, observed_at=datetime(2026, 9, 1)))
        await self.db.commit()

        with (
            patch.object(stremio, "validate_auth_key", AsyncMock(return_value={"_id": "account-a", "email": "owner@example.test"})),
            patch("core.stream_actions.cleanup_disabled_stream_actions", AsyncMock()),
        ):
            await auth._update_connection_fields(
                conn,
                MediaServerConnectionUpdate(token="rotated-auth-key"),
                self.db,
                self.owner,
            )

        self.assertEqual(conn.token, "rotated-auth-key")
        self.assertEqual(conn.identity_version, 0)
        self.assertEqual(await self._count(StreamBaseline), 1)

    async def test_same_nuvio_account_and_profile_reauthentication_preserves_baseline(self):
        conn = await self._connection(provider="nuvio", account_id="nuvio-account-a", profile_id="2")
        self.db.add(StreamBaseline(
            connection_id=conn.id, user_id=self.owner.id, snapshot={"watched": [10]},
            approved=True, observed_at=datetime(2026, 9, 1),
        ))
        await self.db.commit()
        session = nuvio.NuvioSession("access", "rotated-token", 3600, account_id="nuvio-account-a")

        async def _validate(url, token, profile, *, on_refresh=None):
            if on_refresh:
                await on_refresh(session)
            return session, [{"profile_index": 2, "name": "Second"}]

        with (
            patch.object(nuvio, "validate_connection", AsyncMock(side_effect=_validate)),
            patch("core.stream_actions.cleanup_disabled_stream_actions", AsyncMock()),
        ):
            updated = await auth._update_connection_fields(
                conn, MediaServerConnectionUpdate(token="replacement-session"), self.db, self.owner,
            )

        self.assertEqual(updated.server_user_id, "2")
        self.assertEqual(updated.server_username, "Second")
        self.assertEqual(updated.identity_version, 0)
        self.assertEqual(await self._count(StreamBaseline), 1)

    async def test_authenticated_create_allows_distinct_stremio_accounts_but_rejects_duplicate(self):
        body = lambda token: auth.schemas.MediaServerConnectionCreate(
            type="stremio", name="Stremio", url="ignored", token=token,
        )
        with patch.object(stremio, "validate_auth_key", AsyncMock(side_effect=[
            {"_id": "remote-a", "email": "a@example.test"},
            {"_id": "remote-b", "email": "b@example.test"},
            {"_id": "remote-a", "email": "a@example.test"},
            {"_id": "remote-a", "email": "a@example.test"},
        ])) as validate:
            first = await auth.create_connection(body("token-a"), db=self.db, current_user=self.owner)
            second = await auth.create_connection(body("token-b"), db=self.db, current_user=self.owner)
            self.assertNotEqual(first.id, second.id)
            self.assertEqual(first.provider_account_id, "remote-a")
            self.assertEqual(second.provider_account_id, "remote-b")

            # Account uniqueness is scoped to the AnyList user.
            other = await auth.create_connection(body("token-other-user"), db=self.db, current_user=self.other)
            self.assertEqual(other.provider_account_id, "remote-a")

            with self.assertRaises(HTTPException) as duplicate:
                await auth.create_connection(body("token-duplicate"), db=self.db, current_user=self.owner)
            self.assertEqual(duplicate.exception.status_code, 409)
            self.assertEqual(await self._count(MediaServerConnection), 3)
        self.assertEqual(validate.await_count, 4)

    async def test_stremio_reconnect_rejects_an_account_already_attached_to_another_row(self):
        await self._connection(provider="stremio", account_id="remote-a")
        second = await self._connection(provider="stremio", account_id="remote-b")
        self.db.add(StreamBaseline(
            connection_id=second.id, user_id=self.owner.id, snapshot={"keep": True},
            approved=True, observed_at=datetime(2026, 9, 1),
        ))
        await self.db.commit()

        with patch.object(stremio, "validate_auth_key", AsyncMock(return_value={"_id": "remote-a"})):
            with self.assertRaises(HTTPException) as duplicate:
                await auth._update_connection_fields(
                    second, MediaServerConnectionUpdate(token="new-token"), self.db, self.owner,
                )

        self.assertEqual(duplicate.exception.status_code, 409)
        self.assertEqual(second.provider_account_id, "remote-b")
        self.assertEqual(second.token, "refresh-token")
        self.assertEqual(await self._count(StreamBaseline), 1)

    async def test_nuvio_profile_switch_resets_baseline_and_pending_jobs(self):
        conn = await self._connection(provider="nuvio", account_id="nuvio-account-a", profile_id="1")
        self.db.add(StreamBaseline(
            connection_id=conn.id, user_id=self.owner.id, snapshot={"profile": 1},
            approved=True, observed_at=datetime(2026, 9, 1),
        ))
        self.db.add(SyncJob(
            user_id=self.owner.id, source="nuvio", status=SyncStatus.pending,
            connection_id=conn.id, created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1),
        ))
        await self.db.commit()
        session = nuvio.NuvioSession("access", "rotated-token", 3600, account_id="nuvio-account-a")

        async def _validate(url, token, profile, *, on_refresh=None):
            if on_refresh:
                await on_refresh(session)
            return session, [{"profile_index": 2, "name": "Second"}]

        with (
            patch.object(nuvio, "validate_connection", AsyncMock(side_effect=_validate)),
            patch("core.connection_identity.persist_rotated_session", AsyncMock(side_effect=lambda _db, target, rotated: setattr(target, "token", rotated.refresh_token))),
            patch("core.stream_actions.cleanup_disabled_stream_actions", AsyncMock()),
        ):
            updated = await auth._update_connection_fields(
                conn,
                MediaServerConnectionUpdate(server_user_id="2"),
                self.db,
                self.owner,
            )

        self.assertEqual(updated.server_user_id, "2")
        self.assertEqual(updated.provider_account_id, "nuvio-account-a")
        self.assertEqual(updated.identity_version, 1)
        self.assertEqual(await self._count(StreamBaseline), 0)
        pending = (await self.db.execute(select(SyncJob))).scalar_one()
        self.assertEqual(pending.status, SyncStatus.cancelled)

    async def test_stale_provider_result_is_rejected_after_identity_version_changes(self):
        from core.connection_identity import refresh_stream_connection

        conn = await self._connection(provider="stremio", account_id="remote-a")
        conn.identity_version = 2

        async def _refresh(_conn):
            _conn.identity_version = 3

        self.db.refresh = AsyncMock(side_effect=_refresh)
        with self.assertRaises(HTTPException) as stale:
            await refresh_stream_connection(self.db, conn)
        self.assertEqual(stale.exception.status_code, 409)

    async def test_sqlite_unique_index_rejects_duplicate_race_at_storage_boundary(self):
        await self._connection(provider="stremio", account_id="same-account")
        duplicate = MediaServerConnection(
            user_id=self.owner.id, type="stremio", name="duplicate", url=stremio.DEFAULT_URL,
            token="other-token", provider_account_id="same-account", server_user_id="same-account",
            created_at=datetime(2026, 9, 1),
        )
        self.db.add(duplicate)
        with self.assertRaises(IntegrityError):
            await self.db.flush()
        await self.db.rollback()

    async def test_nuvio_session_exposes_authenticated_account_identity(self):
        parsed = nuvio._parse_session({
            "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
            "user": {"id": "remote-nuvio-user"},
        })
        missing = nuvio._parse_session({"access_token": "access", "refresh_token": "refresh"})
        self.assertEqual(parsed.account_id, "remote-nuvio-user")
        self.assertIsNone(missing.account_id)

    async def test_nuvio_connections_require_independent_rotating_sessions(self):
        async def create(token, profile, owner):
            return await auth.create_connection(
                auth.schemas.MediaServerConnectionCreate(
                    type="nuvio", name=f"Profile {profile}", url="https://nuvio.test/", token=token,
                    server_user_id=str(profile),
                ),
                db=self.db,
                current_user=owner,
            )

        session1 = nuvio.NuvioSession("access-1", "rotated-1", 3600, account_id="shared-remote")
        session2 = nuvio.NuvioSession("access-2", "rotated-2", 3600, account_id="shared-remote")
        with (
            patch("routers.auth.validate_service_url", AsyncMock(return_value="https://nuvio.test")),
            patch.object(nuvio, "validate_connection", AsyncMock(side_effect=[
                (session1, [{"id": 1, "name": "One"}]),
                (session2, [{"id": 2, "name": "Two"}]),
            ])) as validate,
        ):
            first = await create("login-session-1", 1, self.owner)
            second = await create("login-session-2", 2, self.owner)
            self.assertEqual(first.provider_account_id, "shared-remote")
            self.assertEqual(second.provider_account_id, "shared-remote")
            self.assertEqual(second.url, "https://nuvio.test")

            # Reusing a rotating refresh token would invalidate the first
            # connection; reject it before contacting Nuvio.
            with self.assertRaises(HTTPException) as reused:
                await create("rotated-1", 3, self.other)
            self.assertEqual(reused.exception.status_code, 409)
            self.assertEqual(validate.await_count, 2)

    async def test_nuvio_connection_without_verified_account_identity_is_rejected(self):
        body = auth.schemas.MediaServerConnectionCreate(
            type="nuvio", name="Nuvio", url="https://nuvio.test", token="refresh-token",
            server_user_id="1",
        )
        with (
            patch("routers.auth.validate_service_url", AsyncMock(return_value="https://nuvio.test")),
            patch.object(nuvio, "validate_connection", AsyncMock(return_value=(
                nuvio.NuvioSession("access", "rotated", 3600), [{"profile_index": 1, "name": "One"}]
            ))) as validate,
        ):
            with self.assertRaises(HTTPException) as invalid_identity:
                await auth.create_connection(body, db=self.db, current_user=self.owner)
        self.assertEqual(invalid_identity.exception.status_code, 409)
        self.assertEqual(await self._count(MediaServerConnection), 0)
        validate.assert_awaited_once()

    async def test_legacy_nuvio_identity_is_verified_before_a_duplicate_is_allowed(self):
        legacy = await self._connection(
            provider="nuvio", account_id=None, url="https://nuvio.test/", profile_id="1", token="legacy-refresh",
        )
        session = nuvio.NuvioSession("access", "rotated-legacy", 3600, account_id="remote-nuvio")

        async def _validate(url, token, profile_id, *, on_refresh=None):
            if on_refresh:
                await on_refresh(session)
            return session, [{"profile_index": 1, "name": "One"}]

        with (
            patch.object(nuvio, "validate_connection", AsyncMock(side_effect=_validate)) as validate,
            patch("core.connection_identity.persist_rotated_session", AsyncMock(side_effect=lambda _db, row, rotated: setattr(row, "token", rotated.refresh_token))) as persist,
        ):
            with self.assertRaises(HTTPException) as duplicate:
                await assert_connection_identity_available(
                    self.db, self.owner.id, "nuvio", "remote-nuvio", "https://nuvio.test", "1",
                )

        self.assertEqual(duplicate.exception.status_code, 409)
        self.assertEqual(legacy.provider_account_id, "remote-nuvio")
        self.assertEqual(legacy.url, "https://nuvio.test")
        self.assertEqual(legacy.token, "rotated-legacy")
        validate.assert_awaited_once()
        persist.assert_awaited_once()

    async def test_unreachable_legacy_nuvio_row_is_not_treated_as_a_free_identity(self):
        legacy = await self._connection(
            provider="nuvio", account_id=None, url="https://nuvio.test", profile_id="1", token="legacy-refresh",
        )
        with patch.object(nuvio, "validate_connection", AsyncMock(side_effect=nuvio.NuvioAPIError("unavailable"))):
            with self.assertRaises(HTTPException) as unavailable:
                await assert_connection_identity_available(
                    self.db, self.owner.id, "nuvio", "remote-nuvio", "https://nuvio.test", "1",
                )
        self.assertEqual(unavailable.exception.status_code, 503)
        self.assertIsNone(legacy.provider_account_id)

    async def test_delete_then_readd_stremio_account_starts_without_old_baseline(self):
        conn = await self._connection(provider="stremio", account_id="remote-a", token="shared-auth-key")
        other_user_conn = await self._connection(
            user_id=self.other.id, provider="stremio", account_id="remote-a", token="shared-auth-key"
        )
        self.db.add(StreamBaseline(
            connection_id=conn.id, user_id=self.owner.id, snapshot={"old": True},
            approved=True, observed_at=datetime(2026, 9, 1),
        ))
        await self.db.commit()
        with (
            patch.object(stremio, "logout", AsyncMock()) as logout,
            patch.object(stremio, "validate_auth_key", AsyncMock(return_value={"_id": "remote-a"})),
        ):
            await auth.delete_connection(conn.id, db=self.db, current_user=self.owner)
            logout.assert_not_awaited()
            self.assertEqual(await self._count(MediaServerConnection), 1)
            self.assertEqual((await self.db.get(MediaServerConnection, other_user_conn.id)).user_id, self.other.id)
            self.assertEqual(await self._count(StreamBaseline), 0)
            recreated = await auth.create_connection(
                auth.schemas.MediaServerConnectionCreate(
                    type="stremio", name="Re-added", url="unused", token="fresh-token"
                ),
                db=self.db,
                current_user=self.owner,
            )
        self.assertEqual(recreated.provider_account_id, "remote-a")
        self.assertEqual(await self._count(StreamBaseline), 0)

    async def test_watch_fanout_delivers_to_each_stremio_account(self):
        from routers.sync import _fan_out_changes_to_other_connections

        first = MediaServerConnection(
            id=101, user_id=self.owner.id, type="stremio", name="First", url=stremio.DEFAULT_URL,
            token="token-1", provider_account_id="remote-1", server_user_id="remote-1",
            push_watched=True, created_at=datetime(2026, 9, 1),
        )
        second = MediaServerConnection(
            id=102, user_id=self.owner.id, type="stremio", name="Second", url=stremio.DEFAULT_URL,
            token="token-2", provider_account_id="remote-2", server_user_id="remote-2",
            push_watched=True, created_at=datetime(2026, 9, 1),
        )
        movie = Media(id=55, tmdb_id=603, media_type=MediaType.movie, title="The Matrix")

        class _Rows:
            def scalars(self):
                return self

            def all(self):
                return [first, second]

        class _EmptyRows:
            def all(self):
                return []

        db = type("FakeDB", (), {
            "execute": AsyncMock(side_effect=[_Rows(), _EmptyRows()]),
            "commit": AsyncMock(),
        })()
        with (
            patch("routers.sync._select_in_chunks", AsyncMock(return_value=[movie])),
            patch("routers.sync._get_effective_tmdb_key", AsyncMock(return_value="tmdb-key")),
            patch("routers.sync._push_stremio_connection", AsyncMock()) as push,
            patch("core.pull_cycle.defer_fan_out", return_value=False),
            patch("core.tracking_snapshot.require_stream_reconciliation", AsyncMock()),
        ):
            await _fan_out_changes_to_other_connections(
                db, self.owner.id, None, {movie.id}, {}, settings=None,
            )

        self.assertEqual(push.await_count, 2)
        self.assertEqual({call.args[1].id for call in push.await_args_list}, {101, 102})

    async def test_nuvio_watched_and_library_fanout_targets_each_profile(self):
        from routers.sync import _fan_out_changes_to_other_connections

        first = type("NuvioConnection", (), {
            "id": 201, "user_id": self.owner.id, "type": "nuvio", "url": "https://nuvio.test",
            "token": "refresh-1", "server_user_id": "1", "provider_account_id": "remote-a",
            "identity_version": 0, "push_collection": True, "push_watched": True,
            "push_ratings": False, "push_playback": False,
        })()
        second = type("NuvioConnection", (), {
            "id": 202, "user_id": self.owner.id, "type": "nuvio", "url": "https://nuvio.test",
            "token": "refresh-2", "server_user_id": "2", "provider_account_id": "remote-a",
            "identity_version": 0, "push_collection": True, "push_watched": True,
            "push_ratings": False, "push_playback": False,
        })()
        movie = Media(
            id=56, tmdb_id=603, media_type=MediaType.movie, title="The Matrix",
            tmdb_data={"external_ids": {"imdb_id": "tt0133093"}},
        )

        class _ConnectionRows:
            def scalars(self):
                return self

            def all(self):
                return [first, second]

        class _EmptyRows:
            def all(self):
                return []

        db = type("FakeDB", (), {
            "execute": AsyncMock(side_effect=[_ConnectionRows(), _EmptyRows()]),
            "commit": AsyncMock(),
            "refresh": AsyncMock(),
        })()
        library_push = AsyncMock(return_value=True)
        watched_push = AsyncMock()
        with (
            patch("routers.sync._select_in_chunks", AsyncMock(return_value=[movie])),
            patch("routers.sync._get_effective_tmdb_key", AsyncMock(return_value="tmdb-key")),
            patch("routers.sync._ensure_nuvio_imdb_ids", AsyncMock()),
            patch("routers.sync._build_nuvio_library_items", AsyncMock(return_value=[{"content_id": "tt0133093"}])),
            patch("routers.sync._build_nuvio_watched_items", AsyncMock(return_value=[{"content_id": "tt0133093"}])),
            patch("routers.sync._push_nuvio_library_delta", library_push),
            patch.object(nuvio, "push_watched_items", watched_push),
            patch("routers.sync.refresh_stream_connection", AsyncMock()),
            patch("core.pull_cycle.defer_fan_out", return_value=False),
            patch("core.tracking_snapshot.require_stream_reconciliation", AsyncMock()),
        ):
            await _fan_out_changes_to_other_connections(
                db, self.owner.id, None, {movie.id}, {}, settings=None,
                new_collected_ids={movie.id},
            )

        self.assertEqual({call.args[1].id for call in library_push.await_args_list}, {201, 202})
        self.assertEqual(watched_push.await_count, 2)
        self.assertEqual({call.args[2] for call in watched_push.await_args_list}, {1, 2})

    async def test_nuvio_progress_queue_targets_only_approved_enabled_peer_profiles(self):
        from core.stream_actions import queue_progress_update

        source = await self._connection(
            provider="nuvio", account_id="remote-a", profile_id="1", token="source-refresh",
        )
        same_account_peer = await self._connection(
            provider="nuvio", account_id="remote-a", profile_id="2", token="peer-refresh",
        )
        other_account_peer = await self._connection(
            provider="nuvio", account_id="remote-b", profile_id="1", token="other-refresh",
        )
        disabled = await self._connection(
            provider="nuvio", account_id="remote-c", profile_id="1", token="disabled-refresh",
        )
        unapproved = await self._connection(
            provider="nuvio", account_id="remote-d", profile_id="1", token="unapproved-refresh",
        )
        for conn in (same_account_peer, other_account_peer, disabled, unapproved):
            conn.push_playback = conn is not disabled
        movie = Media(
            id=57, tmdb_id=603, imdb_id="tt0133093", media_type=MediaType.movie, title="The Matrix",
            created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1),
        )
        self.db.add(movie)
        for conn, approved in (
            (same_account_peer, True), (other_account_peer, True),
            (disabled, True), (unapproved, False),
        ):
            self.db.add(StreamBaseline(
                connection_id=conn.id, user_id=self.owner.id,
                snapshot={"mappings": {"tt0133093": 603}, "progress": {}},
                approved=approved, observed_at=datetime(2026, 9, 1),
            ))
        await self.db.commit()

        await queue_progress_update(
            self.db,
            source,
            movie,
            {
                "content_id": "tt0133093", "position": 120000, "duration": 600000,
                "updated_at": "2026-09-28T12:00:00Z",
            },
        )
        queued = (await self.db.execute(select(StreamAction))).scalars().all()
        self.assertEqual({item.connection_id for item in queued}, {same_account_peer.id, other_account_peer.id})
        self.assertTrue(all(item.payload["source_connection_id"] == source.id for item in queued))
        self.assertTrue(all(item.payload["content_id"] == "tt0133093" for item in queued))

    async def test_connection_status_checks_each_nuvio_profile_independently(self):
        from routers.auth import get_connection_status

        first = await self._connection(provider="nuvio", account_id="remote-a", profile_id="1", token="refresh-a")
        second = await self._connection(provider="nuvio", account_id="remote-a", profile_id="2", token="refresh-b")
        await self.db.commit()
        session = nuvio.NuvioSession("access", "rotated", 3600, account_id="remote-a")

        async def _validate(_url, token, profile_id, *, on_refresh=None):
            return session, [{"profile_index": int(profile_id), "name": f"Profile {profile_id}"}]

        with (
            patch.object(nuvio, "validate_connection", AsyncMock(side_effect=_validate)) as validate,
            patch("core.connection_identity.persist_rotated_session", AsyncMock()),
        ):
            result = await get_connection_status(db=self.db, current_user=self.owner)

        statuses = {row["id"]: row for row in result["connections"]}
        self.assertTrue(statuses[first.id]["connected"])
        self.assertTrue(statuses[second.id]["connected"])
        self.assertEqual(statuses[first.id]["profile_name"], "Profile 1")
        self.assertEqual(statuses[second.id]["profile_name"], "Profile 2")
        self.assertEqual(validate.await_count, 2)

    async def test_stale_bookkeeping_cannot_overwrite_a_switched_account(self):
        conn = await self._connection(provider="stremio", account_id="account-a")
        await self.db.commit()
        connection_id = conn.id

        async with AsyncSession(self.engine, expire_on_commit=False) as switching_db:
            switched = await switching_db.get(MediaServerConnection, connection_id)
            switched.provider_account_id = "account-b"
            switched.identity_version += 1
            switched.stremio_pushed_library_ids = []
            await switching_db.commit()

        conn.stremio_pushed_library_ids = ["tt0133093"]
        with self.assertRaises(StaleDataError):
            await self.db.commit()
        await self.db.rollback()
        fresh = await self.db.get(MediaServerConnection, connection_id)
        self.assertEqual(fresh.provider_account_id, "account-b")
        self.assertEqual(fresh.identity_version, 1)
        self.assertEqual(fresh.stremio_pushed_library_ids, [])

    async def test_connection_update_rejects_a_different_owner(self):
        conn = await self._connection(provider="stremio", account_id="account-a")
        await self.db.commit()

        with self.assertRaises(HTTPException) as missing:
            await auth.update_connection(
                conn.id,
                MediaServerConnectionUpdate(token="new-token"),
                db=self.db,
                current_user=self.other,
            )
        self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
