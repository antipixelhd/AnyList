"""Conservative remote change checks. Unknown always falls back to a pull.

Markers are stored on successful pull jobs, scoped to account identity and pull
configuration. They are fetched BEFORE importing so concurrent remote changes
are not accidentally acknowledged. Never advance them from an outbound write.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
import logging
import math

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import set_committed_value

from core import mdblist, nuvio, simkl, stremio, trakt, trakt_auth
from models.sync import SyncJob, SyncStatus
from models.connections import MediaServerConnection

logger = logging.getLogger(__name__)


class ChangeState(str, Enum):
    unchanged = "unchanged"
    changed = "changed"
    unknown = "unknown"


@dataclass(frozen=True)
class ChangeCheck:
    state: ChangeState
    checkpoint: dict | None = None


def activity_marker(payload: object) -> str | None:
    """Only an explicit, valid aggregate activity clock establishes coverage."""
    if not isinstance(payload, dict):
        return None
    value = payload.get("all")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo is not None else None


def signature(provider: str, settings, conn=None) -> str:
    if conn is not None:
        identity = [conn.url, conn.provider_account_id, conn.server_user_id,
                    conn.identity_version, conn.token if provider == "stremio" else None]
        flags = {name: getattr(conn, name, False) for name in (
            "sync_collection", "sync_watched", "sync_playback", "sync_ratings", "plex_sync_watchlist")}
    else:
        identity = [getattr(settings, f"{provider}_access_token", None),
                    getattr(settings, f"{provider}_api_key", None),
                    getattr(settings, f"{provider}_client_id", None)]
        flags = {name: getattr(settings, f"{provider}_{name}", False) for name in (
            "sync_watched", "sync_ratings", "sync_lists", "sync_watchlist", "sync_dropped", "watchlist_split")}
    # Only the digest is persisted, never credentials.
    return hashlib.sha256(json.dumps([provider, identity, flags], sort_keys=True).encode()).hexdigest()


async def check_provider_changes(db, *, user_id: int, provider: str, settings, conn=None) -> ChangeCheck:
    # Providers without complete verified coverage must take the normal path.
    if provider not in {"trakt", "simkl", "mdblist", "stremio", "nuvio"}:
        return ChangeCheck(ChangeState.unknown)
    # Trakt's aggregate clock is not sufficient for arbitrary custom list edits.
    if provider == "trakt" and getattr(settings, "trakt_sync_lists", False):
        return ChangeCheck(ChangeState.unknown)
    if provider == "stremio" and not conn.stremio_full_sync_done:
        return ChangeCheck(ChangeState.unknown)
    try:
        if provider == "trakt":
            token = await trakt_auth.ensure_valid_trakt_token(db, settings)
            async with httpx.AsyncClient(timeout=trakt.TIMEOUT) as client:
                response = await client.get(f"{trakt.TRAKT_BASE}/sync/last_activities",
                                            headers=trakt._headers(settings.trakt_client_id, token))
                response.raise_for_status()
                marker = activity_marker(response.json())
        elif provider == "simkl":
            async with httpx.AsyncClient(timeout=simkl.TIMEOUT) as client:
                response = await client.post(f"{simkl.SIMKL_BASE}/sync/activities",
                                             headers=simkl._headers(settings.simkl_client_id, settings.simkl_access_token))
                response.raise_for_status()
                marker = activity_marker(response.json())
        elif provider == "mdblist":
            marker = activity_marker(await mdblist._request("GET", "/sync/last_activities", settings.mdblist_api_key))
        elif provider == "nuvio":
            from core.connection_identity import refresh_stream_connection

            async def persist_refresh(session):
                updated = await db.execute(update(MediaServerConnection).where(
                    MediaServerConnection.id == conn.id,
                    MediaServerConnection.user_id == user_id,
                    MediaServerConnection.identity_version == conn.identity_version,
                    MediaServerConnection.token == conn.token,
                ).values(token=session.refresh_token))
                if updated.rowcount != 1:
                    raise RuntimeError("Nuvio connection changed during change check")
                await db.commit()
                set_committed_value(conn, "token", session.refresh_token)

            async with nuvio.connection_lock(conn.id):
                await refresh_stream_connection(db, conn)
                marker = await nuvio.pull_change_marker(conn.url, conn.token, int(conn.server_user_id or 1),
                                                       on_refresh=persist_refresh)
        else:
            meta = await stremio.datastore_meta(conn.token)
            # Comparing the full ID/clock vector also detects removed records;
            # max(timestamp) alone would miss some deletions.
            if any(not isinstance(row[1], (int, float)) or isinstance(row[1], bool)
                   or not math.isfinite(row[1]) or row[1] < 0 for row in meta):
                return ChangeCheck(ChangeState.unknown)
            marker = sorted([[str(row[0]), row[1]] for row in meta])
        if marker is None:
            return ChangeCheck(ChangeState.unknown)
        # Settings blobs/metadata lists can be large. Persist an equality digest.
        marker_digest = hashlib.sha256(json.dumps(marker, sort_keys=True).encode()).hexdigest()
        checkpoint = {"signature": signature(provider, settings, conn), "marker": marker_digest}
        previous = (await db.execute(select(SyncJob).where(
            SyncJob.user_id == user_id,
            SyncJob.source == provider,
            SyncJob.connection_id == (conn.id if conn else None),
            SyncJob.job_type == "pull",
            SyncJob.status.in_([SyncStatus.completed, SyncStatus.failed, SyncStatus.cancelled]),
        ).order_by(SyncJob.id.desc()).limit(1))).scalar_one_or_none()
        saved = (previous.stats or {}).get("provider_checkpoint") if previous else None
        state = ChangeState.unchanged if saved == checkpoint else ChangeState.changed
        return ChangeCheck(state, checkpoint)
    except Exception as error:
        # Avoid logging response bodies or credentials from authentication errors.
        logger.info("Change check unavailable for %s (%s); pulling normally", provider, type(error).__name__)
        await db.rollback()
        return ChangeCheck(ChangeState.unknown)
