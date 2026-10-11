"""Immediate account refreshes share the daily worker's durable lease."""

import asyncio
import uuid
from datetime import timezone

from sqlalchemy import select

from core import statistics_snapshots as snapshots
from models.statistics import StatsMetadataRevision, UserStatsSnapshot, UserStatsState
from schemas_statistics import StatisticsRefresh


async def refresh_status(user_id: int, factory=None) -> StatisticsRefresh:
    factory = factory or snapshots.AsyncSessionLocal
    async with factory() as db:
        # Publication replaces and removes generations atomically; keep these
        # status reads at one cutoff too, including the metadata revision.
        await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        state = await db.get(UserStatsState, user_id)
        if state is None:
            return StatisticsRefresh(status="pending")
        snapshot = await db.get(UserStatsSnapshot, state.active_snapshot_id) if state.active_snapshot_id else None
        revision = await db.scalar(select(StatsMetadataRevision.revision).where(StatsMetadataRevision.id == 1))
        if state.lease_token and state.lease_until and state.lease_until > snapshots.utcnow():
            status = "refreshing"
        elif state.error_code:
            status = "error"
        elif (state.dirty or snapshot is None or snapshot.contract_version != snapshots.CONTRACT_VERSION
              or snapshot.source_revision != state.source_revision or snapshot.metadata_revision != revision):
            status = "pending"
        else:
            status = "ready"
        return StatisticsRefresh(
            status=status,
            generation=snapshot.id if snapshot else None,
            computed_at=snapshot.computed_at.replace(tzinfo=timezone.utc) if snapshot else None,
        )


async def refresh_account(user_id: int, factory=None) -> StatisticsRefresh:
    factory = factory or snapshots.AsyncSessionLocal
    # Retry a single publication race immediately instead of waiting for the scheduler.
    for _ in range(2):
        now = snapshots.utcnow()
        async with factory() as db, db.begin():
            await snapshots.ensure_state(db, user_id)
            state = await db.scalar(select(UserStatsState).where(
                UserStatsState.user_id == user_id,
            ).with_for_update())
            if state.lease_token and state.lease_until and state.lease_until > now:
                claim = None
            else:
                token = str(uuid.uuid4())
                state.lease_token, state.lease_until = token, now + snapshots.LEASE_TIME
                state.last_attempt_at = now
                state.dirty, state.next_due_at, state.error_code = True, now, None
                claim = {"user_id": user_id, "token": token, "skipped": False}
        if claim is None:
            return await refresh_status(user_id, factory)
        try:
            async with asyncio.timeout(120):
                result = await snapshots.compute(claim, factory)
                if result is not None and await snapshots.publish(claim, result, factory):
                    return await refresh_status(user_id, factory)
        except asyncio.CancelledError:
            await snapshots.record_failure(claim, factory)
            raise
        except Exception:
            snapshots.log.warning("Account statistics refresh failed; last successful generation retained")
            await snapshots.record_failure(claim, factory)
            return await refresh_status(user_id, factory)
    return await refresh_status(user_id, factory)
