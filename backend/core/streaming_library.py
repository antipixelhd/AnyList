"""Deliver explicit Library changes to each opted-in Stremio/Nuvio account.

Observed collection files describe provider state. The intent is kept separately
so a failed write, or another collection source, cannot silently reverse it.
"""

import asyncio
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import nuvio, stremio
from core.tracking_snapshot import require_stream_reconciliation
from models import Collection, CollectionFile, Media, MediaServerConnection, User
from models.base import CollectionSource, MediaType
from models.streaming_library import StreamingLibraryDelivery, StreamingLibraryIntent


def _source(conn: MediaServerConnection) -> CollectionSource:
    return CollectionSource.stremio if conn.type == "stremio" else CollectionSource.nuvio


async def library_state(db: AsyncSession, user_id: int, media_id: int) -> dict:
    connections = (await db.execute(select(MediaServerConnection).where(
        MediaServerConnection.user_id == user_id,
        MediaServerConnection.type.in_(("stremio", "nuvio")),
    ).order_by(MediaServerConnection.id))).scalars().all()
    intent = (await db.execute(select(StreamingLibraryIntent).where(
        StreamingLibraryIntent.user_id == user_id,
        StreamingLibraryIntent.media_id == media_id,
    ))).scalar_one_or_none()
    deliveries = {
        row.connection_id: row
        for row in (await db.execute(select(StreamingLibraryDelivery).where(
            StreamingLibraryDelivery.intent_id == intent.id,
        ))).scalars().all()
    } if intent else {}
    memberships = set((await db.execute(select(CollectionFile.connection_id)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .where(Collection.user_id == user_id, Collection.media_id == media_id,
            CollectionFile.source.in_((CollectionSource.stremio, CollectionSource.nuvio)),
            CollectionFile.connection_id.isnot(None)))).scalars().all())
    rows = []
    for conn in connections:
        delivery = deliveries.get(conn.id)
        rows.append({
            "id": conn.id, "name": conn.name, "provider": conn.type,
            "push_enabled": conn.push_collection, "in_library": conn.id in memberships,
            "state": delivery.state if delivery and conn.push_collection else None,
            "error": delivery.last_error if delivery and conn.push_collection else None,
            "attempts": delivery.attempts if delivery and conn.push_collection else 0,
        })
    return {
        "in_library": bool(memberships),
        "desired": intent.desired if intent else bool(memberships),
        "available": any(conn.push_collection for conn in connections),
        "pending": any(row["state"] == "pending" for row in rows),
        "connections": rows,
    }


async def _set_manual_anchor(db: AsyncSession, user_id: int, media_id: int, desired: bool) -> None:
    collection = (await db.execute(select(Collection).where(
        Collection.user_id == user_id, Collection.media_id == media_id,
    ))).scalar_one_or_none()
    marker = f"anylist:{media_id}"
    if desired:
        if collection is None:
            collection = Collection(user_id=user_id, media_id=media_id)
            db.add(collection)
            await db.flush()
        present = (await db.execute(select(CollectionFile.id).where(
            CollectionFile.collection_id == collection.id,
            CollectionFile.source == CollectionSource.manual,
            CollectionFile.source_id == marker,
        ))).first()
        if not present:
            db.add(CollectionFile(collection_id=collection.id, source=CollectionSource.manual, source_id=marker))
        return
    if collection is None:
        return
    await db.execute(delete(CollectionFile).where(
        CollectionFile.collection_id == collection.id,
        CollectionFile.source == CollectionSource.manual,
        CollectionFile.source_id == marker,
    ))
    await db.flush()
    if not (await db.execute(select(CollectionFile.id).where(CollectionFile.collection_id == collection.id))).first():
        await db.delete(collection)


