import base64
import asyncio
import copy
import logging
from datetime import datetime, timezone
import zlib
from typing import Any
from core.timestamps import milliseconds

import httpx


DEFAULT_URL = "https://api.strem.io"
API_URL = f"{DEFAULT_URL}/api"
LINK_URL = "https://link.stremio.com/api/v2"
CINEMETA_URL = "https://v3-cinemeta.strem.io"
LIBRARY_COLLECTION = "libraryItem"
_TIMEOUT = 30.0
_CINEMETA_CONCURRENCY = 5
logger = logging.getLogger(__name__)
_WRITE_BATCH_SIZE = 100
_connection_locks: dict[int, asyncio.Lock] = {}


class StremioAPIError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None):
        super().__init__(message)
        self.code = code


def connection_lock(connection_id: int) -> asyncio.Lock:
    """Serialize provider pulls, pushes, and clear operations per connection."""
    return _connection_locks.setdefault(connection_id, asyncio.Lock())


def _result(payload: Any, operation: str) -> Any:
    if not isinstance(payload, dict):
        raise StremioAPIError(f"Stremio {operation} returned an invalid response")
    error = payload.get("error")
    if error:
        if isinstance(error, dict):
            message = str(error.get("message") or "Unknown API error")
            code = error.get("code")
            try:
                parsed_code = int(code) if code is not None else None
            except (TypeError, ValueError):
                parsed_code = None
            raise StremioAPIError(f"Stremio {operation} failed: {message}", code=parsed_code)
        raise StremioAPIError(f"Stremio {operation} failed: {error}")
    if "result" not in payload:
        raise StremioAPIError(f"Stremio {operation} returned no result")
    return payload["result"]


async def _json(response: httpx.Response, operation: str) -> Any:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise StremioAPIError(
            f"Stremio {operation} failed ({response.status_code})"
        ) from exc
    try:
        return response.json()
    except ValueError as exc:
        raise StremioAPIError(
            f"Stremio {operation} returned invalid JSON"
        ) from exc


async def create_link_code() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        response = await client.get(f"{LINK_URL}/create", params={"type": "Create"})
    result = _result(await _json(response, "link creation"), "link creation")
    if not isinstance(result, dict) or not all(result.get(key) for key in ("code", "link", "qrcode")):
        raise StremioAPIError("Stremio link creation returned incomplete data")
    return result


async def read_link_code(code: str) -> str | None:
    normalized = code.strip().upper()
    if not normalized:
        raise StremioAPIError("Stremio link code is required")
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        response = await client.get(
            f"{LINK_URL}/read",
            params={"type": "Read", "code": normalized},
        )
    payload = await _json(response, "link authorization")
    try:
        result = _result(payload, "link authorization")
    except StremioAPIError as exc:
        if exc.code == 101:
            return None
        raise
    auth_key = result.get("authKey") if isinstance(result, dict) else None
    if not auth_key:
        raise StremioAPIError("Stremio link authorization returned no auth key")
    return str(auth_key)


async def _api_request(path: str, body: dict[str, Any], operation: str) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        response = await client.post(
            f"{API_URL}/{path}",
            headers={"Content-Type": "application/json"},
            json=body,
        )
    return _result(await _json(response, operation), operation)


async def get_user(auth_key: str) -> dict[str, Any]:
    result = await _api_request(
        "getUser",
        {"type": "GetUser", "authKey": auth_key},
        "account validation",
    )
    if not isinstance(result, dict) or not result.get("_id"):
        raise StremioAPIError("Stremio account validation returned incomplete data")
    return result


async def validate_auth_key(auth_key: str) -> dict[str, Any]:
    return await get_user(auth_key)


async def datastore_meta(auth_key: str) -> list[list[Any]]:
    result = await _api_request(
        "datastoreMeta",
        {"authKey": auth_key, "collection": LIBRARY_COLLECTION},
        "library metadata pull",
    )
    if not isinstance(result, list):
        raise StremioAPIError('Stremio library metadata pull returned an invalid collection')
    if any(not isinstance(row, list) or len(row) < 2 or not row[0] for row in result):
        raise StremioAPIError('Stremio library metadata pull returned incomplete rows')
    return result


async def datastore_get(
    auth_key: str,
    *,
    ids: list[str] | None = None,
    all_items: bool = False,
    allow_missing: bool = False,
) -> list[dict[str, Any]]:
    result = await _api_request(
        "datastoreGet",
        {
            "authKey": auth_key,
            "collection": LIBRARY_COLLECTION,
            "ids": ids or [],
            "all": all_items,
        },
        "library pull",
    )
    if not isinstance(result, list) or any(not isinstance(item, dict) or not item.get('_id') for item in result):
        raise StremioAPIError('Stremio library pull returned an incomplete collection')
    if ids and not all_items and not allow_missing and set(ids) - {str(item['_id']) for item in result}:
        raise StremioAPIError('Stremio incremental pull omitted requested records')
    return result


