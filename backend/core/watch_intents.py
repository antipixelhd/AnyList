"""Durable watched-state delivery for Nuvio and Stremio connections."""
from __future__ import annotations

from core import settings_store

from core import stremio_delivery
from core import nuvio_payloads, nuvio_projection
from core.connection_identity import refresh_stream_connection
from datetime import datetime
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
    from core.sync_delivery_targets import connection_clause
    filters.append(connection_clause(MediaServerConnection.id))
    excluded = set(exclude_connection_ids or ())
    if exclude_connection_id is not None:
        excluded.add(exclude_connection_id)
    if excluded:
        filters.append(MediaServerConnection.id.not_in(excluded))
    connections = (await db.execute(select(MediaServerConnection).where(*filters))).scalars().all()

    queued = 0
    for conn in connections:
        baseline = (await db.execute(select(StreamBaseline).where(
            StreamBaseline.connection_id == conn.id,
        ).execution_options(populate_existing=True))).scalar_one_or_none()
        if baseline:
            snapshot = dict(baseline.snapshot or {})
            suppressed = {
                int(media_id)
                for media_id in snapshot.get("playback_clear_watch_visibility_suppressed", [])
                if str(media_id).isdigit()
            }
            if suppressed & ids:
                snapshot["playback_clear_watch_visibility_suppressed"] = sorted(suppressed - ids)
                baseline.snapshot = snapshot
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


