"""Deliver explicit Library changes to each opted-in Stremio/Nuvio account.

Observed collection files describe provider state. The intent is kept separately
so a failed write, or another collection source, cannot silently reverse it.
"""

from core.connection_identity import refresh_stream_connection
import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import nuvio, stremio
from core.tracking_snapshot import require_stream_reconciliation
from models import Collection, CollectionFile, Media, MediaServerConnection, User
from models.base import CollectionSource, MediaType
from models.streaming_library import StreamingLibraryDelivery, StreamingLibraryIntent


def _utcnow_naive() -> datetime:
    """Return a UTC timestamp for database columns without timezone support."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
        "in_library": intent.desired if intent else bool(memberships),
        "desired": intent.desired if intent else bool(memberships),
        "available": True,
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
    # An explicit user choice is newer than every provider observation, even
    # when the chosen value matches the current intent. This also lets a fresh
    # choice wake a delivery suppressed by a provider clear.
    intent.updated_at = _utcnow_naive()
    existing = {
        row.connection_id: row
        for row in (await db.execute(select(StreamingLibraryDelivery).where(
            StreamingLibraryDelivery.intent_id == intent.id,
        ))).scalars().all()
    }
    target_ids = {conn.id for conn in targets}
    delivery_queued = False
    for row in existing.values():
        if row.connection_id not in target_ids and row.state == "pending":
            row.state = "cancelled"
    for conn in targets:
        row = existing.get(conn.id)
        if row is None:
            db.add(StreamingLibraryDelivery(
                intent_id=intent.id,
                connection_id=conn.id,
                desired=desired,
                updated_at=intent.updated_at,
            ))
            delivery_queued = True
        elif changed or row.desired != desired or row.state in ("cancelled", "cleared"):
            row.desired, row.state, row.attempts, row.last_error = desired, "pending", 0, None
            row.updated_at = intent.updated_at
            delivery_queued = True
    await _set_manual_anchor(db, user_id, media_id, desired)
    await db.commit()  # The decision survives a failed provider request.
    state = await library_state(db, user_id, media_id)
    # Internal hint lets the route avoid starting another retry for an
    # unchanged pending delivery. Explicit retry and connection sync still
    # process pending rows.
    state["_delivery_queued"] = delivery_queued
    return state


async def deliver_library_intent(user_id: int, media_id: int) -> None:
    """Run after the response with a session independent of the request."""
    from db import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await dispatch_library_deliveries(db, user_id, media_id)
    except Exception:
        logging.getLogger(__name__).exception("Library delivery task failed for user %s, media %s", user_id, media_id)


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


async def _write_stremio(db: AsyncSession, conn: MediaServerConnection, item: dict, desired: bool) -> None:
    from routers.sync import _stremio_new_library_item, _stremio_same_item

    content_id = item["content_id"]
    async with stremio.connection_lock(conn.id):
        await refresh_stream_connection(db, conn)
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
        # Keep dispatch_library_deliveries' user row lock held until every
        # provider delivery for this intent has finished.
        await db.flush()

    async with nuvio.connection_lock(conn.id):
        await refresh_stream_connection(db, conn)
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
    # Intent writes use this same per-user row lock. Holding it through the
    # provider write serializes concurrent dispatches and prevents an older
    # delivery from applying after a newer library choice has been committed.
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
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
            row.updated_at = _utcnow_naive()
        await db.commit()
        return
    for row in pending:
        conn = await db.get(MediaServerConnection, row.connection_id)
        if not conn or conn.user_id != user_id or not conn.push_collection or conn.type not in ("stremio", "nuvio"):
            row.state = "cancelled"
            continue
        row.attempts += 1
        row.updated_at = _utcnow_naive()
        try:
            await require_stream_reconciliation(db, conn)
            if conn.type == "stremio":
                await _write_stremio(db, conn, item, row.desired)
            else:
                await _write_nuvio(db, conn, item, row.desired)
            await _record_membership(db, user_id, media_id, conn, item["content_id"], row.desired)
            row.state, row.last_error = "applied", None
        except HTTPException as error:
            row.last_error = str(error.detail)
        except Exception as error:
            row.last_error = type(error).__name__  # Never persist provider response bodies or tokens.
    # Commit only after all connections have been attempted. The row lock then
    # prevents a newer intent from committing between fanout targets.
    await db.commit()


async def retry_pending_library_deliveries(db: AsyncSession, user_id: int, connection_id: int) -> None:
    """Retry a bounded batch when an established connection syncs again."""
    conn = await db.get(MediaServerConnection, connection_id)
    if not conn or conn.user_id != user_id or conn.type not in ("stremio", "nuvio") or not conn.push_collection:
        return
    # Choices made before this connection existed become deliveries once its
    # first reviewed snapshot has been imported. The dispatcher checks that gate.
    intents = (await db.execute(select(StreamingLibraryIntent).where(
        StreamingLibraryIntent.user_id == user_id,
    ).order_by(StreamingLibraryIntent.id))).scalars().all()
    existing = {
        row.intent_id: row for row in (await db.execute(select(StreamingLibraryDelivery).where(
        StreamingLibraryDelivery.connection_id == connection_id,
    ))).scalars().all()
    }
    for intent in intents:
        row = existing.get(intent.id)
        if row is None:
            db.add(StreamingLibraryDelivery(intent_id=intent.id, connection_id=connection_id,
                                            desired=intent.desired,
                                            updated_at=_utcnow_naive()))
        elif row.desired != intent.desired or row.state == "cancelled":
            row.desired, row.state, row.attempts, row.last_error = intent.desired, "pending", 0, None
            row.updated_at = _utcnow_naive()
    await db.flush()
    media_ids = (await db.execute(select(StreamingLibraryIntent.media_id)
        .join(StreamingLibraryDelivery, StreamingLibraryDelivery.intent_id == StreamingLibraryIntent.id)
        .where(StreamingLibraryIntent.user_id == user_id,
            StreamingLibraryDelivery.connection_id == connection_id,
            StreamingLibraryDelivery.state == "pending")
        .order_by(StreamingLibraryDelivery.id).limit(100))).scalars().all()
    for media_id in media_ids:
        await dispatch_library_deliveries(db, user_id, media_id)


async def dispatch_pending_library_deliveries(
    db: AsyncSession, user_id: int, *, limit: int = 100,
) -> int:
    """Retry persisted library writes without waiting for another provider pull.

    Oldest-updated work runs first so a bounded batch of failing rows cannot
    starve later titles. Attempts break timestamp ties, including rows retried
    within one long-running database transaction.
    """
    media_ids = (await db.execute(
        select(StreamingLibraryIntent.media_id)
        .join(StreamingLibraryDelivery, StreamingLibraryDelivery.intent_id == StreamingLibraryIntent.id)
        .where(
            StreamingLibraryIntent.user_id == user_id,
            StreamingLibraryDelivery.state == "pending",
        )
        .group_by(StreamingLibraryIntent.media_id)
        .order_by(func.min(StreamingLibraryDelivery.updated_at),
                  func.min(StreamingLibraryDelivery.attempts),
                  func.min(StreamingLibraryDelivery.id))
        .limit(max(0, limit))
    )).scalars().all()
    for media_id in media_ids:
        await dispatch_library_deliveries(db, user_id, media_id)
    return len(media_ids)


async def queue_provider_library_changes(
    db: AsyncSession,
    user_id: int,
    changed_media_ids: set[int],
    *,
    exclude_connection_ids: set[int],
    source_observed_at_by_media: dict[int, datetime],
    source_connection_ids_by_media: dict[int, set[int]] | None = None,
) -> int:
    """Persist an accepted provider collection delta for eligible peers.

    The source and its approved snapshot are validated by the sync fan-out
    entry point. Its prior snapshot time is carried through scheduled pull
    coalescing; a newer local AnyList intent wins over an older provider
    observation. Each source is excluded only for titles it actually changed,
    so a peer pulled in the same scheduler cycle still receives unrelated
    title changes. Per-title source IDs take precedence over the legacy global
    exclusion set when both are supplied.
    """
    if not changed_media_ids:
        return 0

    legacy_source_ids = set(exclude_connection_ids)
    if source_connection_ids_by_media is None and not legacy_source_ids:
        return 0

    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    target_query = select(MediaServerConnection).where(
        MediaServerConnection.user_id == user_id,
        MediaServerConnection.type.in_(("stremio", "nuvio")),
        MediaServerConnection.push_collection.is_(True),
    )
    if legacy_source_ids and source_connection_ids_by_media is None:
        target_query = target_query.where(MediaServerConnection.id.not_in(legacy_source_ids))
    all_targets = (await db.execute(target_query.order_by(MediaServerConnection.id))).scalars().all()
    existing_intents = {
        row.media_id: row
        for row in (await db.execute(select(StreamingLibraryIntent).where(
            StreamingLibraryIntent.user_id == user_id,
            StreamingLibraryIntent.media_id.in_(changed_media_ids),
        ).with_for_update())).scalars().all()
    }
    collections = set((await db.execute(select(Collection.media_id).where(
        Collection.user_id == user_id,
        Collection.media_id.in_(changed_media_ids),
    ))).scalars().all())
    queued = 0
    for media_id in changed_media_ids:
        observed_at = source_observed_at_by_media.get(media_id)
        source_ids = (
            set(source_connection_ids_by_media.get(media_id, ()))
            if source_connection_ids_by_media is not None
            else legacy_source_ids
        )
        if observed_at is None or not source_ids:
            continue
        intent = existing_intents.get(media_id)
        if intent is not None and intent.updated_at is not None and intent.updated_at > observed_at:
            continue
        desired = media_id in collections
        if intent is None:
            intent = StreamingLibraryIntent(
                user_id=user_id,
                media_id=media_id,
                desired=desired,
                updated_at=observed_at,
            )
            db.add(intent)
            await db.flush()
            existing_intents[media_id] = intent
        else:
            intent.desired = desired
            # Preserve the observation's clock so later provider deltas can
            # be ordered against explicit local choices and other sources.
            intent.updated_at = observed_at

        targets = [connection for connection in all_targets if connection.id not in source_ids]
        existing_deliveries = {
            row.connection_id: row
            for row in (await db.execute(select(StreamingLibraryDelivery).where(
                StreamingLibraryDelivery.intent_id == intent.id,
                StreamingLibraryDelivery.connection_id.in_(
                    [conn.id for conn in targets] + list(source_ids)
                ),
            ).with_for_update())).scalars().all()
        }
        # A source may already have an older pending local write. Settle that
        # row against the accepted source observation so the retry worker does
        # not echo it back after this sync excluded the source.
        for connection_id in source_ids:
            delivery = existing_deliveries.get(connection_id)
            if delivery is None:
                db.add(StreamingLibraryDelivery(
                    intent_id=intent.id,
                    connection_id=connection_id,
                    desired=desired,
                    state="observed",
                    updated_at=_utcnow_naive(),
                ))
            else:
                delivery.desired = desired
                delivery.state = "observed"
                delivery.attempts = 0
                delivery.last_error = None
                delivery.updated_at = _utcnow_naive()
        for connection in targets:
            delivery = existing_deliveries.get(connection.id)
            if delivery is None:
                db.add(StreamingLibraryDelivery(
                    intent_id=intent.id,
                    connection_id=connection.id,
                    desired=desired,
                    state="pending",
                    updated_at=_utcnow_naive(),
                ))
            else:
                # A real accepted source delta is the only event that wakes a
                # row suppressed by a provider clear. It also repairs peers
                # that may have drifted since their last successful write.
                delivery.desired = desired
                delivery.state = "pending"
                delivery.attempts = 0
                delivery.last_error = None
                delivery.updated_at = _utcnow_naive()
            queued += 1
    await db.flush()
    return queued


async def clear_pending_library_deliveries(
    db: AsyncSession, user_id: int, connection_id: int,
) -> int:
    """Suppress canonical writes for a destination after its library is cleared.

    ``cleared`` is deliberately distinct from ordinary cancellation: a later
    provider sync must not recreate these writes and repopulate the cleared
    library. Create tombstones for existing intents without a delivery too, so
    the next provider sync cannot materialize them as pending writes. A new
    canonical library choice may still reactivate a tombstone.
    """
    intents = (await db.execute(select(StreamingLibraryIntent).where(
        StreamingLibraryIntent.user_id == user_id,
    ).order_by(StreamingLibraryIntent.id).with_for_update())).scalars().all()
    existing = {
        row.intent_id: row
        for row in (await db.execute(select(StreamingLibraryDelivery).where(
            StreamingLibraryDelivery.connection_id == connection_id,
            StreamingLibraryDelivery.intent_id.in_([intent.id for intent in intents]),
        ).with_for_update())).scalars().all()
    } if intents else {}
    changed = 0
    for intent in intents:
        row = existing.get(intent.id)
        if row is None:
            db.add(StreamingLibraryDelivery(
                intent_id=intent.id,
                connection_id=connection_id,
                desired=intent.desired,
                state="cleared",
            ))
            changed += 1
            continue
        if row.state != "cleared" or row.desired != intent.desired or row.last_error is not None:
            changed += 1
        row.desired = intent.desired
        row.state = "cleared"
        row.last_error = None
    await db.flush()
    return changed
