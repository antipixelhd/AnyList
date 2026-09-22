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
) -> None:
    if not watched_ids and not ratings and not removed_ratings:
        return
    from routers.sync import _fan_out_changes_to_other_connections

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as db:
            settings = (await db.execute(select(UserSettings).where(
                UserSettings.user_id == user_id,
            ))).scalar_one_or_none()
            await _fan_out_changes_to_other_connections(
                db, user_id, None, watched_ids, ratings, settings,
                removed_ratings=removed_ratings,
            )
    except Exception:
        logger.exception("Local tracking delivery failed for user %s", user_id)


async def dispatch_local_watch_rollback(user_id: int, media_ids: set[int]) -> None:
    if not media_ids:
        return
    from routers.history import _push_watch_state

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as db:
            await _push_watch_state(db, user_id, sorted(media_ids), watched=False)
    except Exception:
        logger.exception("Local watch rollback delivery failed for user %s", user_id)
