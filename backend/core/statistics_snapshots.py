"""Database leases, consistent reads and atomic publication of daily statistics."""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from core.statistics_facts import build_overviews, load_facts
from db import AsyncSessionLocal
from models.statistics import StatsMetadataRevision, UserStatsSnapshot, UserStatsState
from schemas_statistics import Overview

CONTRACT_VERSION = 14
LEASE_TIME = timedelta(minutes=10)
REFRESH_TIME = timedelta(hours=24)
log = logging.getLogger(__name__)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def ensure_state(db, user_id):
    await db.execute(insert(UserStatsState).values(user_id=user_id).on_conflict_do_nothing())


async def prepare_startup(factory=AsyncSessionLocal):
    """Queue deployment upgrades without resetting healthy accounts' daily clocks."""
    now = utcnow()
    outdated = select(UserStatsSnapshot.id).where(UserStatsSnapshot.contract_version != CONTRACT_VERSION)
    async with factory() as db, db.begin():
        await db.execute(update(UserStatsState).where(
            UserStatsState.active_snapshot_id.in_(outdated),
            UserStatsState.next_due_at > now,
        ).values(dirty=True, next_due_at=now))


async def claim_next(factory=AsyncSessionLocal, now=None, *, user_ids=None):
    now = now or utcnow()
    async with factory() as db, db.begin():
        state = await db.scalar(select(UserStatsState).where(
            UserStatsState.next_due_at <= now,
            True if user_ids is None else UserStatsState.user_id.in_(user_ids),
            or_(UserStatsState.lease_token.is_(None), UserStatsState.lease_until <= now),
        ).order_by(UserStatsState.next_due_at, UserStatsState.user_id).with_for_update(skip_locked=True).limit(1))
        if state is None:
            return None
        metadata_revision = await db.scalar(select(StatsMetadataRevision.revision).where(StatsMetadataRevision.id == 1))
        snapshot = await db.get(UserStatsSnapshot, state.active_snapshot_id) if state.active_snapshot_id else None
        if snapshot and not state.dirty and snapshot.contract_version == CONTRACT_VERSION and snapshot.metadata_revision == metadata_revision:
            state.next_due_at = now + REFRESH_TIME
            return {"skipped": True}
        token = str(uuid.uuid4())
        state.lease_token, state.lease_until = token, now + LEASE_TIME
        state.last_attempt_at = now
        return {"user_id": state.user_id, "token": token, "skipped": False}


async def compute(claim, factory=AsyncSessionLocal):
    # Isolation is set before the first read. No locks on history rows or provider I/O.
    async with factory() as db:
        await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        state = await db.get(UserStatsState, claim["user_id"])
        if state is None or state.lease_token != claim["token"]:
            return None
        metadata_revision = await db.scalar(select(StatsMetadataRevision.revision).where(StatsMetadataRevision.id == 1))
        source_revision = state.source_revision
        facts, coverage = await load_facts(db, state.user_id)
        payload = build_overviews(facts, coverage)
        return {"payload": payload, "source_revision": source_revision, "metadata_revision": metadata_revision}


async def publish(claim, result, factory=AsyncSessionLocal, now=None):
    now = now or utcnow()
    async with factory() as db, db.begin():
        state = await db.scalar(select(UserStatsState).where(UserStatsState.user_id == claim["user_id"]).with_for_update())
        if state is None or state.lease_token != claim["token"] or state.lease_until <= now:
            return False
        # Record shared metadata's cutoff. Later ordinary enrichment remains due
        # through this revision; visibility purges serialize on the state row.
        revision = await db.scalar(select(StatsMetadataRevision.revision).where(StatsMetadataRevision.id == 1))
        if state.source_revision != result["source_revision"] or revision != result["metadata_revision"]:
            state.lease_token = state.lease_until = None
            state.dirty = True
            state.next_due_at = now + timedelta(minutes=1)
            return False
        snapshot = UserStatsSnapshot(user_id=state.user_id, source_revision=result["source_revision"],
                                     metadata_revision=result["metadata_revision"], contract_version=CONTRACT_VERSION,
                                     computed_at=now, payload=result["payload"])
        db.add(snapshot)
        await db.flush()
        state.active_snapshot_id = snapshot.id
        state.dirty = False
        state.lease_token = state.lease_until = None
        state.attempts, state.error_code = 0, None
        # User-specific staggering, rather than one midnight refresh spike.
        state.next_due_at = now + REFRESH_TIME + timedelta(seconds=state.user_id % 300)
        await db.flush()
        await db.execute(delete(UserStatsSnapshot).where(UserStatsSnapshot.user_id == state.user_id, UserStatsSnapshot.id != snapshot.id))
        return True


async def record_failure(claim, factory=AsyncSessionLocal, now=None):
    now = now or utcnow()
    async with factory() as db, db.begin():
        state = await db.scalar(select(UserStatsState).where(UserStatsState.user_id == claim["user_id"]).with_for_update())
        if state is None or state.lease_token != claim["token"]:
            return
        state.attempts += 1
        state.error_code = "calculation_failed"
        state.lease_token = state.lease_until = None
        state.next_due_at = now + timedelta(minutes=min(60, 2 ** min(state.attempts, 6)))


async def run_due(factory=AsyncSessionLocal, limit=4, *, user_ids=None):
    completed = 0
    for _ in range(limit):
        claim = await claim_next(factory) if user_ids is None else await claim_next(factory, user_ids=user_ids)
        if claim is None:
            break
        if claim["skipped"]:
            continue
        try:
            result = await compute(claim, factory)
            if result is not None and await publish(claim, result, factory):
                completed += 1
        except asyncio.CancelledError:
            # Expiring database lease allows recovery after crash/restart.
            raise
        except Exception:
            # Do not log query parameters, private history or provider credentials.
            log.warning("Statistics calculation failed; last successful generation retained")
            await record_failure(claim, factory)
    return completed


async def scheduler():
    prepared = False
    while True:
        try:
            if not prepared:
                await prepare_startup()
                prepared = True
            completed = await run_due()
            if completed == 4:
                # Drain deployment backlogs in bounded batches without a 30s
                # pause between every four accounts.
                await asyncio.sleep(0)
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Statistics scheduler could not claim work; retrying")
        await asyncio.sleep(30)


async def read_overview(db, user_id, media_type):
    await ensure_state(db, user_id)
    state = await db.get(UserStatsState, user_id)
    snapshot = await db.get(UserStatsSnapshot, state.active_snapshot_id) if state.active_snapshot_id else None
    outdated = bool(snapshot and snapshot.contract_version != CONTRACT_VERSION)
    overview = snapshot.payload.get(media_type) if snapshot else None
    if outdated:
        # Calculation revisions can retain a readable shape. Keep the durable
        # pointer until atomic publication; never discard the last good result
        # merely because a deployment changed the calculation version.
        try:
            overview = Overview.model_validate(overview).model_dump()
        except ValidationError:
            overview = None
        state.dirty = True
        if not state.lease_token and not state.error_code:
            state.next_due_at = min(state.next_due_at, utcnow())
    await db.commit()
    return {"status": "ready" if overview is not None else "error" if state.error_code else "pending",
            "generation": snapshot.id if snapshot else None,
            "computed_at": snapshot.computed_at.replace(tzinfo=timezone.utc) if snapshot else None,
            "next_update_at": state.next_due_at.replace(tzinfo=timezone.utc),
            "refreshing": outdated or bool(state.lease_token and state.lease_until and state.lease_until > utcnow()),
            "overview": overview}
