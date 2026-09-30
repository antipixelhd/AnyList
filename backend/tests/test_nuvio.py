import json
from datetime import datetime, timezone
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
from core import outbound_sync

from core import nuvio
from models.base import MediaType
from models.media import Media
from models.show import Show
from models.sync import SyncStatus
from routers.sync import _apply_nuvio_watch_history, _normalize_nuvio_item, _run_full_push
from schemas import MediaServerConnectionResponse


_REAL_ASYNC_CLIENT = httpx.AsyncClient

class _Result:
    def __init__(self, *, scalars=None, rows=None):
        self._scalars = scalars or []
        self._rows = rows or []

    def scalars(self):
        return _Result(rows=self._scalars)

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None

    def scalar_one(self):
        if len(self._scalars) != 1:
            raise AssertionError(f"expected one scalar result, got {len(self._scalars)}")
        return self._scalars[0]


def _approved_stream_gate_results(user_id: int) -> list[_Result]:
    """Model an existing user, no active clear, an approved baseline, and no reviews."""
    return [
        _Result(scalars=[user_id]),
        _Result(),
        _Result(scalars=[SimpleNamespace(approved=True, snapshot={})]),
        _Result(rows=[]),
    ]


class _SessionCM:
    """Fakes the `async with async_sessionmaker(...)() as db:` pattern used by
    background job functions that open their own session instead of taking
    `db` as a parameter."""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc_info):
        return False


class NuvioClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_origin_client_id_is_persisted_for_the_installation(self):
        with tempfile.TemporaryDirectory() as data_dir, \
                patch.object(nuvio.settings, "data_dir", Path(data_dir)), \
                patch.object(nuvio, "_origin_client_id", None):
            first = nuvio._get_origin_client_id()
            identity_path = Path(data_dir) / nuvio._ORIGIN_CLIENT_ID_FILE

            self.assertRegex(first, r"^anylist-[a-z0-9]{32}$")
            self.assertEqual(identity_path.read_text(encoding="ascii"), first)

            # Simulate a process restart: discard only the in-memory cache.
            nuvio._origin_client_id = None
            self.assertEqual(nuvio._get_origin_client_id(), first)

    async def test_origin_client_id_repairs_a_corrupt_persisted_value(self):
        with tempfile.TemporaryDirectory() as data_dir, \
                patch.object(nuvio.settings, "data_dir", Path(data_dir)), \
                patch.object(nuvio, "_origin_client_id", None):
            identity_path = Path(data_dir) / nuvio._ORIGIN_CLIENT_ID_FILE
            identity_path.write_text("invalid identity", encoding="ascii")

            repaired = nuvio._get_origin_client_id()

            self.assertRegex(repaired, r"^anylist-[a-z0-9]{32}$")
            self.assertEqual(identity_path.read_text(encoding="ascii"), repaired)

    async def test_origin_client_id_is_added_to_current_mutation_rpcs_only(self):
        seen: dict[str, dict] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            function_name = request.url.path.rsplit("/", 1)[-1]
            seen[function_name] = json.loads(request.content or b"{}")
            return httpx.Response(204)

        mutation_rpcs = (
            "sync_push_watched_items",
            "sync_push_watch_progress",
            "sync_delete_watch_progress",
            "sync_push_profile_settings_blob",
        )
        with tempfile.TemporaryDirectory() as data_dir, \
                patch.object(nuvio.settings, "data_dir", Path(data_dir)), \
                patch.object(nuvio, "_origin_client_id", None):
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                for function_name in mutation_rpcs:
                    await nuvio._rpc(
                        client,
                        "https://api.nuvio.tv",
                        "access-token",
                        function_name,
                        {"p_profile_id": 1, "p_origin_client_id": "request-scoped"},
                    )
                await nuvio._rpc(
                    client,
                    "https://api.nuvio.tv",
                    "access-token",
                    "sync_pull_watch_progress",
                    {"p_profile_id": 1},
                )

            expected_id = nuvio._get_origin_client_id()
            for function_name in mutation_rpcs:
                self.assertEqual(seen[function_name]["p_origin_client_id"], expected_id)
                self.assertEqual(seen[function_name]["p_profile_id"], 1)
            self.assertNotIn("p_origin_client_id", seen["sync_pull_watch_progress"])

    async def test_next_up_projection_hides_non_watching_and_untracked_history(self):
        from core.nuvio_visibility import next_up_visibility
        rows = []
        records = []
        for index, status in enumerate(['watching', 'paused', 'dropped', 'planning', 'completed'], 1):
            media = Media(id=index, title=status, media_type=MediaType.series, tmdb_id=index,
                imdb_id=f'tt{index}', tmdb_data={})
            rows.append((media, status))
            records.append({'content_id': f'tt{index}', 'content_type': 'series'})
        records.append({'content_id': 'tt6', 'content_type': 'series'})
        db = SimpleNamespace(execute=AsyncMock(return_value=_Result(rows=rows)),
            get=AsyncMock(return_value=None))
        hidden, visible, seeds = await next_up_visibility(db, 7, 1, records)
        self.assertEqual(hidden, {'tt2', 'tt3', 'tt4', 'tt5', 'tt6',
            'tmdb:2', 'tmdb:3', 'tmdb:4', 'tmdb:5'})
        self.assertEqual(visible, {'tt1', 'tmdb:1'})

    async def test_next_up_merge_preserves_settings_and_unrelated_dismissals(self):
        import copy
        original = {'version': 1, 'features': {'appearance': {'theme': {'type': 'string', 'value': 'dark'}},
            'trakt_settings': {'other': {'type': 'boolean', 'value': True},
                'dismissed_next_up_keys': {'type': 'string_set', 'value': ['tt1|1|2', 'tt2', 'tt3|2|1']}}}}
        stored = copy.deepcopy(original)
        async def rpc(_client, _url, _token, operation, params):
            nonlocal stored
            if params['p_platform'] == 'mobile':
                self.assertEqual(operation, 'sync_pull_profile_settings_blob')
                return []
            self.assertEqual(params['p_platform'], 'tv')
            self.assertEqual(params['p_profile_id'], 3)
            if operation == 'sync_pull_profile_settings_blob':
                return [{'settings_json': stored}]
            self.assertEqual(operation, 'sync_push_profile_settings_blob')
            stored = copy.deepcopy(params['p_settings_json'])
        with patch.object(nuvio, '_rpc', AsyncMock(side_effect=rpc)) as calls:
            changed = await nuvio.update_next_up_dismissals(None, 'https://example.test', 'fixture', 3,
                hide=['tt4'], show=['tt1'], seeds={})
            self.assertTrue(changed)
            self.assertEqual(stored['features']['trakt_settings']['dismissed_next_up_keys']['value'],
                ['tt2', 'tt3|2|1', 'tt4'])
            self.assertEqual(stored['features']['appearance'], original['features']['appearance'])
            self.assertEqual(stored['features']['trakt_settings']['other'], original['features']['trakt_settings']['other'])
            self.assertEqual(original['features']['trakt_settings']['dismissed_next_up_keys']['value'],
                ['tt1|1|2', 'tt2', 'tt3|2|1'])
            calls.reset_mock()
            self.assertFalse(await nuvio.update_next_up_dismissals(None, 'https://example.test', 'fixture', 3,
                hide=['tt4'], show=['tt1'], seeds={}))
            self.assertEqual(calls.await_count, 2)

    async def test_next_up_rejects_malformed_blob_without_overwriting_it(self):
        rpc = AsyncMock(return_value=[{'settings_json': {'features': []}}])
        with patch.object(nuvio, '_rpc', rpc), self.assertRaises(nuvio.NuvioAPIError):
            await nuvio.update_next_up_dismissals(None, 'https://example.test', 'fixture', 3, hide=['tt1'], seeds={})
        self.assertEqual(rpc.await_count, 1)

    async def test_next_up_rejects_unconfirmed_write(self):
        rpc = AsyncMock(side_effect=[[], None, []])
        with patch.object(nuvio, '_rpc', rpc), self.assertRaises(nuvio.NuvioAPIError):
            await nuvio.update_next_up_dismissals(None, 'https://example.test', 'fixture', 3, hide=['tt1'], seeds={})

    async def test_next_up_initializes_empty_profile_settings(self):
        desired = {'version': 1, 'features': {'trakt_settings': {
            'dismissed_next_up_keys': {'type': 'string_set', 'value': ['tt1']}}}}
        rpc = AsyncMock(side_effect=[[{'settings_json': {}}], None, [{'settings_json': desired}], []])
        with patch.object(nuvio, '_rpc', rpc):
            self.assertTrue(await nuvio.update_next_up_dismissals(None, 'https://example.test', 'fixture', 3, hide=['tt1'], seeds={}))
        self.assertEqual(rpc.await_args_list[1].args[-1]['p_settings_json'], desired)

    async def test_connection_response_redacts_refresh_token(self) -> None:
        response = MediaServerConnectionResponse.model_validate(
            {
                "id": 1,
                "user_id": 7,
                "type": "nuvio",
                "name": "Nuvio",
                "url": nuvio.DEFAULT_URL,
                "token": "secret-refresh-token",
                "created_at": datetime(2026, 9, 19),
            }
        )

        self.assertEqual(response.token, "")

    async def test_sign_in_uses_custom_app_anon_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["apikey"], "self-hosted-anon-key")
            return httpx.Response(
                200,
                json={
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600,
                },
            )

        transport = httpx.MockTransport(handler)
        with (
            patch.dict(os.environ, {"NUVIO_APP_ANON_KEY": "self-hosted-anon-key"}),
            patch.object(
                nuvio.httpx,
                "AsyncClient",
                side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
            ),
        ):
            session = await nuvio.sign_in("https://nuvio.example.com", "user@example.com", "password")

        self.assertEqual(session.refresh_token, "refresh-token")

    async def test_pull_sync_data_refreshes_session_and_paginates_library(self) -> None:
        library_offsets: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/token":
                self.assertEqual(request.url.params["grant_type"], "refresh_token")
                self.assertEqual(json.loads(request.content), {"refresh_token": "old-refresh"})
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-token",
                        "refresh_token": "new-refresh",
                        "expires_in": 3600,
                    },
                )
            self.assertEqual(request.headers["authorization"], "Bearer access-token")
            payload = json.loads(request.content or b"{}")
            if request.url.path.endswith("/sync_pull_profiles"):
                return httpx.Response(200, json=[{"profile_index": 2, "name": "Main"}])
            if request.url.path.endswith("/sync_pull_library"):
                library_offsets.append(payload["p_offset"])
                item_count = 500 if payload["p_offset"] == 0 else 1
                return httpx.Response(
                    200,
                    json=[
                        {"content_id": f"tmdb:{index + payload['p_offset']}", "content_type": "movie"}
                        for index in range(item_count)
                    ],
                )
            if request.url.path.endswith("/sync_pull_watched_items"):
                return httpx.Response(
                    200,
                    json=[{"content_id": "tmdb:550", "content_type": "movie", "watched_at": 1711600000000}],
                )
            if request.url.path.endswith("/sync_pull_watch_progress"):
                return httpx.Response(
                    200,
                    json=[{"content_id": "tmdb:550", "content_type": "movie", "position": 1000, "duration": 2000}],
                )
            if request.url.path.endswith("/sync_pull_profile_settings_blob"):
                platform = payload["p_platform"]
                if platform == "tv":
                    blob = {"features": {"trakt_settings": {
                        "dismissed_next_up_keys": {"type": "string_set", "value": ["tv-id"]}}}}
                else:
                    blob = {"features": {"continue_watching_settings_payload": json.dumps({
                        "dismissedNextUpKeys": ["mobile-id|2|4"]})}}
                return httpx.Response(200, json=[{"settings_json": blob}])
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            session, data = await nuvio.pull_sync_data(
                "https://api.nuvio.tv/",
                "old-refresh",
                2,
            )

        self.assertEqual(session.refresh_token, "new-refresh")
        self.assertEqual(library_offsets, [0, 500])
        self.assertEqual(len(data["library"]), 501)
        self.assertEqual(len(data["watched"]), 1)
        self.assertEqual(len(data["progress"]), 1)
        self.assertEqual(data["cw_visibility"], {
            "tv": ["tv-id"],
            "mobile": ["mobile-id|2|4"],
        })

    async def test_pull_sync_data_omits_only_invalid_visibility_platform(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/token":
                return httpx.Response(200, json={
                    "access_token": "access-token", "refresh_token": "new-refresh", "expires_in": 3600,
                })
            payload = json.loads(request.content or b"{}")
            if request.url.path.endswith("/sync_pull_profiles"):
                return httpx.Response(200, json=[{"profile_index": 2}])
            if request.url.path.endswith(("/sync_pull_library", "/sync_pull_watched_items", "/sync_pull_watch_progress")):
                return httpx.Response(200, json=[])
            if request.url.path.endswith("/sync_pull_profile_settings_blob"):
                if payload["p_platform"] == "tv":
                    return httpx.Response(200, json=[{"settings_json": {"features": {
                        "trakt_settings": {"dismissed_next_up_keys": {
                            "type": "string_set", "value": ["kept-tv-key"]}}}}}])
                return httpx.Response(200, json=[{"settings_json": {"features": {
                    "continue_watching_settings_payload": "not-json"}}}])
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)
        with patch.object(nuvio.httpx, "AsyncClient", side_effect=lambda **kwargs:
            _REAL_ASYNC_CLIENT(transport=transport, **kwargs)):
            _session, data = await nuvio.pull_sync_data("https://api.nuvio.tv/", "old-refresh", 2)

        self.assertEqual(data["cw_visibility"], {"tv": ["kept-tv-key"]})

    async def test_on_refresh_fires_before_a_later_pull_call_can_fail_it_away(self) -> None:
        # Regression test: Nuvio's refresh token is single-use. If a pull RPC
        # after the refresh fails, the caller must already have the new token
        # in hand — otherwise it's stranded on a refresh token Nuvio has
        # already invalidated, with no way back in short of a full re-login.
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/token":
                return httpx.Response(
                    200,
                    json={"access_token": "access-token", "refresh_token": "new-refresh", "expires_in": 3600},
                )
            if request.url.path.endswith("/sync_pull_profiles"):
                return httpx.Response(200, json=[{"profile_index": 2, "name": "Main"}])
            if request.url.path.endswith("/sync_pull_watch_progress"):
                return httpx.Response(404, json={"message": "could not find the function"})
            return httpx.Response(200, json=[])

        refreshed: list[str] = []

        async def on_refresh(session: nuvio.NuvioSession) -> None:
            refreshed.append(session.refresh_token)

        transport = httpx.MockTransport(handler)
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            with self.assertRaises(nuvio.NuvioAPIError):
                await nuvio.pull_sync_data(
                    "https://api.nuvio.tv/",
                    "old-refresh",
                    2,
                    on_refresh=on_refresh,
                )

        self.assertEqual(refreshed, ["new-refresh"])

    async def test_connection_lock_is_shared_by_connection_id(self) -> None:
        self.assertIs(nuvio.connection_lock(9001), nuvio.connection_lock(9001))
        self.assertIsNot(nuvio.connection_lock(9001), nuvio.connection_lock(9002))

    async def test_refresh_error_surfaces_supabase_msg_and_error_code(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"code": 400, "error_code": "refresh_token_already_used", "msg": "Invalid Refresh Token: Already Used"},
            )

        transport = httpx.MockTransport(handler)
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            with self.assertRaises(nuvio.NuvioAPIError) as ctx:
                await nuvio.refresh_session("https://api.nuvio.tv/", "stale-refresh")

        self.assertIn("Invalid Refresh Token: Already Used", str(ctx.exception))

    async def test_pull_watch_progress_omits_unsupported_offset_param(self) -> None:
        """Regression test: sync_pull_watch_progress has no p_offset parameter
        on the real API — sending one 404s with "could not find the function"
        because PostgREST can't match the signature. Only p_profile_id/p_limit
        are valid."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/token":
                return httpx.Response(
                    200,
                    json={"access_token": "access-token", "refresh_token": "new-refresh", "expires_in": 3600},
                )
            payload = json.loads(request.content or b"{}")
            if request.url.path.endswith("/sync_pull_profiles"):
                return httpx.Response(200, json=[{"profile_index": 2, "name": "Main"}])
            if request.url.path.endswith("/sync_pull_library"):
                return httpx.Response(200, json=[])
            if request.url.path.endswith("/sync_pull_watched_items"):
                return httpx.Response(200, json=[])
            if request.url.path.endswith("/sync_pull_watch_progress"):
                if "p_offset" in payload:
                    return httpx.Response(
                        404,
                        json={
                            "message": "Could not find the function public.sync_pull_watch_progress"
                            "(p_limit, p_offset, p_profile_id) in the schema cache"
                        },
                    )
                self.assertEqual(payload, {"p_profile_id": 2, "p_limit": 200})
                return httpx.Response(
                    200,
                    json=[{"content_id": "tmdb:550", "content_type": "movie", "position": 1, "duration": 2}],
                )
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            _, data = await nuvio.pull_sync_data("https://api.nuvio.tv/", "old-refresh", 2)

        self.assertEqual(len(data["progress"]), 1)

    async def test_pull_sync_data_tolerates_null_profile_index(self) -> None:
        """Regression test: a profile with profile_index: null must not crash
        pull_sync_data the way it previously crashed on int(None)."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/token":
                return httpx.Response(
                    200,
                    json={"access_token": "access-token", "refresh_token": "new-refresh", "expires_in": 3600},
                )
            if request.url.path.endswith("/sync_pull_profiles"):
                return httpx.Response(200, json=[{"profile_index": None, "name": "Kids"}, {"profile_index": 2, "name": "Main"}])
            if request.url.path.endswith(("/sync_pull_library", "/sync_pull_watched_items", "/sync_pull_watch_progress")):
                return httpx.Response(200, json=[])
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            session, data = await nuvio.pull_sync_data("https://api.nuvio.tv/", "old-refresh", 2)

        self.assertEqual(session.refresh_token, "new-refresh")

    async def test_push_watched_items_batches_without_full_replace(self) -> None:
        batch_sizes: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/token":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-token",
                        "refresh_token": "rotated-refresh",
                        "expires_in": 3600,
                    },
                )
            if request.url.path.endswith("/sync_push_watched_items"):
                payload = json.loads(request.content)
                self.assertEqual(payload["p_profile_id"], 1)
                batch_sizes.append(len(payload["p_items"]))
                return httpx.Response(204)
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)
        items = [
            {
                "content_id": f"tmdb:{index}",
                "content_type": "movie",
                "watched_at": 1711600000000,
            }
            for index in range(501)
        ]
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            session = await nuvio.push_watched_items(
                "https://api.nuvio.tv",
                "old-refresh",
                1,
                items,
            )

        self.assertEqual(session.refresh_token, "rotated-refresh")
        self.assertEqual(batch_sizes, [500, 1])

    async def test_verified_watched_history_push_and_delete_read_back_cloud_state(self) -> None:
        stored: dict[tuple[str, int | None, int | None], dict] = {}
        refreshes = 0

        def key(item: dict) -> tuple[str, int | None, int | None]:
            return item["content_id"], item.get("season"), item.get("episode")

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal refreshes
            if request.url.path == "/auth/v1/token":
                refreshes += 1
                return httpx.Response(200, json={
                    "access_token": "access-token",
                    "refresh_token": f"rotated-{refreshes}",
                    "expires_in": 3600,
                })
            if request.url.path.endswith("/sync_push_watched_items"):
                payload = json.loads(request.content)
                stored.update({key(item): item for item in payload["p_items"]})
                return httpx.Response(204)
            if request.url.path.endswith("/sync_pull_watched_items"):
                return httpx.Response(200, json=list(stored.values()))
            if request.url.path.endswith("/sync_delete_watched_items"):
                payload = json.loads(request.content)
                for item in payload["p_keys"]:
                    stored.pop(key(item), None)
                return httpx.Response(204)
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)
        item = {
            "content_id": "tt1234567",
            "content_type": "series",
            "season": 1,
            "episode": 2,
            "watched_at": 1780000000000,
        }
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            pushed = await nuvio.push_watched_items(
                "https://api.nuvio.tv", "old-refresh", 1, [item], verify=True,
            )
            deleted = await nuvio.delete_watched_items(
                "https://api.nuvio.tv", pushed.refresh_token, 1,
                [{"content_id": "tt1234567", "season": 1, "episode": 2}], verify=True,
            )

        self.assertEqual(pushed.refresh_token, "rotated-1")
        self.assertEqual(deleted.refresh_token, "rotated-2")
        self.assertEqual(stored, {})

    async def test_verified_watched_history_rejects_missing_rows_and_nonnumeric_dates(self) -> None:
        refresh_requests = 0
        writes: list[dict] = []
        remote_mode = "null-date"

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal refresh_requests
            if request.url.path == "/auth/v1/token":
                refresh_requests += 1
                return httpx.Response(200, json={
                    "access_token": "access-token",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 3600,
                })
            if request.url.path.endswith("/sync_push_watched_items"):
                writes.extend(json.loads(request.content)["p_items"])
                return httpx.Response(204)
            if request.url.path.endswith("/sync_pull_watched_items"):
                if remote_mode == "missing":
                    return httpx.Response(200, json=[])
                # The RPC accepted the write but the row cannot be decoded by
                # Nuvio Android because its required watched_at is null.
                return httpx.Response(200, json=[{**writes[0], "watched_at": None}])
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)
        item = {"content_id": "tt1234567", "content_type": "movie", "watched_at": 1}
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            with self.assertRaisesRegex(nuvio.NuvioAPIError, "without a numeric watched_at"):
                await nuvio.push_watched_items(
                    "https://api.nuvio.tv", "old-refresh", 1, [item], verify=True,
                )

            remote_mode = "missing"
            with self.assertRaisesRegex(nuvio.NuvioAPIError, "did not confirm"):
                await nuvio.push_watched_items(
                    "https://api.nuvio.tv", "old-refresh", 1, [item], verify=True,
                )

            with self.assertRaisesRegex(nuvio.NuvioAPIError, "requires a numeric watched_at"):
                await nuvio.push_watched_items(
                    "https://api.nuvio.tv", "old-refresh", 1,
                    [{**item, "watched_at": None}], verify=True,
                )

        self.assertEqual(refresh_requests, 2)

    async def test_push_sync_items_uses_watched_and_progress_endpoints(self) -> None:
        calls: list[tuple[str, int]] = []
        refresh_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal refresh_count
            if request.url.path == "/auth/v1/token":
                refresh_count += 1
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-token",
                        "refresh_token": "rotated-refresh",
                        "expires_in": 3600,
                    },
                )
            payload = json.loads(request.content)
            self.assertEqual(payload["p_profile_id"], 3)
            function_name = request.url.path.rsplit("/", 1)[-1]
            items_key = "p_entries" if function_name == "sync_push_watch_progress" else "p_items"
            self.assertEqual(set(payload), {"p_profile_id", items_key, "p_origin_client_id"})
            calls.append((function_name, len(payload[items_key])))
            return httpx.Response(204)

        watched_items = [{"content_id": "tmdb:550", "content_type": "movie", "watched_at": 1}]
        progress_items = [
            {
                "content_id": f"tmdb:{index}",
                "content_type": "movie",
                "video_id": f"tmdb:{index}",
                "position": 1000,
                "duration": 2000,
                "last_watched": 1,
            }
            for index in range(501)
        ]
        transport = httpx.MockTransport(handler)
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            session = await nuvio.push_sync_items(
                "https://api.nuvio.tv",
                "old-refresh",
                3,
                watched_items,
                progress_items,
            )

        self.assertEqual(session.refresh_token, "rotated-refresh")
        self.assertEqual(refresh_count, 1)
        self.assertEqual(
            calls,
            [
                ("sync_push_watched_items", 1),
                ("sync_push_watch_progress", 500),
                ("sync_push_watch_progress", 1),
            ],
        )


    async def test_merge_library_preserves_unrelated_remote_items(self) -> None:
        pushed_items: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/token":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-token",
                        "refresh_token": "rotated-refresh",
                        "expires_in": 3600,
                    },
                )
            if request.url.path.endswith("/sync_pull_library"):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "content_id": "tmdb:1",
                            "content_type": "movie",
                            "name": "Old title",
                            "addon_base_url": "https://addon.example",
                            "poster": "https://images.example/poster.jpg",
                            "background": "https://images.example/background.jpg",
                            "genres": ["Drama"],
                        },
                        {
                            "content_id": "tmdb:2",
                            "content_type": "movie",
                            "name": "Keep me",
                        },
                    ],
                )
            if request.url.path.endswith("/sync_push_library"):
                pushed_items.extend(json.loads(request.content)["p_items"])
                return httpx.Response(204)
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            session, count = await nuvio.merge_library(
                "https://api.nuvio.tv",
                "old-refresh",
                1,
                additions=[
                    {
                        "content_id": "tmdb:1",
                        "content_type": "movie",
                        "name": "New title",
                        "poster": "",
                        "background": None,
                        "genres": [],
                    },
                    {"content_id": "tmdb:3", "content_type": "movie", "name": "Added"},
                ],
                removed_content_ids=set(),
            )

        self.assertEqual(session.refresh_token, "rotated-refresh")
        self.assertEqual(count, 3)
        self.assertEqual({item["content_id"] for item in pushed_items}, {"tmdb:1", "tmdb:2", "tmdb:3"})
        updated = next(item for item in pushed_items if item["content_id"] == "tmdb:1")
        self.assertEqual(updated["name"], "New title")
        self.assertEqual(updated["addon_base_url"], "https://addon.example")
        self.assertEqual(updated["poster"], "https://images.example/poster.jpg")
        self.assertEqual(updated["background"], "https://images.example/background.jpg")
        self.assertEqual(updated["genres"], ["Drama"])

    async def test_push_library_replaces_snapshot_but_preserves_playback_metadata(self) -> None:
        pushed_items: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/token":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-token",
                        "refresh_token": "rotated-refresh",
                        "expires_in": 3600,
                    },
                )
            if request.url.path.endswith("/sync_pull_library"):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "content_id": "tmdb:1",
                            "content_type": "movie",
                            "addon_base_url": "https://addon.example",
                        },
                        {"content_id": "tmdb:2", "content_type": "movie"},
                    ],
                )
            if request.url.path.endswith("/sync_push_library"):
                pushed_items.extend(json.loads(request.content)["p_items"])
                return httpx.Response(204)
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            await nuvio.push_library(
                "https://api.nuvio.tv",
                "old-refresh",
                1,
                [{"content_id": "tmdb:1", "content_type": "movie", "name": "Only item"}],
            )

        self.assertEqual(len(pushed_items), 1)
        self.assertEqual(pushed_items[0]["content_id"], "tmdb:1")
        self.assertEqual(pushed_items[0]["addon_base_url"], "https://addon.example")


