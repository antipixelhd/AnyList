"""Persist accepted cycle deliveries with canonical changes, then retry safely."""
import asyncio
from dataclasses import fields
from datetime import datetime

from sqlalchemy import select

from core.pull_cycle import PullCycleState, allow_cycle_delivery, is_active
from models.base import CollectionSource
from models.sync import SyncJob, SyncStatus

_locks: dict[int, asyncio.Lock] = {}


def _encode(value):
    if isinstance(value, CollectionSource):
        return {"source": value.value}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, set):
        return {"set": [_encode(item) for item in value]}
    if isinstance(value, tuple):
        return {"tuple": [_encode(item) for item in value]}
    if isinstance(value, dict):
        return {"map": [[_encode(key), _encode(item)] for key, item in value.items()]}
    return value


def _decode(value):
    if not isinstance(value, dict):
        return value
    if "source" in value:
        return CollectionSource(value["source"])
    if "datetime" in value:
        return datetime.fromisoformat(value["datetime"])
    if "set" in value:
        return {_decode(item) for item in value["set"]}
    if "tuple" in value:
        return tuple(_decode(item) for item in value["tuple"])
    return {_decode(key): _decode(item) for key, item in value["map"]}


def delivery_payload(state):
    # Credentials are loaded at delivery time, never copied to job stats.
    return {field.name: _encode(getattr(state, field.name)) for field in fields(state)
            if field.name != "library_api_key"}


async def persist_delivery(db, job_id, state):
    job = await db.get(SyncJob, job_id)
    job.stats = {**(job.stats or {}), "reconciled": True, "delivery_pending": True,
                 "delivery": delivery_payload(state)}


async def deliver_cycle(user_id, job_id, factory, state=None):
    """At-least-once handoff; a failure leaves the committed batch recoverable."""
    from core.scheduler import _flush_pull_cycle
    async with _locks.setdefault(user_id, asyncio.Lock()):
        async with factory() as db:
            job = await db.get(SyncJob, job_id)
            if job is None or not (job.stats or {}).get("delivery_pending"):
                return
            if state is None:
                state = PullCycleState(**{key: _decode(value) for key, value in job.stats["delivery"].items()})
        with allow_cycle_delivery(state):
            await _flush_pull_cycle(state)
        async with factory() as db:
            job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id).with_for_update())).scalar_one()
            job.stats = {key: value for key, value in job.stats.items() if key not in {"delivery", "delivery_pending"}}
            await db.commit()


async def retry_cycle_deliveries(factory):
    from core.pull_cycle import _locks as cycle_locks
    async with factory() as db:
        jobs = (await db.execute(select(SyncJob.id, SyncJob.user_id).where(
            SyncJob.job_type == "pull_cycle", SyncJob.status.in_((SyncStatus.failed, SyncStatus.completed)),
            SyncJob.stats["delivery_pending"].as_boolean().is_(True),
        ).order_by(SyncJob.id))).all()
    for job_id, user_id in jobs:
        if is_active(user_id):
            continue
        try:
            # A new pull can be admitted while recovery awaits its DB reads.
            # Share its execution lock so delivery cannot cross that pull.
            async with cycle_locks.setdefault(user_id, asyncio.Lock()):
                await deliver_cycle(user_id, job_id, factory)
        except Exception:
            # Do not persist provider response bodies or credentials.
            continue