async def set_library_intent(db: AsyncSession, user_id: int, media_id: int, desired: bool) -> dict:
    media = await db.get(Media, media_id)
    if media is None or media.media_type not in (MediaType.movie, MediaType.series):
        raise HTTPException(404, "Title not found")
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    targets = (await db.execute(select(MediaServerConnection).where(
        MediaServerConnection.user_id == user_id,
        MediaServerConnection.type.in_(("stremio", "nuvio")),
        MediaServerConnection.push_collection.is_(True),
    ))).scalars().all()
    if not targets:
        raise HTTPException(409, "Enable Library push for a Stremio or Nuvio connection first")
    intent = (await db.execute(select(StreamingLibraryIntent).where(
        StreamingLibraryIntent.user_id == user_id,
        StreamingLibraryIntent.media_id == media_id,
    ))).scalar_one_or_none()
    if intent is None:
        intent = StreamingLibraryIntent(user_id=user_id, media_id=media_id, desired=desired)
        db.add(intent)
        await db.flush()
    changed = intent.desired != desired
    intent.desired = desired
    existing = {
        row.connection_id: row
        for row in (await db.execute(select(StreamingLibraryDelivery).where(
            StreamingLibraryDelivery.intent_id == intent.id,
        ))).scalars().all()
    }
    target_ids = {conn.id for conn in targets}
    for row in existing.values():
        if row.connection_id not in target_ids and row.state == "pending":
            row.state = "cancelled"
    for conn in targets:
        row = existing.get(conn.id)
        if row is None:
            db.add(StreamingLibraryDelivery(intent_id=intent.id, connection_id=conn.id, desired=desired))
        elif changed or row.desired != desired or row.state == "cancelled":
            row.desired, row.state, row.attempts, row.last_error = desired, "pending", 0, None
    await _set_manual_anchor(db, user_id, media_id, desired)
    await db.commit()  # The decision survives a failed provider request.
    await dispatch_library_deliveries(db, user_id, media_id)
    return await library_state(db, user_id, media_id)


async def _record_for_media(db: AsyncSession, user_id: int, media: Media) -> dict:
    from routers.sync import _ensure_nuvio_imdb_ids, _nuvio_library_item, _nuvio_library_content_id
    from routers.media import get_user_tmdb_key
    from models.show import Show

    show = None
    if media.media_type == MediaType.series and media.tmdb_id is not None:
        show = (await db.execute(select(Show).where(Show.tmdb_id == media.tmdb_id))).scalars().first()
    api_key = await get_user_tmdb_key(db, user_id)
    await _ensure_nuvio_imdb_ids([media], {}, api_key,
        {media.tmdb_id: show} if show and media.tmdb_id is not None else {})
    entity = show if show and _nuvio_library_content_id(media, show) else None
    item = _nuvio_library_item(media, datetime.now(timezone.utc), entity)
    if not item:
        raise ValueError("Missing IMDb ID for streaming library delivery")
    return item


async def _write_stremio(conn: MediaServerConnection, item: dict, desired: bool) -> None:
    from routers.sync import _stremio_new_library_item, _stremio_same_item, _stremio_push_locks

    content_id = item["content_id"]
    lock = _stremio_push_locks.setdefault(conn.id, asyncio.Lock())
    async with lock:
        remote = {str(row["_id"]): row for row in await stremio.datastore_get(conn.token, all_items=True)}
        old = remote.get(content_id)
        if old is None and not desired:
            return
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        candidate = dict(old or _stremio_new_library_item(item, now, in_library=desired))
        candidate["removed"] = not desired
        candidate["temp"] = False
        candidate["_mtime"] = now
        if old is None or not _stremio_same_item(old, candidate):
            await stremio.datastore_put(conn.token, [candidate])


async def _write_nuvio(db: AsyncSession, conn: MediaServerConnection, item: dict, desired: bool) -> None:
    from routers.sync import _nuvio_profile_id

    async def persist_refresh(session: nuvio.NuvioSession) -> None:
        conn.token = session.refresh_token
        await db.commit()

    async with nuvio.connection_lock(conn.id):
        await db.refresh(conn)
        await nuvio.merge_library(
            conn.url, conn.token, _nuvio_profile_id(conn),
            additions=[item] if desired else [],
            removed_content_ids=set() if desired else {item["content_id"]},
            on_refresh=persist_refresh,
        )