class RpcRetryTests(unittest.IsolatedAsyncioTestCase):
    """#389: a transient network/timeout failure on a Nuvio RPC call must be
    retried a couple of times instead of immediately failing the whole sync
    job, but a real error response from the server is never retried."""

    async def test_retries_a_transient_timeout_then_succeeds(self) -> None:
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            if calls["count"] < 3:
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        client = _REAL_ASYNC_CLIENT(transport=transport)
        try:
            with patch.object(nuvio.asyncio, "sleep", AsyncMock()):
                result = await nuvio._rpc(client, "https://api.nuvio.tv", "token", "sync_pull_profiles")
        finally:
            await client.aclose()

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls["count"], 3)

    async def test_gives_up_after_exhausting_retries(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        transport = httpx.MockTransport(handler)
        client = _REAL_ASYNC_CLIENT(transport=transport)
        try:
            with patch.object(nuvio.asyncio, "sleep", AsyncMock()):
                with self.assertRaises(httpx.ReadTimeout):
                    await nuvio._rpc(client, "https://api.nuvio.tv", "token", "sync_pull_profiles")
        finally:
            await client.aclose()

    async def test_a_real_error_response_is_never_retried(self) -> None:
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(400, json={"message": "bad request"})

        transport = httpx.MockTransport(handler)
        client = _REAL_ASYNC_CLIENT(transport=transport)
        try:
            with patch.object(nuvio.asyncio, "sleep", AsyncMock()):
                with self.assertRaises(nuvio.NuvioAPIError):
                    await nuvio._rpc(client, "https://api.nuvio.tv", "token", "sync_pull_profiles")
        finally:
            await client.aclose()

        self.assertEqual(calls["count"], 1)


class NuvioCollectionFanoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_collection_addition_pushes_imdb_item_to_nuvio(self) -> None:
        movie = Media(
            id=10,
            tmdb_id=1368337,
            media_type=MediaType.movie,
            title="The Odyssey",
            tmdb_data={"external_ids": {"imdb_id": "tt33764258"}},
        )
        conn = SimpleNamespace(
            id=4,
            type="nuvio",
            url="https://api.nuvio.tv",
            token="refresh-token",
            server_user_id="1",
            push_collection=True,
            push_watched=False,
            push_ratings=False,
        )
        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    _Result(scalars=[movie]),
                    _Result(scalars=[conn]),
                    _Result(rows=[]),
                    _Result(rows=[(datetime(2026, 7, 19, tzinfo=timezone.utc), movie)]),
                ]
            ),
            commit=AsyncMock(),
        )

        with (
            patch(
                "core.settings_store.get_effective_tmdb_key",
                AsyncMock(return_value="tmdb-token"),
            ),
            patch(
                "routers.sync.outbound_sync.push_nuvio_library_delta",
                AsyncMock(return_value=True),
            ) as push_delta,
            patch('core.tracking_snapshot.require_stream_reconciliation', AsyncMock()),
        ):
            await outbound_sync.fan_out_changes(
                db,
                user_id=7,
                exclude_connection_id=None,
                new_watched_ids=set(),
                new_ratings={},
                settings=None,
                new_collected_ids={movie.id},
            )

        push_delta.assert_awaited_once()
        _, _, current_items, changed_ids = push_delta.await_args.args
        self.assertEqual(changed_ids, {"tt33764258"})
        self.assertEqual(
            current_items,
            [
                {
                    "content_id": "tt33764258",
                    "content_type": "movie",
                    "name": "The Odyssey",
                    "poster": None,
                    "poster_shape": "poster",
                    "background": None,
                    "description": None,
                    "release_info": None,
                    "imdb_rating": None,
                    "genres": [],
                    "added_at": 1784419200000,
                }
            ],
        )

    async def test_library_delta_persists_rotated_token_even_if_the_push_fails(self) -> None:
        # Regression test for the disconnect bug: merge_library refreshes the
        # session and only then pushes the merged snapshot. If that push RPC
        # fails, the connection must already hold the rotated token — it must
        # not be silently discarded along with the failed push.
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/token":
                return httpx.Response(
                    200,
                    json={"access_token": "access-token", "refresh_token": "new-refresh", "expires_in": 3600},
                )
            if request.url.path.endswith("/sync_pull_library"):
                return httpx.Response(200, json=[])
            if request.url.path.endswith("/sync_push_library"):
                return httpx.Response(500, json={"message": "internal error"})
            return httpx.Response(404, json={"message": "unexpected request"})

        conn = SimpleNamespace(id=77, url="https://api.nuvio.tv", token="old-refresh", server_user_id="2")
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())

        transport = httpx.MockTransport(handler)
        with patch.object(
            nuvio.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            with self.assertRaises(nuvio.NuvioAPIError):
                await outbound_sync.push_nuvio_library_delta(db, conn, [], {"tt1"})

        self.assertEqual(conn.token, "new-refresh")
        db.commit.assert_awaited_once()


class NuvioWatchHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_distinct_watch_timestamps_are_imported_idempotently(self) -> None:
        movie = Media(id=10, tmdb_id=550, media_type=MediaType.movie, title="Fight Club")
        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    _Result(scalars=[movie]),
                    _Result(rows=[]),
                    _Result(),  # get_dedup_window_minutes lookup (#390)
                    # reconcile_inferred_watch_date checks confident matches
                    # and inferred candidates for both provider timestamps.
                    _Result(),
                    _Result(),
                    _Result(),
                    _Result(),
                    _Result(),
                    _Result(),
                    # record_rewatch_progress's own Media lookup, once per new
                    # WatchEvent (2 distinct timestamps below) - no-ops since
                    # this test isn't exercising rewatch behavior.
                    _Result(),
                    _Result(),
                ]
            ),
            add=MagicMock(),
            commit=AsyncMock(),
        )
        rows = [
            {
                "content_id": "tmdb:550",
                "content_type": "movie",
                "watched_at": 1711600000000,
            },
            {
                "content_id": "tmdb:550",
                "content_type": "movie",
                "watched_at": 1711700000000,
            },
            {
                "content_id": "tmdb:550",
                "content_type": "movie",
                "watched_at": 1711700000000,
            },
        ]

        added = await _apply_nuvio_watch_history(
            db,
            user_id=7,
            rows=rows,
            show_map={},
            tmdb_ids={"tmdb:550": 550},
        )

        self.assertEqual(added, {10})
        self.assertEqual(db.add.call_count, 2)
        self.assertEqual(
            {call.args[0].watched_at for call in db.add.call_args_list},
            {
                datetime(2024, 3, 28, 4, 26, 40),
                datetime(2024, 3, 29, 8, 13, 20),
            },
        )

