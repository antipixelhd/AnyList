"""Persist accepted cycle deliveries with canonical changes, then retry safely."""
import asyncio
from dataclasses import fields
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from core.pull_cycle import PullCycleState, allow_cycle_delivery, is_active
from models.base import CollectionSource
from models.sync import SyncJob, SyncStatus
from core.sync_delivery_targets import destination_scope

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
    targets = await delivery_targets(db, state)
    job = await db.get(SyncJob, job_id)
    job.stats = {**(job.stats or {}), "reconciled": True, "delivery_pending": True,
                 "delivery": delivery_payload(state), "delivery_targets": targets, "delivery_done": []}


async def delivery_targets(db, state):
    from models import MediaServerConnection, UserSettings
    connections = (await db.execute(select(MediaServerConnection).where(
        MediaServerConnection.user_id == state.user_id))).scalars().all()
    targets = []
    for conn in connections:
        if conn.id in state.connection_versions and conn.identity_version != state.connection_versions[conn.id]:
            continue
        if conn.push_enabled:
            targets.append(f"connection:{conn.id}")
            state.connection_versions.setdefault(conn.id, conn.identity_version)
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == state.user_id))).scalar_one_or_none()
    for provider in ("trakt", "simkl", "mdblist", "bingebase"):
        if any(getattr(settings, f"{provider}_push_{field}", False) for field in ("watched", "ratings", "collection")):
            targets.append(provider)
    return targets + ["queues"]


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
            if "delivery_targets" not in job.stats:
                # Upgrade batches saved by the previous release without losing them.
                targets = await delivery_targets(db, state)
                job.stats = {**job.stats, "delivery_targets": targets, "delivery_done": [],
                             "delivery": delivery_payload(state)}
                flag_modified(job, "updated_at")  # Delivery bookkeeping must not postpone the next pull.
                await db.commit()
            targets = list(job.stats["delivery_targets"])
            done = set(job.stats.get("delivery_done", []))
        errors = []
        for target in targets:
            if target in done:
                continue
            try:
                valid = True
                if target.startswith("connection:"):
                    from models import MediaServerConnection
                    async with factory() as db:
                        conn = await db.get(MediaServerConnection, int(target.split(":", 1)[1]))
                        valid = bool(conn and conn.user_id == user_id
                                     and conn.identity_version == state.connection_versions.get(conn.id))
                if valid:
                    with allow_cycle_delivery(state), destination_scope(target):
                        await _flush_pull_cycle(state)
                async with factory() as db:
                    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id).with_for_update())).scalar_one()
                    done = set(job.stats.get("delivery_done", [])) | {target}
                    job.stats = {**job.stats, "delivery_done": sorted(done)}
                    if done.issuperset(targets):
                        job.stats = {key: value for key, value in job.stats.items()
                                     if key not in {"delivery", "delivery_pending", "delivery_targets", "delivery_done"}}
                    flag_modified(job, "updated_at")
                    await db.commit()
            except Exception as error:
                errors.append(error)
        if errors:
            raise RuntimeError("Accepted sync delivery is pending retry") from errors[0]


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
