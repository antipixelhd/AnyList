"""Export only accepted changes from an already reconciled cloud pull."""
from __future__ import annotations

import logging

from sqlalchemy import select

from models.base import CollectionSource
from models.ratings import RatingChanges
from models.users import UserSettings

logger = logging.getLogger(__name__)


async def propagate_cloud_pull(
    db,
    *,
    user_id: int,
    provider: str,
    watched_ids: set[int],
    ratings: RatingChanges,
    complete: bool,
) -> None:
    if not complete or (not watched_ids and not ratings):
        return
    from core.cloud_reconciliation import cloud_push_is_approved

    if not await cloud_push_is_approved(db, user_id, provider):
        return
    from routers.sync import _fan_out_changes_to_other_connections

    try:
        settings = (await db.execute(select(UserSettings).where(
            UserSettings.user_id == user_id,
        ))).scalar_one_or_none()
        await _fan_out_changes_to_other_connections(
            db,
            user_id,
            None,
            watched_ids,
            ratings,
            settings,
            exclude_cloud_source=CollectionSource(provider),
        )
    except Exception:
        # The import has already committed. A destination outage cannot turn an
        # accepted source snapshot into a failed pull or erase its local state.
        logger.exception("Cloud pull propagation failed for %s user %s", provider, user_id)


async def propagate_media_server_pull(
    db, *, conn, watched_ids: set[int], ratings: RatingChanges,
) -> None:
    if not watched_ids and not ratings:
        return
    from models.tracking import StreamBaseline

    baseline = await db.get(StreamBaseline, conn.id)
    if not baseline or not baseline.approved:
        return
    from routers.sync import _fan_out_changes_to_other_connections

    try:
        settings = (await db.execute(select(UserSettings).where(
            UserSettings.user_id == conn.user_id,
        ))).scalar_one_or_none()
        await _fan_out_changes_to_other_connections(
            db, conn.user_id, conn.id, watched_ids, ratings, settings,
        )
    except Exception:
        logger.exception("Media server pull propagation failed for connection %s", conn.id)