class NuvioNormalizationTests(unittest.TestCase):
    def test_episode_history_maps_to_tmdb_series_and_watch_state(self) -> None:
        normalized = _normalize_nuvio_item(
            {
                "content_id": "tmdb:1396",
                "content_type": "series",
                "title": "Pilot",
                "season": 1,
                "episode": 1,
                "watched_at": 1711600000000,
            },
            profile_id=3,
            watched=True,
        )

        self.assertIsNotNone(normalized)
        media_type, item = normalized
        self.assertEqual(media_type, MediaType.episode)
        self.assertEqual(item["Id"], "3:tmdb:1396:s1e1")
        self.assertEqual(item["SeriesId"], "tmdb:1396")
        self.assertEqual(item["ProviderIds"], {})
        self.assertEqual(item["UserData"]["Played"], True)
        self.assertEqual(item["UserData"]["PlayCount"], 1)
        self.assertIsNotNone(item["UserData"]["LastPlayedDate"])


    def test_imdb_content_uses_resolved_tmdb_id(self) -> None:
        normalized = _normalize_nuvio_item(
            {
                "content_id": "tt0411008",
                "content_type": "series",
                "name": "Lost",
            },
            profile_id=1,
            tmdb_id=4607,
        )

        self.assertIsNotNone(normalized)
        media_type, item = normalized
        self.assertEqual(media_type, MediaType.series)
        self.assertEqual(item["Id"], "1:tt0411008")
        self.assertEqual(item["ProviderIds"], {"Tmdb": "4607"})


    def test_unsupported_content_identifier_is_skipped(self) -> None:
        self.assertIsNone(
            _normalize_nuvio_item(
                {"content_id": "imdb:tt0137523", "content_type": "movie"},
                profile_id=1,
            )
        )


class NuvioWatchedStateFanoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_episode_watch_delta_sends_null_dated_watch_to_nuvio(self) -> None:
        episode = Media(
            id=10,
            tmdb_id=987,
            media_type=MediaType.episode,
            title="The Rescue",
            show_id=5,
            season_number=1,
            episode_number=2,
        )
        show = Show(
            id=5,
            tmdb_id=123,
            title="Fixture Show",
            tmdb_data={"external_ids": {"imdb_id": "tt1234567"}},
        )
        connection = SimpleNamespace(
            id=4,
            user_id=7,
            type="nuvio",
            url="https://api.nuvio.tv",
            token="refresh-token",
            server_user_id="1",
            push_collection=False,
            push_watched=True,
            push_ratings=False,
            push_playback=False,
        )

        class _FanoutDB:
            def __init__(self):
                self.execute = AsyncMock(side_effect=[
                    _Result(scalars=[connection]),  # connected providers
                    _Result(rows=[]),               # collection file mappings
                    _Result(rows=[(episode.id, None, datetime(2026, 9, 1, 12, 30))]),  # canonical watch event
                ])
                self.refresh = AsyncMock()
                self.commit = AsyncMock()

        db = _FanoutDB()
        selected = AsyncMock(side_effect=[[episode], [show], [episode], [show]])
        pushed = AsyncMock()
        with (
            patch("core.pull_cycle.defer_fan_out", return_value=False),
            patch("core.outbound_sync._select_in_chunks", selected),
            patch("core.nuvio_projection.select_in_chunks", selected),
            patch("core.nuvio_projection.ensure_imdb_ids", AsyncMock()),
            patch("core.settings_store.get_effective_tmdb_key", AsyncMock(return_value="tmdb-key")),
            patch("core.tracking_snapshot.require_stream_reconciliation", AsyncMock()),
            patch.object(nuvio, "push_watched_items", pushed),
        ):
            await outbound_sync.fan_out_changes(
                db,
                user_id=7,
                exclude_connection_id=None,
                new_watched_ids={episode.id},
                new_ratings={},
                settings=None,
            )

        pushed.assert_awaited_once()
        self.assertEqual(pushed.await_args.args[3], [{
            "content_id": "tt1234567",
            "content_type": "series",
            "title": "The Rescue",
            "season": 1,
            "episode": 2,
            "watched_at": int(datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc).timestamp() * 1000),
        }])


class LocalTrackingRollbackDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_unwatch_delta_uses_history_adapter_once_in_delivery_job(self) -> None:
        from core.local_outbound import dispatch_local_tracking_delta

        db = SimpleNamespace(execute=AsyncMock(return_value=_Result()), commit=AsyncMock())
        fan_out = AsyncMock()
        push_watch_state = AsyncMock()
        start_job = AsyncMock()
        finish_job = AsyncMock()
        with (
            patch("core.local_outbound.async_sessionmaker", return_value=lambda: _SessionCM(db)),
            patch("routers.sync.outbound_sync.fan_out_changes", fan_out),
            patch("routers.history._push_watch_state", push_watch_state),
            patch("core.watch_intents.queue_watch_intents", AsyncMock()),
            patch("core.watch_intents.dispatch_watch_intents", AsyncMock()),
            patch("core.tracking_delivery.start_tracking_delivery_job", start_job),
            patch("core.tracking_delivery.finish_tracking_delivery_job", finish_job),
            patch("core.stream_actions.dispatch_stream_actions", AsyncMock()),
            patch("core.cloud_actions.dispatch_cloud_actions", AsyncMock()),
        ):
            await dispatch_local_tracking_delta(
                user_id=7,
                watched_ids=set(),
                ratings={},
                removed_ratings=set(),
                delivery_job_id=55,
                removed_watched_ids={10, 2},
            )

        fan_out.assert_not_awaited()
        push_watch_state.assert_awaited_once_with(
            db, 7, [2, 10], watched=False, skip_stream_watch_writes=True,
        )
        start_job.assert_awaited_once_with(db, 55)
        finish_job.assert_awaited_once_with(55)


class NuvioFullPushTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_push_uses_creation_time_for_unknown_date_episode_watches(self) -> None:
        conn = SimpleNamespace(
            id=4,
            user_id=7,
            type="nuvio",
            url="https://api.nuvio.tv",
            token="old-refresh",
            server_user_id="1",
            push_collection=False,
            push_watched=True,
            push_playback=False,
            stremio_pushed_library_ids=None,
        )
        user_settings = SimpleNamespace(tmdb_api_key="tmdb-key")
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[
                _Result(scalars=[99]),  # SyncJob status=running update
                _Result(scalars=[conn]),
                *_approved_stream_gate_results(7),
                _Result(scalars=[user_settings]),
                None,  # SyncJob totals update
                None,  # SyncJob completed update
            ]),
            commit=AsyncMock(),
            refresh=AsyncMock(),
            get=AsyncMock(return_value=SimpleNamespace(approved=True, snapshot={})),
        )
        created_at = datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc)
        watched_record = {
            "content_id": "tt1234567",
            "content_type": "series",
            "title": "The Rescue",
            "season": 1,
            "episode": 2,
            "watched_at": int(created_at.timestamp() * 1000),
        }
        pushed_items: list[dict] = []
        rpc_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            rpc_paths.append(request.url.path)
            if request.url.path == "/auth/v1/token":
                return httpx.Response(200, json={
                    "access_token": "access-token",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 3600,
                })
            if request.url.path.endswith("/sync_push_watched_items"):
                pushed_items.extend(json.loads(request.content)["p_items"])
                return httpx.Response(204)
            if request.url.path.endswith("/sync_pull_watched_items"):
                return httpx.Response(200, json=pushed_items)
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)
        visibility = AsyncMock(return_value=([], [], {}))
        update_visibility = AsyncMock()
        with (
            patch("routers.sync.async_sessionmaker", lambda *args, **kwargs: (lambda: _SessionCM(db))),
            patch("core.nuvio_projection.build_watched_items", AsyncMock(return_value=[watched_record])),
            patch("core.nuvio_visibility.next_up_visibility", visibility),
            patch.object(nuvio, "update_next_up_dismissals", update_visibility),
            patch.object(
                nuvio.httpx,
                "AsyncClient",
                side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
            ),
        ):
            await _run_full_push(user_id=7, connection_id=4, job_id=99)

        self.assertEqual(pushed_items, [watched_record])
        self.assertEqual(pushed_items[0]["watched_at"], int(created_at.timestamp() * 1000))
        visibility.assert_awaited_once()
        update_visibility.assert_awaited_once()
        self.assertFalse(any(path.endswith(("/sync_pull_watch_progress", "/sync_delete_watch_progress"))
            for path in rpc_paths))

    async def test_full_push_skips_existing_watches_and_pushes_new_numeric_date(self) -> None:
        conn = SimpleNamespace(id=4, user_id=7, type="nuvio", url="https://api.nuvio.tv",
            token="old-refresh", server_user_id="1", push_collection=False,
            push_watched=True, push_playback=False, stremio_pushed_library_ids=None)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            _Result(scalars=[99]), _Result(scalars=[conn]), *_approved_stream_gate_results(7),
            _Result(scalars=[SimpleNamespace(tmdb_api_key="tmdb-key")]), None, None,
        ]), commit=AsyncMock(), refresh=AsyncMock(),
            get=AsyncMock(return_value=SimpleNamespace(approved=True, snapshot={})))
        already_watched = {
            "content_id": "tt0000001", "content_type": "movie", "watched_at": 1700000000000,
        }
        newly_watched = {
            "content_id": "tt0000002", "content_type": "movie", "watched_at": 1800000000000,
        }
        remote_rows = [already_watched]
        pushed_items: list[dict] = []
        rpc_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            rpc_paths.append(request.url.path)
            if request.url.path == "/auth/v1/token":
                return httpx.Response(200, json={"access_token": "access-token",
                    "refresh_token": "rotated-refresh", "expires_in": 3600})
            if request.url.path.endswith("/sync_push_watched_items"):
                batch = json.loads(request.content)["p_items"]
                pushed_items.extend(batch)
                remote_rows.extend(batch)
                return httpx.Response(204)
            if request.url.path.endswith("/sync_pull_watched_items"):
                return httpx.Response(200, json=remote_rows)
            return httpx.Response(404)

        visibility = AsyncMock(return_value=([], [], {}))
        update_visibility = AsyncMock()
        with (
            patch("routers.sync.async_sessionmaker", lambda *args, **kwargs: (lambda: _SessionCM(db))),
            patch("core.nuvio_projection.build_watched_items",
                  AsyncMock(return_value=[already_watched, newly_watched])),
            patch("core.nuvio_visibility.next_up_visibility", visibility),
            patch.object(nuvio, "update_next_up_dismissals", update_visibility),
            patch.object(nuvio.httpx, "AsyncClient", side_effect=lambda **kwargs:
                _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)),
        ):
            await _run_full_push(user_id=7, connection_id=4, job_id=99)

        self.assertEqual(pushed_items, [newly_watched])
        visibility.assert_awaited_once()
        update_visibility.assert_awaited_once()
        self.assertFalse(any(path.endswith(("/sync_pull_watch_progress", "/sync_delete_watch_progress"))
            for path in rpc_paths))

    async def test_full_push_fails_when_nuvio_does_not_store_watched_episodes(self) -> None:
        conn = SimpleNamespace(id=4, user_id=7, type="nuvio", url="https://api.nuvio.tv",
            token="old-refresh", server_user_id="1", push_collection=False,
            push_watched=True, push_playback=False, stremio_pushed_library_ids=None)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            _Result(scalars=[99]), _Result(scalars=[conn]), *_approved_stream_gate_results(7),
            _Result(scalars=[SimpleNamespace(tmdb_api_key="tmdb-key")]),
            None, None,
        ]), commit=AsyncMock(), refresh=AsyncMock(),
            get=AsyncMock(return_value=SimpleNamespace(approved=True, snapshot={})))
        watched_record = {"content_id": "tt1234567", "content_type": "series",
            "title": "The Rescue", "season": 1, "episode": 2, "watched_at": None}
        rpc_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            rpc_paths.append(request.url.path)
            if request.url.path == "/auth/v1/token":
                return httpx.Response(200, json={"access_token": "access-token",
                    "refresh_token": "rotated-refresh", "expires_in": 3600})
            if request.url.path.endswith("/sync_push_watched_items"):
                return httpx.Response(204)
            if request.url.path.endswith("/sync_pull_watched_items"):
                return httpx.Response(200, json=[])
            return httpx.Response(404)

        visibility = AsyncMock(return_value=([], [], {}))
        update_visibility = AsyncMock()
        with (
            patch("routers.sync.async_sessionmaker", lambda *args, **kwargs: (lambda: _SessionCM(db))),
            patch("core.nuvio_projection.build_watched_items", AsyncMock(return_value=[watched_record])),
            patch("core.nuvio_visibility.next_up_visibility", visibility),
            patch.object(nuvio, "update_next_up_dismissals", update_visibility),
            patch.object(nuvio.httpx, "AsyncClient", side_effect=lambda **kwargs:
                _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)),
        ):
            await _run_full_push(user_id=7, connection_id=4, job_id=99)

        self.assertTrue(any(
            getattr(value, "value", None) == SyncStatus.failed
            for call in db.execute.await_args_list
            for value in getattr(call.args[0], "_values", {}).values()
        ))
        visibility.assert_awaited_once()
        update_visibility.assert_awaited_once()
        self.assertFalse(any(path.endswith(("/sync_pull_watch_progress", "/sync_delete_watch_progress"))
            for path in rpc_paths))

    async def test_full_push_merges_instead_of_replacing_remote_library(self) -> None:
        """A first full push must merge the local library without dropping
        remote-only items that AnyList has never managed."""
        conn = SimpleNamespace(
            id=4,
            user_id=7,
            type="nuvio",
            url="https://api.nuvio.tv",
            token="old-refresh",
            server_user_id="1",
            push_collection=True,
            push_watched=False,
            push_playback=False,
            stremio_pushed_library_ids=None,
        )
        user_settings = SimpleNamespace(tmdb_api_key="tmdb-key")

        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    _Result(scalars=[99]),  # SyncJob status=running update (job was pending)
                    _Result(scalars=[conn]),  # conn_result
                    *_approved_stream_gate_results(7),
                    _Result(scalars=[user_settings]),  # settings_result
                    None,  # SyncJob total_items update
                    None,  # SyncJob status=completed update
                ]
            ),
            commit=AsyncMock(),
            refresh=AsyncMock(),
            get=AsyncMock(return_value=SimpleNamespace(approved=True, snapshot={})),
        )

        pushed_items: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/token":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-token",
                        "refresh_token": "rotated-refresh",
                        "expires_in": 3600,
                    },
                )
            if request.url.path.endswith("/sync_pull_library"):
                return httpx.Response(
                    200,
                    json=[
                        {"content_id": "tmdb:1", "content_type": "movie", "name": "Local movie"},
                        {"content_id": "tt9999999", "content_type": "movie", "name": "Added on Nuvio directly"},
                    ],
                )
            if request.url.path.endswith("/sync_push_library"):
                pushed_items.extend(json.loads(request.content)["p_items"])
                return httpx.Response(204)
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)

        with (
            patch(
                "routers.sync.async_sessionmaker",
                lambda *args, **kwargs: (lambda: _SessionCM(db)),
            ),
            patch(
                "core.nuvio_projection.build_library_items",
                AsyncMock(return_value=[
                    {"content_id": "tmdb:1", "content_type": "movie", "name": "Local movie"},
                ]),
            ),
            patch.object(
                nuvio.httpx,
                "AsyncClient",
                side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
            ),
        ):
            await _run_full_push(user_id=7, connection_id=4, job_id=99)

        # The remote-only item ("tt9999999", never known locally) must survive
        # the push alongside the locally-known item, instead of being wiped by
        # a hard snapshot replace.
        self.assertEqual(
            {item["content_id"] for item in pushed_items},
            {"tmdb:1", "tt9999999"},
        )
        self.assertEqual(conn.stremio_pushed_library_ids, ["tmdb:1"])

    async def test_full_push_retries_only_previously_managed_removals(self) -> None:
        conn = SimpleNamespace(
            id=4,
            user_id=7,
            type="nuvio",
            url="https://api.nuvio.tv",
            token="old-refresh",
            server_user_id="1",
            push_collection=True,
            push_watched=False,
            push_playback=False,
            stremio_pushed_library_ids=["tmdb:1", "tmdb:gone"],
        )
        user_settings = SimpleNamespace(tmdb_api_key="tmdb-key")
        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    _Result(scalars=[99]),
                    _Result(scalars=[conn]),
                    *_approved_stream_gate_results(7),
                    _Result(scalars=[user_settings]),
                    None,
                    None,
                ]
            ),
            commit=AsyncMock(),
            refresh=AsyncMock(),
            get=AsyncMock(return_value=SimpleNamespace(approved=True, snapshot={})),
        )
        pushed_items: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/token":
                return httpx.Response(200, json={
                    "access_token": "access-token",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 3600,
                })
            if request.url.path.endswith("/sync_pull_library"):
                return httpx.Response(200, json=[
                    {"content_id": "tmdb:1", "content_type": "movie", "name": "Local movie"},
                    {"content_id": "tmdb:gone", "content_type": "movie", "name": "Removed locally"},
                    {"content_id": "tt9999999", "content_type": "movie", "name": "Remote only"},
                ])
            if request.url.path.endswith("/sync_push_library"):
                pushed_items.extend(json.loads(request.content)["p_items"])
                return httpx.Response(204)
            return httpx.Response(404, json={"message": "unexpected request"})

        transport = httpx.MockTransport(handler)
        with (
            patch("routers.sync.async_sessionmaker", lambda *args, **kwargs: (lambda: _SessionCM(db))),
            patch("core.nuvio_projection.build_library_items", AsyncMock(return_value=[
                {"content_id": "tmdb:1", "content_type": "movie", "name": "Local movie"},
            ])),
            patch.object(
                nuvio.httpx,
                "AsyncClient",
                side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
            ),
        ):
            await _run_full_push(user_id=7, connection_id=4, job_id=99)

        self.assertEqual(
            {item["content_id"] for item in pushed_items},
            {"tmdb:1", "tt9999999"},
        )
        self.assertEqual(conn.stremio_pushed_library_ids, ["tmdb:1"])


if __name__ == "__main__":
    unittest.main()
