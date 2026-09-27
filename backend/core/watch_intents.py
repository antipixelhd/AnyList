"""Durable watched-state delivery for Nuvio and Stremio connections."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy import select

from models.connections import MediaServerConnection
from models.events import WatchEvent
from models.media import Media
from models.show import Show
from models.tracking import StreamBaseline
from models.watch_intent import WatchIntent

logger = logging.getLogger(__name__)
WatchWriter = Callable[[WatchIntent, bool, datetime | None], Awaitable[None]]


def _refresh_intent(intent: WatchIntent, desired_watched: bool) -> None:
    """Coalesce a newer canonical state onto the connection/media row."""
    intent.desired_watched = desired_watched
    intent.state = "pending"
    intent.attempts = 0
    intent.last_error = None


async def queue_watch_intents(
    db,
    user_id: int,
    media_ids: set[int] | list[int],
    *,
    exclude_connection_id: int | None = None,
    exclude_connection_ids: set[int] | None = None,
) -> int:
    """Queue the canonical watched state for each eligible stream connection.

    The row is keyed by connection and media, so quick changes coalesce. The
    dispatcher re-reads WatchEvent before writing; no play date is copied into
    this operational queue.
    """
    ids = {int(media_id) for media_id in media_ids if media_id is not None}
    if not ids:
        return 0

    from models.users import User
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())

    watched_ids = set((await db.execute(select(WatchEvent.media_id).where(
        WatchEvent.user_id == user_id,
        WatchEvent.media_id.in_(ids),
        WatchEvent.completed.is_(True),
    ).distinct())).scalars())
    filters = [
        MediaServerConnection.user_id == user_id,
        MediaServerConnection.type.in_(("nuvio", "stremio")),
        MediaServerConnection.push_watched.is_(True),
    ]
    excluded = set(exclude_connection_ids or ())
    if exclude_connection_id is not None:
        excluded.add(exclude_connection_id)
    if excluded:
        filters.append(MediaServerConnection.id.not_in(excluded))
    connections = (await db.execute(select(MediaServerConnection).where(*filters))).scalars().all()

    queued = 0
    for conn in connections:
        for media_id in ids:
            intent = (await db.execute(select(WatchIntent).where(
                WatchIntent.connection_id == conn.id,
                WatchIntent.media_id == media_id,
            ).with_for_update())).scalar_one_or_none()
            desired_watched = media_id in watched_ids
            if intent is None:
                db.add(WatchIntent(
                    user_id=user_id,
                    connection_id=conn.id,
                    media_id=media_id,
                    desired_watched=desired_watched,
                    state="pending",
                ))
            else:
                _refresh_intent(intent, desired_watched)
            queued += 1
    return queued


async def _current_watch_event(db, user_id: int, media_id: int) -> WatchEvent | None:
    return (await db.execute(select(WatchEvent).where(
        WatchEvent.user_id == user_id,
        WatchEvent.media_id == media_id,
        WatchEvent.completed.is_(True),
    ).order_by(WatchEvent.watched_at.desc().nulls_last(), WatchEvent.id.desc()).limit(1))).scalar_one_or_none()


def _valid_imdb_id(value: object) -> str | None:
    candidate = str(value or "").strip()
    return candidate if candidate.startswith("tt") and candidate[2:].isdigit() else None


def _content_id_for_connection(conn, baseline, media: Media, show: Show | None) -> str | None:
    entity = show if media.media_type.value == "episode" else media
    target_tmdb = getattr(entity, "tmdb_id", None)
    mappings = (baseline.snapshot or {}).get("mappings", {}) if baseline else {}
    matching = [str(key) for key, value in mappings.items() if target_tmdb is not None and str(value) == str(target_tmdb)]

    data = getattr(entity, "tmdb_data", None) or {}
    external = data.get("external_ids") if isinstance(data, dict) else {}
    direct = _valid_imdb_id(
        getattr(entity, "imdb_id", None)
        or (data.get("imdb_id") if isinstance(data, dict) else None)
        or (external.get("imdb_id") if isinstance(external, dict) else None)
    )
    if direct and direct in matching:
        return direct
    imdb_mapping = next((key for key in matching if _valid_imdb_id(key)), None)
    return imdb_mapping or (matching[0] if matching else None) or direct


async def _write_provider_watch_state(
    db,
    conn: MediaServerConnection,
    media: Media,
    watched: bool,
    watched_at: datetime | None,
) -> None:
    if conn.type == "stremio":
        from models.users import UserSettings
        from routers.sync import _get_effective_tmdb_key, _push_stremio_connection

        settings = (await db.execute(select(UserSettings).where(
            UserSettings.user_id == conn.user_id,
        ))).scalar_one_or_none()
        api_key = await _get_effective_tmdb_key(db, settings)
        await _push_stremio_connection(
            db,
            conn,
            conn.user_id,
            api_key=api_key,
            watch_overrides={media.id: watched},
            watch_only=True,
        )
        return

    if conn.type != "nuvio":
        return

    baseline = await db.get(StreamBaseline, conn.id)
    show = await db.get(Show, media.show_id) if media.show_id is not None else None
    from models.users import UserSettings
    from routers.sync import (
        _ensure_nuvio_imdb_ids,
        _get_effective_tmdb_key,
        _nuvio_watched_item,
    )

    settings = (await db.execute(select(UserSettings).where(
        UserSettings.user_id == conn.user_id,
    ))).scalar_one_or_none()
    api_key = await _get_effective_tmdb_key(db, settings)
    await _ensure_nuvio_imdb_ids([media], {show.id: show} if show else {}, api_key)
    payload = _nuvio_watched_item(
        media,
        watched_at,
        show,
        include_unknown_date=True,
    )
    if payload is None:
        raise ValueError("content_id_unresolved")
    mapped_id = _content_id_for_connection(conn, baseline, media, show)
    if mapped_id:
        payload["content_id"] = mapped_id
    if media.media_type.value == "episode" and (
        media.season_number is None or media.episode_number is None
    ):
        raise ValueError("episode_position_unresolved")
    key = {field: payload[field] for field in ("content_id", "season", "episode") if field in payload}

    import core.nuvio as nuvio
    async def persist_refresh(session) -> None:
        from db import AsyncSessionLocal
        from sqlalchemy import update
        from sqlalchemy.orm.attributes import set_committed_value
        async with AsyncSessionLocal() as token_db:
            await token_db.execute(update(MediaServerConnection).where(
                MediaServerConnection.id == conn.id,
            ).values(token=session.refresh_token))
            await token_db.commit()
        set_committed_value(conn, "token", session.refresh_token)

    async with nuvio.connection_lock(conn.id):
        await db.refresh(conn)
        if watched:
            await nuvio.push_watched_items(
                conn.url, conn.token, nuvio.parse_profile_id(conn.server_user_id),
                [payload], on_refresh=persist_refresh,
            )
        else:
            await nuvio.delete_watched_items(
                conn.url, conn.token, nuvio.parse_profile_id(conn.server_user_id),
                [key], on_refresh=persist_refresh,
            )


async def _attempt_watch_write(intent: WatchIntent, watched: bool, watched_at: datetime | None, writer: WatchWriter) -> bool:
    intent.attempts += 1
    try:
        await writer(intent, watched, watched_at)
    except Exception as error:
        # Exception messages can contain credentials or remote response bodies.
        intent.last_error = type(error).__name__
        return False
    intent.last_error = None
    return True


async def dispatch_watch_intents(db, user_id: int, *, writer: WatchWriter | None = None) -> None:
    """Retry pending watch writes, always projecting the latest local truth."""
    from core.pull_cycle import is_active
    if is_active(user_id):
        return

    from models.users import User
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    intents = (await db.execute(select(WatchIntent).where(
        WatchIntent.user_id == user_id,
        WatchIntent.state == "pending",
    ).order_by(WatchIntent.id).limit(100).with_for_update(skip_locked=True))).scalars().all()
    for intent in intents:
        conn = await db.get(MediaServerConnection, intent.connection_id)
        if not conn or conn.type not in ("nuvio", "stremio") or not conn.push_watched:
            intent.state = "cancelled"
            intent.last_error = None
            continue
        media = await db.get(Media, intent.media_id)
        if not media:
            intent.state = "cancelled"
            intent.last_error = None
            continue
        event = await _current_watch_event(db, user_id, intent.media_id)
        watched = event is not None
        intent.desired_watched = watched

        from core.tracking_snapshot import require_stream_reconciliation
        try:
            await require_stream_reconciliation(db, conn)
        except HTTPException:
            # Keep the intent queued until the initial merge/conflicts are resolved.
            continue

        if writer is None:
            async def perform_write(row, current_watched, watched_at, *, _db=db, _conn=conn, _media=media):
                await _write_provider_watch_state(_db, _conn, _media, current_watched, watched_at)
        else:
            perform_write = writer

        succeeded = await _attempt_watch_write(intent, watched, event.watched_at if event else None, perform_write)
        if succeeded:
            intent.state = "applied"
        else:
            logger.info("Watch intent remains pending for connection %s media %s after %s", conn.id, media.id, intent.last_error)
    await db.commit()
