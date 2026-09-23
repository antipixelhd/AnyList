"""Durable, truthful status for outbound work caused by an AnyList entry edit."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.tracking import StreamAction, TrackingDeliveryJob


async def create_tracking_delivery_job(
    db: AsyncSession, *, user_id: int, media_id: int, changes: dict,
) -> TrackingDeliveryJob:
    has_outbound_change = bool(changes.get("watched_media_ids") or changes.get("removed_watched_media_ids")
                               or changes.get("ratings") or changes.get("removed_ratings")
                               or changes.get("stream_actions"))
    job = TrackingDeliveryJob(
        user_id=user_id,
        media_id=media_id,
        state="queued" if has_outbound_change else "no_external_changes",
        changes=changes,
        detail=None if has_outbound_change else "This entry edit did not change a field mirrored to connected providers.",
    )
    db.add(job)
    await db.flush()
    return job


async def finish_tracking_delivery_job(job_id: int, *, error: str | None = None) -> None:
    """Record dispatcher outcome without equating a swallowed error with success."""
    from db import async_sessionmaker, engine

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db:
        job = (await db.execute(select(TrackingDeliveryJob).where(
            TrackingDeliveryJob.id == job_id,
        ))).scalar_one_or_none()
        if job is None:
            return
        if job.state == "failed" and error is None:
            return
        if error is None:
            stream_action_ids = [row.get("id") for row in (job.changes or {}).get("stream_actions", []) if row.get("id")]
            if stream_action_ids:
                pending = (await db.execute(select(StreamAction.id).where(
                    StreamAction.id.in_(stream_action_ids), StreamAction.state == "pending",
                ))).scalars().all()
                if pending:
                    job.state = "queued"
                    job.detail = "Provider work remains queued and has not been acknowledged."
                    await db.commit()
                    return
        job.state = "failed" if error else "attempted_unverified"
        job.detail = (
            f"The local outbound dispatcher raised {error}; provider delivery was not verified."
            if error else
            "The outbound dispatcher ran. Provider acceptance is not reported by every connector, so delivery remains unverified."
        )
        job.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()


async def start_tracking_delivery_job(db: AsyncSession, job_id: int) -> None:
    """Persist that provider dispatch was entered before making outbound calls."""
    job = (await db.execute(select(TrackingDeliveryJob).where(
        TrackingDeliveryJob.id == job_id,
    ))).scalar_one_or_none()
    if job is not None and job.state == "queued":
        job.state = "dispatching"
        job.detail = "Outbound delivery is being attempted; provider acceptance is not yet known."
        await db.commit()