async def datastore_put(auth_key: str, changes: list[dict[str, Any]]) -> None:
    if not changes:
        return
    result = await _api_request(
        "datastorePut",
        {
            "authKey": auth_key,
            "collection": LIBRARY_COLLECTION,
            "changes": changes,
        },
        "library push",
    )
    if not isinstance(result, dict) or result.get("success") is not True:
        raise StremioAPIError("Stremio library push was not acknowledged")


async def clear_datastore_data(
    auth_key: str,
    *,
    collection: bool = False,
    watched: bool = False,
    playback: bool = False,
) -> dict[str, int]:
    """Clear selected Stremio library-item fields without touching others.

    Stremio stores collection membership and viewing state in the same
    ``libraryItem`` record. Collection removal is represented by its normal
    tombstone, while watched and playback state are reset independently.
    """
    if not (collection or watched or playback):
        raise StremioAPIError("Select at least one Stremio data category to clear")

    remote_items = await datastore_get(auth_key, all_items=True)
    by_id: dict[str, dict[str, Any]] = {}
    for remote in remote_items:
        if not isinstance(remote, dict):
            raise StremioAPIError("Stremio returned a malformed library item; refusing an unverifiable clear")
        content_id = remote.get("_id")
        if not isinstance(content_id, str) or not content_id.strip() or content_id in by_id:
            raise StremioAPIError("Stremio returned a missing or duplicate library id; refusing an unverifiable clear")
        if remote.get("state") is not None and not isinstance(remote.get("state"), dict):
            raise StremioAPIError("Stremio returned malformed viewing state; refusing an unsafe clear")
        by_id[content_id] = remote

    now = milliseconds(datetime.now(timezone.utc)).isoformat(timespec='milliseconds').replace("+00:00", "Z")
    changes: dict[str, dict[str, Any]] = {}
    if watched or playback:
        for content_id, remote in by_id.items():
            candidate = copy.deepcopy(remote)
            state = dict(candidate.get("state") or {})
            if watched:
                state.update({
                    "lastWatched": None,
                    "timeWatched": 0,
                    "overallTimeWatched": 0,
                    "timesWatched": 0,
                    "flaggedWatched": 0,
                    "watched": None,
                })
            if playback:
                state.update({"timeOffset": 0, "video_id": None, "duration": 0})
            candidate["state"] = state
            candidate["_mtime"] = now
            changes[content_id] = candidate

        view_changes = list(changes.values())
        for offset in range(0, len(view_changes), _WRITE_BATCH_SIZE):
            await datastore_put(auth_key, view_changes[offset : offset + _WRITE_BATCH_SIZE])
        for offset in range(0, len(view_changes), _WRITE_BATCH_SIZE):
            batch = [item["_id"] for item in view_changes[offset : offset + _WRITE_BATCH_SIZE]]
            confirmed_rows = await datastore_get(auth_key, ids=batch, allow_missing=True)
            confirmed = {
                str(item.get("_id")): item for item in confirmed_rows
                if isinstance(item, dict) and isinstance(item.get("_id"), str)
            }
            for content_id in batch:
                item = confirmed.get(content_id)
                if item is None:
                    raise StremioAPIError("Stremio did not confirm cleared viewing data")
                state = item.get("state")
                if not isinstance(state, dict):
                    raise StremioAPIError("Stremio did not confirm cleared viewing data")
                if watched and any(
                    state.get(key) not in (None, 0, "0")
                    for key in ("timesWatched", "flaggedWatched", "timeWatched", "overallTimeWatched")
                ):
                    raise StremioAPIError("Stremio did not confirm watched-history removal")
                if watched and (state.get("lastWatched") is not None or state.get("watched") is not None):
                    raise StremioAPIError("Stremio did not confirm watched-history removal")
                if playback and any(
                    state.get(key) not in (None, 0, "0")
                    for key in ("timeOffset", "duration")
                ):
                    raise StremioAPIError("Stremio did not confirm playback removal")
                if playback and state.get("video_id") is not None:
                    raise StremioAPIError("Stremio did not confirm playback removal")
                changes[content_id] = item

    if collection:
        collection_changes: list[dict[str, Any]] = []
        for content_id, remote in changes.items() if changes else by_id.items():
            candidate = copy.deepcopy(remote)
            candidate["removed"] = True
            candidate["temp"] = False
            candidate["_mtime"] = now
            collection_changes.append(candidate)
        for offset in range(0, len(collection_changes), _WRITE_BATCH_SIZE):
            await datastore_put(auth_key, collection_changes[offset : offset + _WRITE_BATCH_SIZE])
        for offset in range(0, len(collection_changes), _WRITE_BATCH_SIZE):
            batch = [item["_id"] for item in collection_changes[offset : offset + _WRITE_BATCH_SIZE]]
            confirmed_rows = await datastore_get(auth_key, ids=batch, allow_missing=True)
            confirmed = {
                str(item.get("_id")): item for item in confirmed_rows
                if isinstance(item, dict) and isinstance(item.get("_id"), str)
            }
            for content_id in batch:
                item = confirmed.get(content_id)
                # Stremio may remove a tombstoned record from later reads. The
                # viewing fields were confirmed above before issuing tombstones.
                if item is not None and (not item.get("removed") or item.get("temp")):
                    raise StremioAPIError("Stremio did not confirm collection removal")

    # Catch items introduced while the destructive writes were in flight. The
    # result is an error (and the caller retains its clear guard), never a false
    # claim that an incomplete point-in-time set was emptied.
    remaining = await datastore_get(auth_key, all_items=True)
    for item in remaining:
        if not isinstance(item, dict) or not isinstance(item.get("_id"), str) or not item.get("_id").strip():
            raise StremioAPIError("Stremio returned malformed data during clear verification")
        if collection and not item.get("removed"):
            raise StremioAPIError("Stremio did not confirm collection removal")
        if (watched or playback) and item.get("state") is not None and not isinstance(item.get("state"), dict):
            raise StremioAPIError("Stremio returned malformed viewing state during clear verification")
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        if watched and (
            state.get("lastWatched") is not None
            or state.get("watched") is not None
            or any(state.get(key) not in (None, 0, "0") for key in (
                "timesWatched", "flaggedWatched", "timeWatched", "overallTimeWatched",
            ))
        ):
            raise StremioAPIError("Stremio did not confirm watched-history removal")
        if playback and (
            state.get("video_id") is not None
            or any(state.get(key) not in (None, 0, "0") for key in ("timeOffset", "duration"))
        ):
            raise StremioAPIError("Stremio did not confirm playback removal")

    return {
        "collection": len(remote_items) if collection else 0,
        "watched": len(remote_items) if watched else 0,
        "playback": len(remote_items) if playback else 0,
    }


