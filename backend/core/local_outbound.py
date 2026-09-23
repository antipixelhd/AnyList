"""Run ordinary local tracking delivery after the local response is committed."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db import engine
from models.ratings import RatingChanges, RatingKey
from models.users import UserSettings

logger = logging.getLogger(__name__)


async def dispatch_local_tracking_delta(
    user_id: int,
    watched_ids: set[int],
    ratings: RatingChanges,
    removed_ratings: set[RatingKey],
    delivery_job_id: int | None = None,
) -> None:
    from routers.sync import _fan_out_changes_to_other_connections

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as db:
            if delivery_job_id is not None:
                from core.tracking_delivery import start_tracking_delivery_job
                await start_tracking_delivery_job(db, delivery_job_id)
            settings = (await db.execute(select(UserSettings).where(
                UserSettings.user_id == user_id,
            ))).scalar_one_or_none()
            if watched_ids or ratings or removed_ratings:
                await _fan_out_changes_to_other_connections(
                    db, user_id, None, watched_ids, ratings, settings,
                    removed_ratings=removed_ratings,
                )
            if delivery_job_id is not None:
                # Status changes may have queued stream restore/dismiss actions
                # even when there is no watch or rating delta.
                from core.stream_actions import dispatch_stream_actions
                from core.cloud_actions import dispatch_cloud_actions
                await dispatch_stream_actions(db, user_id)
                await dispatch_cloud_actions(db, user_id)
    except Exception:
        logger.exception("Local tracking delivery failed for user %s", user_id)
        if delivery_job_id is not None:
            from core.tracking_delivery import finish_tracking_delivery_job
            await finish_tracking_delivery_job(delivery_job_id, error="dispatcher_error")
    else:
        if delivery_job_id is not None:
            from core.tracking_delivery import finish_tracking_delivery_job
            await finish_tracking_delivery_job(delivery_job_id)


async def dispatch_local_watch_rollback(user_id: int, media_ids: set[int], delivery_job_id: int | None = None) -> None:
    if not media_ids:
        return
    from routers.history import _push_watch_state

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as db:
            if delivery_job_id is not None:
                from core.tracking_delivery import start_tracking_delivery_job
                await start_tracking_delivery_job(db, delivery_job_id)
            await _push_watch_state(db, user_id, sorted(media_ids), watched=False)
    except Exception:
        logger.exception("Local watch rollback delivery failed for user %s", user_id)
        if delivery_job_id is not None:
            from core.tracking_delivery import finish_tracking_delivery_job
            await finish_tracking_delivery_job(delivery_job_id, error="dispatcher_error")
    else:
        if delivery_job_id is not None:
            from core.tracking_delivery import finish_tracking_delivery_job
            await finish_tracking_delivery_job(delivery_job_id)
