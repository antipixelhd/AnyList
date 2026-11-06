"""Shared, bounded episode acquisition owned by the server, not a browser request."""
import asyncio
import uuid
import weakref
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from core import settings_store
from core.tracking_metadata import hydrate_tracking_episodes, tracking_catalogue_is_fresh
from models import Media
from models.catalogue import MetadataSnapshot

PROVIDER = 'tracking'
KIND = 'episode_catalogue'
_flights = {}
_slots = weakref.WeakKeyDictionary()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def complete(media):
    return bool((media.tmdb_data or {}).get('tracking_catalogue_refreshed_at'))


def fresh(media):
    return tracking_catalogue_is_fresh(media, media.status or (media.tmdb_data or {}).get('status'))


def query(media_id):
    return select(MetadataSnapshot).where(MetadataSnapshot.provider == PROVIDER,
        MetadataSnapshot.kind == KIND, MetadataSnapshot.external_id == str(media_id))


def state(ready, *, error=None, delay=2):
    return {'ready': ready, 'pending': True, 'state': 'retry' if error else 'loading',
        'error': error, 'retry_after': delay}


async def _load(engine, media_id, viewer_id):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    loop = asyncio.get_running_loop()
    reference = _slots.get(loop)
    slots = reference() if reference else None
    if slots is None:
        slots = asyncio.Semaphore(2)
        _slots[loop] = weakref.ref(slots)
    token = None
    ready = False
    try:
        async with slots:
            async with sessions() as db:
                media = await db.get(Media, media_id)
                if media is None:
                    return {'state': 'unavailable', 'pending': False, 'ready': False}
                ready = complete(media)
                await db.execute(insert(MetadataSnapshot).values(provider=PROVIDER, kind=KIND,
                    external_id=str(media_id)).on_conflict_do_nothing(constraint='uq_catalogue_snapshot'))
                row = await db.scalar(query(media_id).with_for_update(skip_locked=True))
                stamp = now()
                if row is None or row.lease_until and row.lease_until > stamp:
                    return state(ready)
                if row.next_attempt_at and row.next_attempt_at > stamp:
                    return state(ready, error=row.error_code,
                        delay=max(2, int((row.next_attempt_at - stamp).total_seconds()) + 1))
                token = uuid.uuid4().hex
                row.lease_token = token
                row.lease_until = stamp + timedelta(minutes=4)
                await db.commit()
                # Recheck under the same media lock as explicit/scheduled refreshes.
                media = await db.scalar(select(Media).where(Media.id == media_id)
                    .with_for_update().execution_options(populate_existing=True))
                if not fresh(media):
                    key = await settings_store.get_user_tmdb_key(db, viewer_id if viewer_id is not None else -1)
                    tvdb_key = await settings_store.get_user_tvdb_key(db, viewer_id if viewer_id is not None else -1)
                    async with asyncio.timeout(180):
                        await hydrate_tracking_episodes(db, media, key, tvdb_key)
                result = await db.execute(update(MetadataSnapshot).where(
                    MetadataSnapshot.provider == PROVIDER, MetadataSnapshot.kind == KIND,
                    MetadataSnapshot.external_id == str(media_id), MetadataSnapshot.lease_token == token,
                    MetadataSnapshot.lease_until > now())
                    .values(lease_token=None, lease_until=None, next_attempt_at=None, error_code=None,
                        fetched_at=now(), coverage={'complete': True}))
                if not result.rowcount:
                    await db.rollback()
                    return state(ready)
                await db.commit()
                return {'state': 'ready', 'ready': True, 'pending': False}
    except BaseException as error:
        cancelled = isinstance(error, asyncio.CancelledError)
        if token:
            async with sessions() as db:
                await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider == PROVIDER,
                    MetadataSnapshot.kind == KIND, MetadataSnapshot.external_id == str(media_id),
                    MetadataSnapshot.lease_token == token).values(lease_token=None, lease_until=None,
                        next_attempt_at=None if cancelled else now() + timedelta(seconds=60),
                        error_code=None if cancelled else 'episode_metadata_unavailable'))
                await db.commit()
        if cancelled:
            raise
        if not isinstance(error, Exception):
            raise
        return state(ready, error='episode_metadata_unavailable', delay=60)


async def ensure(db, media, viewer_id):
    """A cancelled subscriber never cancels acquisition needed by another page."""
    if fresh(media):
        return {'state': 'ready', 'ready': True, 'pending': False}
    ready = complete(media)
    row = await db.scalar(query(media.id))
    if row and row.next_attempt_at and row.next_attempt_at > now():
        return state(ready, error=row.error_code,
            delay=max(2, int((row.next_attempt_at - now()).total_seconds()) + 1))
    engine = db.bind
    key = (engine, media.id)
    task = _flights.get(key)
    if task is None:
        if sum(scope is engine for scope, _ in _flights) >= 16:
            return state(ready)
        task = asyncio.create_task(_load(engine, media.id, viewer_id))
        _flights[key] = task

        def finished(result):
            if _flights.get(key) is result:
                _flights.pop(key, None)
            if not result.cancelled():
                result.exception()
        task.add_done_callback(finished)
    await db.commit()  # Never hold the subscriber's SQL connection while waiting.
    if ready:
        return state(True)
    return await asyncio.shield(task)


async def close():
    tasks = list(_flights.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
