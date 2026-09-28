"""Mocked regression tests for provider data-clear adapters."""

import copy
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import nuvio, stremio


_REAL_ASYNC_CLIENT = httpx.AsyncClient


class NuvioClearAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_all_selected_buckets_removes_remote_only_rows_and_uses_origin_identity(self):
        url = "https://nuvio.example"
        access_token = "fixture-access"
        origin_id = "anylist-clear-test-identity"
        state = {
            "library": [
                {"content_id": "remote-library-a", "content_type": "movie", "extra": "a"},
                {"content_id": "remote-library-b", "content_type": "series", "extra": "b"},
            ],
            "watched": [
                {"content_id": "remote-watched-a", "content_type": "movie", "watched_at": 10},
                {"content_id": "remote-watched-b", "content_type": "series", "season": 1,
                 "episode": 2, "watched_at": 20},
            ],
            "progress": [
                {"progress_key": "remote-progress-a", "content_id": "remote-a", "content_type": "movie"},
                {"progress_key": "remote-progress-b", "content_id": "remote-b", "content_type": "series"},
            ],
        }
        rpc_calls = []

        async def handler(request):
            function = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content)
            rpc_calls.append((function, payload))
            if function == "sync_push_library":
                state["library"] = payload["p_items"]
            elif function == "sync_delete_watched_items":
                keys = {
                    (row["content_id"], row.get("season"), row.get("episode"))
                    for row in payload["p_keys"]
                }
                state["watched"] = [
                    row for row in state["watched"]
                    if (row["content_id"], row.get("season"), row.get("episode")) not in keys
                ]
            elif function == "sync_delete_watch_progress":
                keys = set(payload["p_keys"])
                state["progress"] = [row for row in state["progress"] if row["progress_key"] not in keys]
            return httpx.Response(204, request=request)

        def make_client(**kwargs):
            return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

        session = nuvio.NuvioSession(access_token, "rotated-refresh", 3600)
        with (
            patch.object(nuvio, "refresh_session", AsyncMock(return_value=session)),
            patch.object(nuvio, "_pull_library", AsyncMock(side_effect=lambda *args: list(state["library"]))),
            patch.object(nuvio, "_pull_watched_items", AsyncMock(side_effect=lambda *args: list(state["watched"]))),
            patch.object(nuvio, "_pull_watch_progress", AsyncMock(side_effect=lambda *args: list(state["progress"]))),
            patch.object(nuvio, "_get_origin_client_id", return_value=origin_id),
            patch.object(nuvio, "update_next_up_dismissals", AsyncMock()) as hide_next_up,
            patch.object(nuvio.httpx, "AsyncClient", side_effect=make_client),
        ):
            counts = await nuvio.clear_sync_data(
                url, "old-refresh", 3, collection=True, watched=True, playback=True,
            )

        self.assertEqual(counts, {"collection": 2, "watched": 2, "playback": 2})
        self.assertEqual(state, {"library": [], "watched": [], "progress": []})
        watched_delete = next(payload for name, payload in rpc_calls if name == "sync_delete_watched_items")
        self.assertEqual(watched_delete["p_origin_client_id"], origin_id)
        self.assertEqual(len(watched_delete["p_keys"]), 2)
        self.assertEqual(
            {name for name, _ in rpc_calls},
            {"sync_push_library", "sync_delete_watched_items", "sync_delete_watch_progress"},
        )
        hide_next_up.assert_awaited_once()
        self.assertEqual(hide_next_up.await_args.kwargs["hide"], set())

    async def test_playback_only_hides_retained_watched_series_and_preserves_other_buckets(self):
        url = "https://nuvio.example"
        access_token = "fixture-access"
        library = [{"content_id": "provider-only-library", "content_type": "movie"}]
        watched = [
            {"content_id": "provider-only-show", "content_type": "series", "season": 1,
             "episode": 1, "watched_at": 20},
            {"content_id": "provider-only-film", "content_type": "movie", "watched_at": 30},
        ]
        progress = [{"progress_key": "playback-key", "content_id": "provider-show", "content_type": "series"}]
        pull_progress = AsyncMock(side_effect=[progress, []])
        rpc = AsyncMock()
        session = nuvio.NuvioSession(access_token, "rotated-refresh", 3600)

        with (
            patch.object(nuvio, "refresh_session", AsyncMock(return_value=session)),
            patch.object(nuvio, "_pull_library", AsyncMock(return_value=library)) as pull_library,
            patch.object(nuvio, "_pull_watched_items", AsyncMock(return_value=watched)) as pull_watched,
            patch.object(nuvio, "_pull_watch_progress", pull_progress),
            patch.object(nuvio, "_rpc", rpc),
            patch.object(nuvio, "update_next_up_dismissals", AsyncMock()) as hide_next_up,
        ):
            counts = await nuvio.clear_sync_data(
                url, "old-refresh", 3, playback=True,
            )

        self.assertEqual(counts, {"collection": 0, "watched": 0, "playback": 1})
        pull_library.assert_not_awaited()
        pull_watched.assert_awaited_once()
        self.assertEqual(pull_progress.await_args_list[0].args[-1], 3)
        self.assertEqual(rpc.await_args.args[3], "sync_delete_watch_progress")
        self.assertEqual(rpc.await_args.args[4]["p_keys"], ["playback-key"])
        self.assertEqual(hide_next_up.await_args.kwargs["hide"], {"provider-only-show"})
        self.assertEqual(library, [{"content_id": "provider-only-library", "content_type": "movie"}])
        self.assertEqual(watched[0]["content_id"], "provider-only-show")
        self.assertEqual(watched[1]["content_id"], "provider-only-film")

    async def test_full_watch_progress_page_refuses_all_selected_writes(self):
        session = nuvio.NuvioSession("fixture-access", "rotated-refresh", 3600)
        for row_count in (200, 201):
            with self.subTest(row_count=row_count):
                rpc = AsyncMock()
                with (
                    patch.object(nuvio, "refresh_session", AsyncMock(return_value=session)),
                    patch.object(nuvio, "_pull_library", AsyncMock(return_value=[{"content_id": "library"}])) as pull_library,
                    patch.object(nuvio, "_pull_watched_items", AsyncMock(return_value=[{"content_id": "watched"}])) as pull_watched,
                    patch.object(nuvio, "_pull_watch_progress", AsyncMock(return_value=[{"progress_key": str(i)} for i in range(row_count)])),
                    patch.object(nuvio, "_rpc", rpc),
                    patch.object(nuvio, "update_next_up_dismissals", AsyncMock()) as hide_next_up,
                ):
                    with self.assertRaisesRegex(nuvio.NuvioAPIError, "maximum watch-progress page"):
                        await nuvio.clear_sync_data(
                            "https://nuvio.example", "old-refresh", 3,
                            collection=True, watched=True, playback=True,
                        )

                pull_library.assert_awaited_once()
                pull_watched.assert_awaited_once()
                rpc.assert_not_awaited()
                hide_next_up.assert_not_awaited()

    async def test_library_clear_fails_when_successful_rpc_is_not_reflected_in_readback(self):
        url = "https://nuvio.example"
        row = {"content_id": "provider-only-item", "content_type": "movie"}
        rpc_calls = []

        async def handler(request):
            function = request.url.path.rsplit("/", 1)[-1]
            rpc_calls.append(function)
            return httpx.Response(204, request=request)

        session = nuvio.NuvioSession("fixture-access", "rotated-refresh", 3600)
        with (
            patch.object(nuvio, "refresh_session", AsyncMock(return_value=session)),
            patch.object(nuvio, "_pull_library", AsyncMock(return_value=[row])) as pull_library,
            patch.object(nuvio.httpx, "AsyncClient", side_effect=lambda **kwargs:
                 _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)),
        ):
            with self.assertRaisesRegex(nuvio.NuvioAPIError, "did not confirm library removal"):
                await nuvio.clear_sync_data(
                    url, "old-refresh", 3, collection=True,
                )

        self.assertEqual(rpc_calls, ["sync_push_library"])
        self.assertEqual(pull_library.await_count, 2)


class StremioClearAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_all_selected_fields_for_every_provider_record_and_preserve_unknown_fields(self):
        initial = [
            {
                "_id": "provider-only-a",
                "type": "series",
                "removed": False,
                "temp": True,
                "providerMetadata": {"future": True},
                "state": {
                    "lastWatched": "date-a",
                    "timeWatched": 41,
                    "overallTimeWatched": 42,
                    "timesWatched": 3,
                    "flaggedWatched": 1,
                    "watched": "history-a",
                    "timeOffset": 500,
                    "video_id": "episode-a",
                    "duration": 900,
                    "providerState": {"future": "kept"},
                },
            },
            {
                "_id": "provider-only-b",
                "type": "movie",
                "removed": False,
                "temp": False,
                "state": {"watched": "history-b", "timeOffset": 90, "video_id": "movie-b", "duration": 180},
            },
        ]
        remote = {item["_id"]: copy.deepcopy(item) for item in initial}
        put_batches = []

        async def datastore_get(_auth_key, *, ids=None, all_items=False, allow_missing=False):
            if all_items:
                return copy.deepcopy(list(remote.values()))
            return [copy.deepcopy(remote[item_id]) for item_id in ids if item_id in remote]

        async def datastore_put(_auth_key, changes):
            put_batches.append(copy.deepcopy(changes))
            for item in changes:
                remote[item["_id"]] = copy.deepcopy(item)

        with (
            patch.object(stremio, "datastore_get", side_effect=datastore_get) as get,
            patch.object(stremio, "datastore_put", side_effect=datastore_put) as put,
        ):
            counts = await stremio.clear_datastore_data(
                "fixture-auth", collection=True, watched=True, playback=True,
            )

        self.assertEqual(counts, {"collection": 2, "watched": 2, "playback": 2})
        self.assertEqual(len(put_batches), 2)
        for item in remote.values():
            self.assertTrue(item["removed"])
            self.assertFalse(item["temp"])
            self.assertEqual(item["state"]["lastWatched"], None)
            self.assertEqual(item["state"]["watched"], None)
            self.assertEqual(item["state"]["timesWatched"], 0)
            self.assertEqual(item["state"]["timeOffset"], 0)
            self.assertIsNone(item["state"]["video_id"])
            self.assertEqual(item["state"]["duration"], 0)
        self.assertEqual(remote["provider-only-a"]["providerMetadata"], {"future": True})
        self.assertEqual(remote["provider-only-a"]["state"]["providerState"], {"future": "kept"})
        self.assertEqual(set(remote), {"provider-only-a", "provider-only-b"})
        self.assertEqual(get.await_count, 4)
        put.assert_awaited()

    async def test_playback_clear_preserves_unselected_collection_and_watched_state(self):
        original = {
            "_id": "provider-only-show",
            "type": "series",
            "removed": False,
            "temp": True,
            "providerMetadata": "kept",
            "state": {
                "lastWatched": "watched-date",
                "watched": "watched-bitfield",
                "timesWatched": 4,
                "timeOffset": 120,
                "video_id": "episode-id",
                "duration": 600,
                "providerState": "kept",
            },
        }
        remote = copy.deepcopy(original)

        async def get(_auth_key, *, ids=None, all_items=False, allow_missing=False):
            return [copy.deepcopy(remote)]

        async def put(_auth_key, changes):
            nonlocal remote
            remote = copy.deepcopy(changes[0])

        with (
            patch.object(stremio, "datastore_get", side_effect=get),
            patch.object(stremio, "datastore_put", side_effect=put),
        ):
            counts = await stremio.clear_datastore_data("fixture-auth", playback=True)

        self.assertEqual(counts, {"collection": 0, "watched": 0, "playback": 1})
        self.assertFalse(remote["removed"])
        self.assertTrue(remote["temp"])
        self.assertEqual(remote["providerMetadata"], "kept")
        self.assertEqual(remote["state"]["lastWatched"], "watched-date")
        self.assertEqual(remote["state"]["watched"], "watched-bitfield")
        self.assertEqual(remote["state"]["timesWatched"], 4)
        self.assertEqual(remote["state"]["providerState"], "kept")
        self.assertEqual(remote["state"]["timeOffset"], 0)
        self.assertIsNone(remote["state"]["video_id"])
        self.assertEqual(remote["state"]["duration"], 0)

    async def test_malformed_stremio_ids_fail_before_first_write(self):
        bad_snapshots = [
            [{"type": "movie", "state": {}}],
            [{"_id": "provider-a", "state": {}}, {"_id": "provider-a", "state": {}}],
            [{"_id": "  ", "state": {}}],
        ]
        for snapshot in bad_snapshots:
            with self.subTest(snapshot=snapshot):
                get = AsyncMock(return_value=snapshot)
                put = AsyncMock()
                with (
                    patch.object(stremio, "datastore_get", get),
                    patch.object(stremio, "datastore_put", put),
                ):
                    with self.assertRaisesRegex(stremio.StremioAPIError, "library id"):
                        await stremio.clear_datastore_data("fixture-auth", collection=True)
                get.assert_awaited_once_with("fixture-auth", all_items=True)
                put.assert_not_awaited()

    async def test_viewing_clear_fails_when_acknowledged_write_is_not_read_back(self):
        remote = {
            "_id": "provider-only-item",
            "state": {"timeOffset": 20, "video_id": "episode", "duration": 60},
        }
        get = AsyncMock(side_effect=[
            [copy.deepcopy(remote)],
            [copy.deepcopy(remote)],
        ])
        put = AsyncMock()

        with (
            patch.object(stremio, "datastore_get", get),
            patch.object(stremio, "datastore_put", put),
        ):
            with self.assertRaisesRegex(stremio.StremioAPIError, "did not confirm playback removal"):
                await stremio.clear_datastore_data("fixture-auth", playback=True)

        put.assert_awaited_once()
        self.assertEqual(get.await_count, 2)

    async def test_final_verification_rejects_a_concurrently_added_unselected_record(self):
        remote = {
            "provider-original": {"_id": "provider-original", "removed": False, "temp": False, "state": {}},
        }

        async def get(_auth_key, *, ids=None, all_items=False, allow_missing=False):
            if all_items:
                return copy.deepcopy(list(remote.values()))
            return [copy.deepcopy(remote[item_id]) for item_id in ids if item_id in remote]

        async def put(_auth_key, changes):
            for item in changes:
                remote[item["_id"]] = copy.deepcopy(item)
            remote["provider-arrived-during-clear"] = {
                "_id": "provider-arrived-during-clear",
                "removed": False,
                "temp": False,
                "state": {},
            }

        get_mock = AsyncMock(side_effect=get)
        put_mock = AsyncMock(side_effect=put)
        with (
            patch.object(stremio, "datastore_get", get_mock),
            patch.object(stremio, "datastore_put", put_mock),
        ):
            with self.assertRaisesRegex(stremio.StremioAPIError, "did not confirm collection removal"):
                await stremio.clear_datastore_data("fixture-auth", collection=True)

        put_mock.assert_awaited_once()
        self.assertEqual(get_mock.await_count, 3)
        self.assertTrue(remote["provider-original"]["removed"])
        self.assertFalse(remote["provider-arrived-during-clear"]["removed"])


if __name__ == "__main__":
    unittest.main()
