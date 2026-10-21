"""Durable public discovery evidence, separate from personal tracking caches."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from core.catalogue_providers import ProviderError
from models.catalogue import MetadataSnapshot

_flights = {}
SCHEMA_VERSION = 'v1'


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GameSourceCache:
    def __init__(self, session_factory, *, max_records=256):
        if isinstance(max_records, bool) or not isinstance(max_records, int) or not 1 <= max_records <= 1024:
            raise ValueError('Invalid discovery cache capacity')
        self.sessions = session_factory
        self.max_records = max_records

    @staticmethod
    def query(provider, kind, key):
        if any(not isinstance(value,str) for value in (provider,kind,key)) or not kind.startswith('game_') or not 1<=len(provider)<=32 or not len(kind)<=24 or len(f'{SCHEMA_VERSION}:{key}')>200:
            raise ProviderError('invalid_request')
        return select(MetadataSnapshot).where(MetadataSnapshot.provider == provider,
            MetadataSnapshot.kind == kind, MetadataSnapshot.external_id == f'{SCHEMA_VERSION}:{key}')

    @staticmethod
    def view(row, now, max_stale):
        if row and row.payload is not None and row.fetched_at and now-row.fetched_at <= max_stale:
            return dict(data=row.payload, fetched_at=row.fetched_at.isoformat(),
                stale=not bool(row.expires_at and row.expires_at > now), error=row.error_code)
        return None

    async def prune(self, db, provider, kind, now):
        # Only this discovery scope is bounded. Canonical metadata snapshots and
        # in-progress leases are never deleted to make room for browse queries.
        overflow = select(MetadataSnapshot.id).where(MetadataSnapshot.provider == provider,
            MetadataSnapshot.kind == kind, or_(MetadataSnapshot.lease_until.is_(None), MetadataSnapshot.lease_until <= now))
        overflow = overflow.order_by(func.coalesce(MetadataSnapshot.fetched_at, MetadataSnapshot.next_attempt_at)
            .desc().nullslast(), MetadataSnapshot.id.desc()).offset(self.max_records)
        await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.id.in_(overflow))
            .execution_options(synchronize_session=False))

    async def peek(self, provider, kind, key, *, max_age):
        """Read an immutable browse continuation; eviction means restart, not replay."""
        if not kind.startswith('game_'):
            raise ProviderError('invalid_request')
        async with self.sessions() as db:
            row = (await db.execute(self.query(provider, kind, key).with_only_columns(
                MetadataSnapshot.payload, MetadataSnapshot.fetched_at))).one_or_none()
        if not row or row[0] is None or not row[1] or utcnow()-row[1] > max_age:
            raise ProviderError('cursor_expired')
        return row[0]

    async def get(self, provider, kind, key, loader, *, ttl=timedelta(hours=6), max_stale=timedelta(days=3)):
        if not kind.startswith('game_'):
            raise ProviderError('invalid_request')
        async with self.sessions() as db:
            row = await db.scalar(self.query(provider, kind, key))
        now = utcnow()
        previous = self.view(row, now, max_stale)
        if previous and not previous['stale']:
            return previous
        if row and row.next_attempt_at and row.next_attempt_at > now:
            if previous:
                return previous
            raise ProviderError('retry_pending')
        flight_key = (id(self.sessions), SCHEMA_VERSION, provider, kind, key)
        task = _flights.get(flight_key)
        if task is None:
            if len(_flights) >= 64:
                if previous:
                    return previous
                raise ProviderError('rate_limited')
            task = asyncio.create_task(self.refresh(provider, kind, key, loader, ttl, max_stale))
            _flights[flight_key] = task
            def finished(done):
                if _flights.get(flight_key) is done:
                    _flights.pop(flight_key, None)
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finished)
        # A usable expired ranking paints immediately while a shared refresh
        # runs. Cold reads wait for that same task; cancelling one cannot cancel
        # another viewer's refresh. The database lease also spans workers.
        return previous if previous else await asyncio.shield(task)

    async def refresh(self, provider, kind, key, loader, ttl, max_stale):
        self.query(provider,kind,key)  # Validate the persisted namespace before INSERT.
        token = uuid.uuid4().hex
        async with self.sessions() as db:
            await db.execute(insert(MetadataSnapshot).values(provider=provider, kind=kind, external_id=f'{SCHEMA_VERSION}:{key}')
                .on_conflict_do_nothing())
            row = await db.scalar(self.query(provider, kind, key).with_for_update())
            now = utcnow()
            previous = self.view(row, now, max_stale)
            if row.expires_at and row.expires_at > now and previous:
                await db.commit()
                return previous
            if row.lease_until and row.lease_until > now:
                await db.commit()
                if previous:
                    return previous
                raise ProviderError('refreshing', 5)
            if row.next_attempt_at and row.next_attempt_at > now:
                await db.commit()
                if previous:
                    return previous
                raise ProviderError('retry_pending')
            row.lease_token, row.lease_until = token, now+timedelta(minutes=3)
            await db.commit()
        error = None
        try:
            data = await asyncio.wait_for(loader(), 60)
            if not isinstance(data, dict) or len(json.dumps(data).encode()) > 2_000_000:
                raise ProviderError('bad_response')
        except (ProviderError, asyncio.TimeoutError) as cause:
            error = cause.code if isinstance(cause, ProviderError) else 'unavailable'
        except Exception:
            error = 'unavailable'
        async with self.sessions() as db:
            row = await db.scalar(self.query(provider, kind, key).with_for_update())
            now = utcnow()
            if row.lease_token != token:
                previous = self.view(row, now, max_stale)
                await db.commit()
                if previous:
                    return previous
                raise ProviderError('refreshing', 5)
            row.lease_token = row.lease_until = None
            if error:
                row.attempts += 1
                row.error_code = error
                row.next_attempt_at = now+timedelta(seconds=min(3600, 60*2**min(row.attempts-1, 6)))
            else:
                row.payload, row.fetched_at, row.expires_at = data, now, now+ttl
                row.error_code, row.next_attempt_at, row.attempts = None, None, 0
            await self.prune(db, provider, kind, now)
            await db.commit()
            result = self.view(row, now, max_stale)
        if result:
            return result
        raise ProviderError(error or 'unavailable')
