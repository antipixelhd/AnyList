"""Regression coverage for the selectively ported Scrob fixes."""

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from models.base import Base, MediaType
from models.media import Media
from models.show import Show
from models.users import User
from models.lists import List, ListItem
from models.events import WatchEvent
from models.tracking import TrackingActivity, ProviderMatch
from models.password_reset import PasswordResetToken
from models.episode_order import EpisodeOrderMapping
from core import (
    episode_order,
    media_presentation,
    server_sync,
    show_metadata,
    watch_dedup,
    plex,
    sonarr,
    webhook_payloads,
)
from core.security import get_password_hash, verify_password
from routers import admin, simkl, webhooks
from db import get_db
from dependencies import require_admin, get_current_user


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


class MemoryDB(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.addAsyncCleanup(self.engine.dispose)


class IdentityRegressionTests(MemoryDB):
    async def test_mixed_category_membership_and_season_membership_stay_separate(self):
        async with self.Session() as db:
            movie = Media(tmdb_id=100, media_type=MediaType.movie, title="Movie")
            show = Media(tmdb_id=100, media_type=MediaType.series, title="Show")
            lists = [
                List(user_id=1, name=name) for name in ("Movies", "Shows", "Season")
            ]
            db.add_all([movie, show, *lists])
            await db.flush()
            db.add_all(
                [
                    ListItem(list_id=lists[0].id, media_id=movie.id),
                    ListItem(list_id=lists[1].id, media_id=show.id),
                    ListItem(list_id=lists[2].id, media_id=show.id, season_number=1),
                ]
            )
            await db.commit()
            items = [
                {"tmdb_id": 100, "type": "movie"},
                {"tmdb_id": 100, "type": "series"},
                {"tmdb_id": 100, "type": "series", "season_number": 1},
            ]
            with (
                patch.object(
                    media_presentation,
                    "get_order_keys_for_series",
                    AsyncMock(return_value={}),
                ),
                patch.object(
                    media_presentation.settings_store,
                    "get_global_settings",
                    AsyncMock(return_value=None),
                ),
            ):
                result = await media_presentation.enrich_with_state(db, 1, items)
            self.assertEqual(
                [i["in_lists"] for i in result],
                [[lists[0].id], [lists[1].id], [lists[2].id]],
            )

    async def test_tvdb_identity_merge_preserves_anylist_activity_and_matches(self):
        async with self.Session() as db:
            show = Show(tmdb_id=100, title="Show")
            db.add(show)
            await db.flush()
            canonical = Media(
                tmdb_id=101,
                media_type=MediaType.episode,
                title="Episode",
                show_id=show.id,
                season_number=1,
                episode_number=25,
            )
            divergent = Media(
                tmdb_id=None,
                tvdb_id=700,
                media_type=MediaType.episode,
                title="Twin",
                show_id=show.id,
                season_number=2,
                episode_number=1,
            )
            db.add_all([canonical, divergent])
            await db.flush()
            activity = TrackingActivity(
                user_id=1, media_id=divergent.id, status="completed"
            )
            match = ProviderMatch(
                user_id=1,
                provider="jellyfin",
                external_key="episode",
                media_id=divergent.id,
            )
            event = WatchEvent(
                user_id=1,
                media_id=divergent.id,
                watched_at=datetime(2020, 1, 1),
                completed=True,
            )
            db.add_all(
                [
                    activity,
                    match,
                    event,
                    EpisodeOrderMapping(
                        series_tmdb_id=100,
                        tmdb_season_number=1,
                        tmdb_episode_number=25,
                        tmdb_episode_id=101,
                        tvdb_season_number=2,
                        tvdb_episode_number=1,
                        tvdb_id=700,
                    ),
                ]
            )
            await db.commit()
            stats = await episode_order.reconcile_divergent_episode_media(db, show)
            await db.commit()
            self.assertEqual(stats["merged"], 1)
            for row in (activity, match, event):
                await db.refresh(row)
                self.assertEqual(row.media_id, canonical.id)
            self.assertIsNone(await db.get(Media, divergent.id))

    async def test_jellyfin_sync_uses_exact_tvdb_episode_id(self):
        async with self.Session() as db:
            db.add(
                EpisodeOrderMapping(
                    series_tmdb_id=100,
                    tmdb_season_number=1,
                    tmdb_episode_number=25,
                    tmdb_episode_id=101,
                    tvdb_season_number=2,
                    tvdb_episode_number=1,
                    tvdb_id=700,
                )
            )
            await db.commit()
            positions = await episode_order.load_tvdb_episode_id_positions(
                db, [100], [700]
            )
            item = {
                "ProviderIds": {"Tvdb": "700"},
                "ParentIndexNumber": 2,
                "IndexNumber": 1,
            }
            self.assertEqual(
                server_sync._canonical_episode_position(item, 100, positions), (1, 25)
            )
            self.assertIsNone(
                server_sync._canonical_episode_position(
                    {**item, "IndexNumberEnd": 2}, 100, positions
                )
            )
            self.assertIsNone(
                server_sync._canonical_episode_position(item, 200, positions)
            )


class AdminPasswordRegressionTests(MemoryDB):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.app = FastAPI()
        self.app.include_router(admin.router, prefix="/admin")

        async def database():
            async with self.Session() as db:
                yield db

        self.app.dependency_overrides[get_db] = database
        self.app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
            id=1, is_admin=True
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.addAsyncCleanup(self.client.aclose)
        async with self.Session() as db:
            user = User(
                username="person",
                email="person@example.com",
                api_key="key",
                password_hash=get_password_hash("old"),
                totp_enabled=True,
                totp_secret="keep",
            )
            db.add(user)
            await db.flush()
            self.user_id = user.id
            db.add(PasswordResetToken(user_id=user.id, token="old-reset"))
            await db.commit()

    async def test_reset_changes_login_and_invalidates_links_without_changing_identity_or_2fa(
        self,
    ):
        response = await self.client.post(
            f"/admin/users/{self.user_id}/reset-password",
            json={"password": "new-password"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("new-password", response.text)
        async with self.Session() as db:
            user = await db.get(User, self.user_id)
            self.assertTrue(verify_password("new-password", user.password_hash))
            self.assertFalse(verify_password("old", user.password_hash))
            self.assertEqual(user.email, "person@example.com")
            self.assertTrue(user.totp_enabled)
            self.assertEqual(user.totp_secret, "keep")
            self.assertEqual(
                (await db.execute(select(PasswordResetToken))).scalars().all(), []
            )

    async def test_validation_and_missing_user(self):
        for password in ("", "x" * 1025):
            res = await self.client.post(
                f"/admin/users/{self.user_id}/reset-password",
                json={"password": password},
            )
            self.assertEqual(res.status_code, 422)
        res = await self.client.post(
            "/admin/users/9999/reset-password", json={"password": "new"}
        )
        self.assertEqual(res.status_code, 404)

    async def test_non_admin_and_anonymous_cannot_reset(self):
        del self.app.dependency_overrides[require_admin]
        res = await self.client.post(
            f"/admin/users/{self.user_id}/reset-password", json={"password": "new"}
        )
        self.assertEqual(res.status_code, 401)
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=2, is_admin=False
        )
        res = await self.client.post(
            f"/admin/users/{self.user_id}/reset-password", json={"password": "new"}
        )
        self.assertEqual(res.status_code, 403)


class ProviderRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_insert_race_returns_winner_and_unrelated_integrity_error_propagates(
        self,
    ):
        from sqlalchemy.exc import IntegrityError

        winner = SimpleNamespace(id=1, tmdb_id=100)
        for result in (winner, None):
            db = SimpleNamespace(
                execute=AsyncMock(
                    side_effect=[
                        SimpleNamespace(scalar_one_or_none=lambda: None),
                        SimpleNamespace(scalar_one_or_none=lambda: result),
                    ]
                ),
                add=Mock(),
                begin_nested=Mock(return_value=AsyncMock()),
                flush=AsyncMock(
                    side_effect=IntegrityError("insert", {}, Exception("duplicate"))
                ),
            )
            with patch.object(
                show_metadata.tmdb, "get_show", AsyncMock(return_value={"name": "Show"})
            ):
                if result:
                    self.assertIs(
                        await show_metadata.find_or_create_show(db, 100), winner
                    )
                else:
                    with self.assertRaises(IntegrityError):
                        await show_metadata.find_or_create_show(db, 100)

    async def test_dedup_acquires_postgres_transaction_lock_before_query(self):
        db = SimpleNamespace(
            get_bind=lambda: SimpleNamespace(
                dialect=SimpleNamespace(name="postgresql")
            ),
            execute=AsyncMock(
                side_effect=[None, SimpleNamespace(scalar_one_or_none=lambda: None)]
            ),
        )
        await watch_dedup.find_duplicate_watch_event(db, 7, 42, datetime(2020, 1, 1), 5)
        self.assertIn(
            "pg_advisory_xact_lock", str(db.execute.call_args_list[0].args[0])
        )
        self.assertEqual(
            db.execute.call_args_list[0].args[1], {"user_id": 7, "media_id": 42}
        )

    async def test_plex_library_pages_all_categories_and_propagates_failed_pages(self):
        for fn in (
            plex.get_movies,
            plex.get_shows,
            plex.get_seasons,
            plex.get_episodes,
        ):
            with patch.object(
                plex,
                "_get",
                AsyncMock(
                    side_effect=[
                        {"MediaContainer": {"Metadata": [{"id": 1}], "totalSize": 2}},
                        {"MediaContainer": {"Metadata": [{"id": 2}], "totalSize": 2}},
                    ]
                ),
            ) as get:
                self.assertEqual(
                    await fn("http://plex", "token", "1"), [{"id": 1}, {"id": 2}]
                )
                self.assertEqual(
                    get.call_args_list[1].kwargs["params"]["X-Plex-Container-Start"], 1
                )
                self.assertEqual(
                    get.call_args_list[0].kwargs["params"]["X-Plex-Container-Size"],
                    1000,
                )
        with patch.object(
            plex,
            "_get",
            AsyncMock(
                side_effect=[
                    {"MediaContainer": {"Metadata": [{}], "totalSize": 2}},
                    RuntimeError("offline"),
                ]
            ),
        ):
            with self.assertRaises(RuntimeError):
                await plex.get_episodes("http://plex", "token", "1")

    async def test_sonarr_v3_resolves_zero_language_profile_and_v4_skips_lookup(self):
        real = httpx.AsyncClient
        for lookup in ({"title": "Show", "languageProfileId": 0}, {"title": "Show"}):
            paths = []
            posted = []

            def handler(request):
                import json

                paths.append(request.url.path)
                if request.url.path.endswith("/lookup"):
                    return httpx.Response(200, json=[lookup])
                if request.url.path.endswith("/languageprofile"):
                    return httpx.Response(200, json=[{"id": 3}])
                posted.append(json.loads(request.content))
                return httpx.Response(200, json={"id": 1})

            with patch.object(
                sonarr.httpx,
                "AsyncClient",
                side_effect=lambda **kw: real(
                    transport=httpx.MockTransport(handler), **kw
                ),
            ):
                await sonarr.add_series("http://sonarr", "token", 1, "/shows", 2)
            self.assertEqual(
                "/api/v3/languageprofile" in paths, "languageProfileId" in lookup
            )
            if "languageProfileId" in lookup:
                self.assertEqual(posted[0]["languageProfileId"], 3)

    async def test_simkl_pin_failures_are_readable_and_do_not_persist_a_code(self):
        req = httpx.Request("GET", "https://simkl.invalid")
        outcomes = [
            httpx.HTTPStatusError(
                "bad", request=req, response=httpx.Response(403, request=req)
            ),
            httpx.ConnectError("offline"),
            ValueError("invalid JSON"),
            {},
            [],
        ]
        for outcome in outcomes:
            settings = SimpleNamespace(simkl_client_id="id")
            db = SimpleNamespace(
                execute=AsyncMock(
                    return_value=SimpleNamespace(scalar_one_or_none=lambda: settings)
                ),
                commit=AsyncMock(),
            )
            provider = (
                AsyncMock(side_effect=outcome)
                if isinstance(outcome, Exception)
                else AsyncMock(return_value=outcome)
            )
            with patch.object(simkl.simkl_client, "start_pin_auth", provider):
                from fastapi import HTTPException

                with self.assertRaises(HTTPException) as err:
                    await simkl.simkl_pin_start(
                        db=db, current_user=SimpleNamespace(id=1)
                    )
            self.assertEqual(err.exception.status_code, 502)
            provider.assert_awaited_once_with("id")
            self.assertFalse(hasattr(settings, "simkl_device_code"))
            db.commit.assert_not_awaited()

    async def test_kodi_runtime_uses_file_length_then_metadata_and_preserves_existing_runtime(
        self,
    ):
        for existing, total, expected in (
            (None, 3187, 53),
            (42, 3187, 42),
            (None, None, 99),
        ):
            media = SimpleNamespace(runtime=existing)
            db = SimpleNamespace(commit=AsyncMock())
            with patch.object(
                webhooks, "_runtime_from_tmdb", AsyncMock(return_value=99)
            ) as fallback:
                await webhooks._backfill_kodi_runtime(
                    db, media, {"total_seconds": total}, "key"
                )
            self.assertEqual(media.runtime, expected)
            self.assertEqual(
                fallback.await_count, int(existing is None and total is None)
            )

    async def test_kodi_library_echo_is_ignored_but_real_playback_is_recorded(self):
        for library_update in (True, False):
            payload = {
                "method": "Player.OnStop",
                "source": "library_update" if library_update else "playback",
                "item": {"type": "movie", "title": "Movie", "uniqueid": {"tmdb": "1"}},
                "params": {"data": {"end": True}},
            }
            request = SimpleNamespace(
                body=AsyncMock(return_value=b"payload"),
                json=AsyncMock(return_value=payload),
            )
            settings = SimpleNamespace(duplicate_watch_window_minutes=5)
            db = SimpleNamespace(
                execute=AsyncMock(
                    side_effect=[
                        SimpleNamespace(scalar_one_or_none=lambda: settings),
                        SimpleNamespace(scalar_one=lambda: 1),
                    ]
                ),
                commit=AsyncMock(),
            )
            with (
                patch.object(
                    webhooks,
                    "find_or_create_media_kodi",
                    AsyncMock(return_value=SimpleNamespace(id=10, runtime=42)),
                ),
                patch.object(
                    webhooks.settings_store,
                    "get_effective_tmdb_key",
                    AsyncMock(return_value="key"),
                ),
                patch.object(webhooks, "_close_session", AsyncMock(return_value=None)),
                patch.object(webhooks, "_write_watch_event", AsyncMock()) as write,
                patch.object(webhooks.scrobble_delivery, "forward", AsyncMock()),
            ):
                result = await webhooks._handle_kodi_webhook(
                    request, db, SimpleNamespace(id=1)
                )
            self.assertEqual(result["status"], "ignored" if library_update else "ok")
            self.assertEqual(write.await_count, 0 if library_update else 1)

    def test_hama_show_guid_and_kodi_library_marker_are_retained(self):
        data = webhook_payloads.parse_plex_payload(
            {
                "event": "media.play",
                "Metadata": {
                    "type": "episode",
                    "grandparentGuid": "com.plexapp.agents.hama://tvdb-73762/1/1",
                },
            }
        )
        self.assertEqual(str(data["grandparent_tvdb_id"]), "73762")
        kodi = webhook_payloads.parse_kodi_payload(
            {
                "method": "Player.OnStop",
                "source": "library_update",
                "item": {"type": "movie", "title": "Movie", "uniqueid": {"tmdb": "1"}},
                "total_seconds": 3600,
            }
        )
        self.assertTrue(kodi["library_update"])
        self.assertEqual(kodi["total_seconds"], 3600)


class PostgresConcurrencyRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = os.environ.get("SCROB_REGRESSION_DATABASE_URL")
        if not url:
            self.skipTest("Set SCROB_REGRESSION_DATABASE_URL to a disposable database")
        self.engine = create_async_engine(url)
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(
                    c,
                    tables=[
                        User.__table__,
                        Show.__table__,
                        Media.__table__,
                        WatchEvent.__table__,
                    ],
                )
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            user = User(
                username="concurrency", email="concurrency@example.com", api_key="key"
            )
            media = Media(tmdb_id=50, media_type=MediaType.movie, title="Movie")
            db.add_all([user, media])
            await db.commit()
            self.uid = user.id
            self.mid = media.id
        self.addAsyncCleanup(self.engine.dispose)

    async def asyncTearDown(self):
        if hasattr(self, "engine"):
            async with self.engine.begin() as conn:
                await conn.run_sync(
                    lambda c: Base.metadata.drop_all(
                        c,
                        tables=[
                            User.__table__,
                            Show.__table__,
                            Media.__table__,
                            WatchEvent.__table__,
                        ],
                    )
                )

    async def test_concurrent_watch_events_create_one_play(self):
        import asyncio

        first_locked = asyncio.Event()
        release_first = asyncio.Event()
        second_entered = asyncio.Event()
        at = datetime(2020, 1, 1)

        async def write_first():
            async with self.Session() as db:
                duplicate = await watch_dedup.find_duplicate_watch_event(
                    db, self.uid, self.mid, at, 5
                )
                self.assertIsNone(duplicate)
                first_locked.set()
                await release_first.wait()
                db.add(
                    WatchEvent(
                        user_id=self.uid,
                        media_id=self.mid,
                        watched_at=at,
                        completed=True,
                    )
                )
                await db.commit()

        async def write_second():
            await first_locked.wait()
            async with self.Session() as db:
                second_entered.set()
                duplicate = await watch_dedup.find_duplicate_watch_event(
                    db, self.uid, self.mid, at, 5
                )
                if duplicate is None:
                    db.add(
                        WatchEvent(
                            user_id=self.uid,
                            media_id=self.mid,
                            watched_at=at,
                            completed=True,
                        )
                    )
                await db.commit()

        one = asyncio.create_task(write_first())
        two = asyncio.create_task(write_second())
        await second_entered.wait()
        await asyncio.sleep(0.05)
        self.assertFalse(two.done())
        release_first.set()
        await asyncio.wait_for(asyncio.gather(one, two), 5)
        async with self.Session() as db:
            self.assertEqual(
                len((await db.execute(select(WatchEvent))).scalars().all()), 1
            )

    async def test_concurrent_show_creation_returns_same_row_and_keeps_both_sessions_usable(
        self,
    ):
        import asyncio

        both_fetched = asyncio.Event()
        fetch_count = 0

        async def metadata(*args, **kwargs):
            nonlocal fetch_count
            fetch_count += 1
            if fetch_count == 2:
                both_fetched.set()
            await both_fetched.wait()
            return {"name": "New show", "seasons": []}

        async def create():
            async with self.Session() as db:
                show = await show_metadata.find_or_create_show(db, 999, "key")
                await db.commit()
                self.assertEqual(
                    (await db.execute(select(User.id))).scalar_one(), self.uid
                )
                return show.id

        with patch.object(show_metadata.tmdb, "get_show", metadata):
            ids = await asyncio.wait_for(asyncio.gather(create(), create()), 5)
        self.assertEqual(ids[0], ids[1])