async def logout(auth_key: str) -> None:
    await _api_request(
        "logout",
        {"type": "Logout", "authKey": auth_key},
        "logout",
    )


async def get_cinemeta_series(imdb_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        response = await client.get(f"{CINEMETA_URL}/meta/series/{imdb_id}.json")
    payload = await _json(response, "Cinemeta lookup")
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        raise StremioAPIError(f"Cinemeta series {imdb_id} was not found")
    return meta


def decode_watched_bitfield(serialized: str | None, video_ids: list[str]) -> set[str]:
    if not serialized or not video_ids:
        return set()
    try:
        anchor_video_id, anchor_length_raw, packed = serialized.rsplit(":", 2)
        anchor_length = int(anchor_length_raw)
        anchor_index = video_ids.index(anchor_video_id)
        previous = zlib.decompress(base64.b64decode(packed))
    except (ValueError, TypeError, zlib.error, base64.binascii.Error):
        return set()
    if anchor_length <= 0:
        return set()

    offset = (anchor_length - 1) - anchor_index
    watched: set[str] = set()
    for index, video_id in enumerate(video_ids):
        previous_index = index + offset
        if previous_index < 0 or previous_index >= anchor_length:
            continue
        byte_index, bit_index = divmod(previous_index, 8)
        if byte_index < len(previous) and previous[byte_index] & (1 << bit_index):
            watched.add(video_id)
    return watched


def encode_watched_bitfield(watched_ids: set[str], video_ids: list[str]) -> str | None:
    if not video_ids:
        return None
    values = bytearray((len(video_ids) + 7) // 8)
    last_watched_index = 0
    for index, video_id in enumerate(video_ids):
        if video_id in watched_ids:
            byte_index, bit_index = divmod(index, 8)
            values[byte_index] |= 1 << bit_index
            last_watched_index = index
    packed = base64.b64encode(zlib.compress(bytes(values))).decode("ascii")
    return f"{video_ids[last_watched_index]}:{last_watched_index + 1}:{packed}"


async def get_series_metadata(content_ids: set[str]) -> dict[str, dict]:
    semaphore = asyncio.Semaphore(_CINEMETA_CONCURRENCY)

    async def fetch(content_id: str) -> tuple[str, dict | None]:
        try:
            async with semaphore:
                return content_id, await get_cinemeta_series(content_id)
        except StremioAPIError:
            logger.warning("Cinemeta metadata unavailable for %s", content_id)
            return content_id, None

    results = await asyncio.gather(*(fetch(content_id) for content_id in content_ids))
    return {
        content_id: metadata
        for content_id, metadata in results
        if metadata is not None
    }
