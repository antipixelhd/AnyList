"""Focused Stremio delivery tests, independent of sync-router orchestration."""

import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import stremio, stremio_delivery
from models.base import MediaType


class StremioDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_push_clears_stale_resume_for_paused_item_and_preserves_other_state(self) -> None:
        connection = SimpleNamespace(id=49, token="auth-key", push_collection=True,
            push_watched=False, push_playback=True, stremio_pushed_library_ids=None)
        remote = {"_id":"tt0133093", "type":"movie", "name":"The Matrix", "removed":False,
            "temp":False, "state":{"timeOffset":120_000, "duration":600_000, "timesWatched":2,
                "watched":"history", "lastWatched":"date"}}
        cleared = {**remote, "state":{**remote["state"], "timeOffset":0}}
        media = SimpleNamespace(tmdb_id=603, media_type=MediaType.movie, imdb_id="tt0133093", tmdb_data=None)
        baseline = SimpleNamespace(snapshot={"mappings":{"tt0133093":603}})
        db = SimpleNamespace(get=AsyncMock(return_value=baseline), execute=AsyncMock(side_effect=[
            SimpleNamespace(all=lambda:[(media,"paused")]),
            SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:[])),
        ]), refresh=AsyncMock())
        with patch("core.nuvio_projection.build_library_items", AsyncMock(return_value=[{
                "content_id":"tt0133093", "content_type":"movie", "title":"The Matrix",
            }])), \
             patch("core.nuvio_projection.build_progress_items", AsyncMock(return_value=[])), \
             patch.object(stremio, "datastore_get", AsyncMock(side_effect=[[remote],[cleared]])), \
             patch.object(stremio, "datastore_put", AsyncMock()) as write:
            await stremio_delivery.push_connection(db, connection, 7, api_key=None)

        payload = write.await_args.args[1][0]
        self.assertEqual(payload["state"]["timeOffset"], 0)
        self.assertEqual(payload["state"]["watched"], "history")
        self.assertEqual(payload["state"]["timesWatched"], 2)
        self.assertEqual(payload["state"]["lastWatched"], "date")


    async def test_full_push_preserves_unrelated_remote_items(self) -> None:
        connection = SimpleNamespace(
            id=44,
            token="auth-key",
            push_collection=True,
            push_watched=False,
            push_playback=False,
            stremio_pushed_library_ids=None,
        )
        local = {
            "content_id": "tt0133093",
            "content_type": "movie",
            "name": "The Matrix",
            "poster": "https://example.test/matrix.jpg",
            "poster_shape": "poster",
        }
        remote_only = {
            "_id": "tt9999999",
            "name": "Remote only",
            "type": "movie",
            "removed": False,
            "temp": False,
            "_ctime": "2026-01-01T00:00:00Z",
            "_mtime": "2026-01-01T00:00:00Z",
            "state": {"customPlaybackState": True},
            "unknownField": {"preserve": True},
        }
        datastore_put = AsyncMock()

        with (
            patch(
                "core.nuvio_projection.build_library_items",
                AsyncMock(return_value=[local]),
            ),
            patch.object(
                stremio,
                "datastore_get",
                AsyncMock(return_value=[remote_only]),
            ),
            patch.object(stremio, "datastore_put", datastore_put),
        ):
            changed = await stremio_delivery.push_connection(
                SimpleNamespace(refresh=AsyncMock()),
                connection,
                7,
                api_key="tmdb-key",
            )

        self.assertEqual(changed, 1)
        pushed = datastore_put.await_args.args[1]
        self.assertEqual([item["_id"] for item in pushed], ["tt0133093"])
        self.assertEqual(remote_only["unknownField"], {"preserve": True})
        self.assertEqual(connection.stremio_pushed_library_ids, ["tt0133093"])


    async def test_remote_removal_requires_prior_scrob_push(self) -> None:
        connection = SimpleNamespace(
            id=45,
            token="auth-key",
            push_collection=True,
            push_watched=False,
            push_playback=False,
            stremio_pushed_library_ids=["tt0133093"],
        )
        remote_items = [
            {
                "_id": "tt0133093",
                "name": "The Matrix",
                "type": "movie",
                "removed": False,
                "temp": False,
                "_ctime": "2026-01-01T00:00:00Z",
                "_mtime": "2026-01-01T00:00:00Z",
                "state": {},
            },
            {
                "_id": "tt9999999",
                "name": "Remote only",
                "type": "movie",
                "removed": False,
                "temp": False,
                "_ctime": "2026-01-01T00:00:00Z",
                "_mtime": "2026-01-01T00:00:00Z",
                "state": {},
            },
        ]
        datastore_put = AsyncMock()

        with (
            patch(
                "core.nuvio_projection.build_library_items",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                stremio,
                "datastore_get",
                AsyncMock(return_value=remote_items),
            ),
            patch.object(stremio, "datastore_put", datastore_put),
        ):
            changed = await stremio_delivery.push_connection(
                SimpleNamespace(refresh=AsyncMock()),
                connection,
                7,
                api_key="tmdb-key",
            )

        self.assertEqual(changed, 1)
        pushed = datastore_put.await_args.args[1]
        self.assertEqual([item["_id"] for item in pushed], ["tt0133093"])
        self.assertTrue(pushed[0]["removed"])
        self.assertEqual(connection.stremio_pushed_library_ids, [])


    async def test_unknown_watch_creates_temporary_item_without_fabricating_date(self) -> None:
        await self._assert_watch_override(None)


    async def test_dated_watch_override_serializes_datetime_as_epoch_milliseconds(self) -> None:
        await self._assert_watch_override(datetime(2026, 9, 27, 12, tzinfo=timezone.utc))


    async def _assert_watch_override(self, watched_at) -> None:
        connection = SimpleNamespace(
            id=46,
            token="auth-key",
            push_collection=False,
            push_watched=True,
            push_playback=False,
            stremio_pushed_library_ids=None,
        )
        record = {
            "content_id": "tt0133093",
            "content_type": "movie",
            "title": "The Matrix",
            "watched_at": None,
        }
        datastore_put = AsyncMock()
        with (
            patch(
                "core.nuvio_projection.build_watched_items",
                AsyncMock(return_value=[record]),
            ) as build_watched,
            patch(
                "core.stremio_delivery.media_records",
                AsyncMock(return_value={10: {key: record[key] for key in ("content_id", "content_type", "title")}}),
            ),
            patch(
                "core.stremio_delivery.latest_watched_at",
                AsyncMock(return_value={10: watched_at}),
            ),
            patch.object(stremio, "datastore_get", AsyncMock(return_value=[])),
            patch.object(stremio, "datastore_put", datastore_put),
        ):
            changed = await stremio_delivery.push_connection(
                SimpleNamespace(refresh=AsyncMock()),
                connection,
                7,
                api_key="tmdb-key",
                changed_media_ids={10},
                watch_overrides={10: True},
            )

        self.assertEqual(changed, 1)
        build_watched.assert_awaited_once_with(
            ANY,
            7,
            media_ids={10},
            api_key="tmdb-key",
            include_unknown_dates=True,
            tracked_only=True,
        )
        pushed = datastore_put.await_args.args[1][0]
        self.assertTrue(pushed["removed"])
        self.assertTrue(pushed["temp"])
        self.assertEqual(pushed["state"]["timesWatched"], 1)
        if watched_at is None:
            self.assertIsNone(pushed["state"]["lastWatched"])
        else:
            self.assertEqual(pushed["state"]["lastWatched"], '2026-09-27T12:00:00Z')


    async def test_unwatch_clears_state_without_erasing_last_known_date(self) -> None:
        connection = SimpleNamespace(
            id=47,
            token="auth-key",
            push_collection=False,
            push_watched=True,
            push_playback=False,
            stremio_pushed_library_ids=None,
        )
        record = {
            "content_id": "tt0133093",
            "content_type": "movie",
            "title": "The Matrix",
        }
        remote = {
            "_id": "tt0133093",
            "name": "The Matrix",
            "type": "movie",
            "removed": True,
            "temp": True,
            "_ctime": "2026-01-01T00:00:00Z",
            "_mtime": "2026-01-01T00:00:00Z",
            "state": {
                "timesWatched": 1,
                "lastWatched": "2026-01-01T00:00:00Z",
            },
        }
        datastore_put = AsyncMock()
        with (
            patch(
                "core.nuvio_projection.build_watched_items",
                AsyncMock(return_value=[]),
            ),
            patch(
                "core.stremio_delivery.media_records",
                AsyncMock(return_value={10: record}),
            ),
            patch(
                "core.stremio_delivery.latest_watched_at",
                AsyncMock(return_value={}),
            ),
            patch.object(stremio, "datastore_get", AsyncMock(return_value=[remote])),
            patch.object(stremio, "datastore_put", datastore_put),
        ):
            await stremio_delivery.push_connection(
                SimpleNamespace(refresh=AsyncMock()),
                connection,
                7,
                api_key="tmdb-key",
                changed_media_ids={10},
                watch_overrides={10: False},
            )

        pushed = datastore_put.await_args.args[1][0]
        self.assertEqual(pushed["state"]["timesWatched"], 0)
        self.assertEqual(
            pushed["state"]["lastWatched"],
            "2026-01-01T00:00:00Z",
        )


    async def test_progress_without_collection_creates_temporary_item(self) -> None:
        connection = SimpleNamespace(
            id=48,
            token="auth-key",
            push_collection=False,
            push_watched=False,
            push_playback=True,
            stremio_pushed_library_ids=None,
        )
        progress = {
            "content_id": "tt0133093",
            "content_type": "movie",
            "title": "The Matrix",
            "position": 120_000,
            "duration": 600_000,
            "last_watched": None,
        }
        stored: list[dict] = []
        async def save_items(_token, items):
            stored.extend(items)
        datastore_put = AsyncMock(side_effect=save_items)
        with (
            patch(
                "core.nuvio_projection.build_progress_items",
                AsyncMock(return_value=[progress]),
            ),
            patch.object(stremio, "datastore_get", AsyncMock(side_effect=[[], stored])),
            patch.object(stremio, "datastore_put", datastore_put),
        ):
            await stremio_delivery.push_connection(
                SimpleNamespace(
                    get=AsyncMock(return_value=None),
                    execute=AsyncMock(side_effect=[
                        SimpleNamespace(all=lambda: []),
                        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
                    ]),
                    refresh=AsyncMock(),
                ),
                connection,
                7,
                api_key="tmdb-key",
            )

        pushed = datastore_put.await_args.args[1][0]
        self.assertFalse(pushed["removed"])
        self.assertTrue(pushed["temp"])
        self.assertEqual(pushed["state"]["timeOffset"], 120_000)
        self.assertEqual(pushed["state"]["duration"], 600_000)