async def _write_nuvio_watch_batch(db, conn, entries):
    from core import nuvio
    from db import AsyncSessionLocal
    from models.users import UserSettings
    from sqlalchemy import update
    from sqlalchemy.orm.attributes import set_committed_value

    baseline = await db.get(StreamBaseline, conn.id)
    show_ids = {media.show_id for _, media, _ in entries if media.show_id is not None}
    shows = {show_id: await db.get(Show, show_id) for show_id in show_ids}
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == conn.user_id))).scalar_one_or_none()
    api_key = await settings_store.get_effective_tmdb_key(db, settings)
    await nuvio_projection.ensure_imdb_ids([media for _, media, _ in entries],
        {key: show for key, show in shows.items() if show is not None}, api_key)
    additions, removals, prepared, errors = [], [], [], {}
    for intent, media, watched_at in entries:
        show = shows.get(media.show_id)
        payload = nuvio_payloads.watched_item(media, watched_at, show, include_unknown_date=True)
        if payload is None:
            errors[intent.id] = 'content_id_unresolved'
            continue
        if media.media_type.value == 'episode' and (media.season_number is None or media.episode_number is None):
            errors[intent.id] = 'episode_position_unresolved'
            continue
        if mapped_id := nuvio_payloads.content_id_for_baseline(baseline, media, show):
            payload['content_id'] = mapped_id
        if intent.desired_watched:
            if not nuvio._has_numeric_watched_at(payload):
                errors[intent.id] = 'NuvioAPIError'
                continue
            additions.append(payload)
        else:
            removals.append({field: payload[field] for field in ('content_id', 'season', 'episode') if field in payload})
        prepared.append((intent, payload, getattr(show or media, 'tmdb_id', None)))

    if not prepared:
        return errors

    async def persist_refresh(session):
        async with AsyncSessionLocal() as token_db:
            await token_db.execute(update(MediaServerConnection).where(
                MediaServerConnection.id == conn.id,
            ).values(token=session.refresh_token))
            await token_db.commit()
        set_committed_value(conn, 'token', session.refresh_token)

    async with nuvio.connection_lock(conn.id):
        await refresh_stream_connection(db, conn)
        remote = await nuvio.sync_watched_delta(conn.url, conn.token, nuvio.parse_profile_id(conn.server_user_id),
            additions, removals, on_refresh=persist_refresh)

    remote_keys = {nuvio._watched_history_key(row) for row in remote}
    dated_keys = {nuvio._watched_history_key(row) for row in remote if nuvio._has_numeric_watched_at(row)}
    snapshot = dict(baseline.snapshot or {}) if baseline else {}
    mappings = dict(snapshot.get('mappings', {}))
    for intent, payload, tmdb_id in prepared:
        key = nuvio._watched_history_key(payload)
        confirmed = key in dated_keys if intent.desired_watched else key not in remote_keys
        if not confirmed:
            errors[intent.id] = 'NuvioAPIError'
        elif tmdb_id is not None:
            mappings[str(payload['content_id'])] = tmdb_id
    if baseline:
        baseline.snapshot = {**snapshot, 'mappings': mappings}
    return errors


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
    ).order_by(
        # Give never-attempted or freshly re-queued local changes a chance
        # before repeatedly failing provider writes. Without this, the oldest
        # 100 failures can occupy every batch and starve newer edits forever.
        WatchIntent.attempts.asc(),
        WatchIntent.updated_at.desc(),
        WatchIntent.id.desc(),
    ).limit(100).with_for_update(skip_locked=True))).scalars().all()
    processed = set()
    nuvio_visibility_batches = {}
    for intent in intents:
        if intent.id in processed:
            continue
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
        from core.tracking_snapshot import require_stream_reconciliation
        try:
            await require_stream_reconciliation(db, conn)
        except HTTPException:
            # Keep the intent queued until the initial merge/conflicts are resolved.
            continue

        if writer is None and conn.type == 'stremio':
            # Episodes share one remote library row/bitfield. Write the whole
            # pending delta together so successive reads cannot overwrite an
            # earlier episode write with an older provider snapshot.
            batch = [row for row in intents if row.connection_id == conn.id]
            overrides = {}
            for row in batch:
                processed.add(row.id)
                target = await db.get(Media, row.media_id)
                if target is None:
                    row.state = 'cancelled'
                    row.last_error = None
                    continue
                row.desired_watched = await _current_watch_event(db, user_id, row.media_id) is not None
                overrides[row.media_id] = row.desired_watched
                row.attempts += 1
            try:
                from models.users import UserSettings
                settings = (await db.execute(select(UserSettings).where(
                    UserSettings.user_id == user_id))).scalar_one_or_none()
                await stremio_delivery.push_connection(db, conn, user_id,
                    api_key=await settings_store.get_effective_tmdb_key(db, settings),
                    watch_overrides=overrides, watch_only=True)
            except Exception as error:
                for row in batch:
                    if row.media_id in overrides:
                        row.last_error = type(error).__name__
            else:
                for row in batch:
                    if row.media_id in overrides:
                        row.state = 'applied'
                        row.last_error = None
            continue

        if writer is not None:
            event = await _current_watch_event(db, user_id, intent.media_id)
            intent.desired_watched = event is not None
            if await _attempt_watch_write(intent, intent.desired_watched,
                    (event.watched_at or event.created_at) if event else None, writer):
                intent.state = 'applied'
            continue

        batch = []
        for row in intents:
            if row.connection_id != conn.id:
                continue
            processed.add(row.id)
            target = await db.get(Media, row.media_id)
            if target is None:
                row.state = 'cancelled'
                row.last_error = None
                continue
            current = await _current_watch_event(db, user_id, row.media_id)
            row.desired_watched = current is not None
            row.attempts += 1
            batch.append((row, target, (current.watched_at or current.created_at) if current else None))
        try:
            errors = await _write_nuvio_watch_batch(db, conn, batch)
        except Exception as error:
            errors = {row.id: type(error).__name__ for row, _, _ in batch}
        baseline = await db.get(StreamBaseline, conn.id)
        suppressed = set((baseline.snapshot or {}).get('playback_clear_watch_visibility_suppressed', [])) if baseline else set()
        for row, _, _ in batch:
            row.last_error = errors.get(row.id)
            if row.last_error:
                logger.info("Watch intent remains pending for connection %s media %s after %s", conn.id, row.media_id, row.last_error)
                continue
            row.state = 'applied'
            if row.media_id not in suppressed and str(row.media_id) not in suppressed:
                nuvio_visibility_batches.setdefault(conn.id, []).append(row)
    for connection_id, batch in nuvio_visibility_batches.items():
        conn = await db.get(MediaServerConnection, connection_id)
        records = []
        from core.nuvio_visibility import sync_next_up_visibility
        baseline = await db.get(StreamBaseline, conn.id)
        mappings = (baseline.snapshot or {}).get('mappings', {}) if baseline else {}
        for intent in batch:
            media = await db.get(Media, intent.media_id)
            show = await db.get(Show, media.show_id) if media.show_id else None
            entity = show or media
            keys = {str(key) for key, value in mappings.items()
                if entity.tmdb_id is not None and str(value) == str(entity.tmdb_id)}
            for key in (nuvio_payloads.content_id_for_baseline(baseline, media, show), nuvio_payloads.imdb_id(entity)):
                if key:
                    keys.add(key)
            for key in keys:
                records.append({'content_id': key,
                    'content_type': 'series' if show else media.media_type.value})
        try:
            await sync_next_up_visibility(db, conn, records)
        except Exception as error:
            for intent in batch:
                intent.state = 'pending'
                intent.last_error = type(error).__name__
    await db.commit()