async def _record_membership(db: AsyncSession, user_id: int, media_id: int,
                             conn: MediaServerConnection, content_id: str, desired: bool) -> None:
    collection = (await db.execute(select(Collection).where(
        Collection.user_id == user_id, Collection.media_id == media_id,
    ))).scalar_one_or_none()
    if desired and collection is None:
        collection = Collection(user_id=user_id, media_id=media_id)
        db.add(collection)
        await db.flush()
    if collection is not None:
        files = (await db.execute(select(CollectionFile).where(
            CollectionFile.collection_id == collection.id,
            CollectionFile.connection_id == conn.id,
            CollectionFile.source == _source(conn),
        ))).scalars().all()
        if desired:
            if files:
                files[0].source_id = content_id
            else:
                db.add(CollectionFile(collection_id=collection.id, connection_id=conn.id,
                    source=_source(conn), source_id=content_id))
        else:
            for row in files:
                await db.delete(row)
            await db.flush()
            if not (await db.execute(select(CollectionFile.id).where(CollectionFile.collection_id == collection.id))).first():
                await db.delete(collection)
    managed = set(conn.stremio_pushed_library_ids or [])
    if desired:
        managed.add(content_id)
    else:
        managed.discard(content_id)
    conn.stremio_pushed_library_ids = sorted(managed)


async def dispatch_library_deliveries(db: AsyncSession, user_id: int, media_id: int) -> None:
    intent = (await db.execute(select(StreamingLibraryIntent).where(
        StreamingLibraryIntent.user_id == user_id,
        StreamingLibraryIntent.media_id == media_id,
    ))).scalar_one_or_none()
    if intent is None:
        return
    media = await db.get(Media, media_id)
    if media is None:
        return
    pending = (await db.execute(select(StreamingLibraryDelivery).where(
        StreamingLibraryDelivery.intent_id == intent.id,
        StreamingLibraryDelivery.state == "pending",
    ).order_by(StreamingLibraryDelivery.id))).scalars().all()
    if not pending:
        return
    try:
        item = await _record_for_media(db, user_id, media)
    except Exception as error:
        for row in pending:
            row.attempts += 1
            row.last_error = str(error) if isinstance(error, ValueError) else type(error).__name__
        await db.commit()
        return
    for row in pending:
        conn = await db.get(MediaServerConnection, row.connection_id)
        if not conn or conn.user_id != user_id or not conn.push_collection or conn.type not in ("stremio", "nuvio"):
            row.state = "cancelled"
            await db.commit()
            continue
        row.attempts += 1
        try:
            await require_stream_reconciliation(db, conn)
            if conn.type == "stremio":
                await _write_stremio(conn, item, row.desired)
            else:
                await _write_nuvio(db, conn, item, row.desired)
            await _record_membership(db, user_id, media_id, conn, item["content_id"], row.desired)
            row.state, row.last_error = "applied", None
        except HTTPException as error:
            row.last_error = str(error.detail)
        except Exception as error:
            row.last_error = type(error).__name__  # Never persist provider response bodies or tokens.
        await db.commit()


async def retry_pending_library_deliveries(db: AsyncSession, user_id: int, connection_id: int) -> None:
    """Retry a bounded batch when an established connection syncs again."""
    media_ids = (await db.execute(select(StreamingLibraryIntent.media_id)
        .join(StreamingLibraryDelivery, StreamingLibraryDelivery.intent_id == StreamingLibraryIntent.id)
        .where(StreamingLibraryIntent.user_id == user_id,
            StreamingLibraryDelivery.connection_id == connection_id,
            StreamingLibraryDelivery.state == "pending")
        .order_by(StreamingLibraryDelivery.id).limit(100))).scalars().all()
    for media_id in media_ids:
        await dispatch_library_deliveries(db, user_id, media_id)
