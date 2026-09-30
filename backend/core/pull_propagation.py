"""Export only accepted changes from an already reconciled cloud pull."""
from __future__ import annotations
from core import outbound_sync

import logging

from sqlalchemy import select

from models.base import CollectionSource
from models.ratings import RatingChanges
from models.tracking import CloudBaseline
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
    baseline = (await db.execute(select(CloudBaseline).where(
        CloudBaseline.user_id == user_id,
        CloudBaseline.provider == provider,
    ))).scalar_one_or_none()
    if not baseline or not baseline.approved:
        return

    try:
        if watched_ids:
            from core.watch_intents import queue_watch_intents, dispatch_watch_intents
            await queue_watch_intents(db, user_id, watched_ids)
            await db.commit()
            await dispatch_watch_intents(db, user_id)
        settings = (await db.execute(select(UserSettings).where(
            UserSettings.user_id == user_id,
        ))).scalar_one_or_none()
        await outbound_sync.fan_out_changes(
            db,
            user_id,
            None,
            watched_ids,
            ratings,
            settings,
            exclude_cloud_source=CollectionSource(provider),
            durable_watch_media_ids=set(watched_ids),
        )
    except Exception:
        # The import has already committed. A destination outage cannot turn an
        # accepted source snapshot into a failed pull or erase its local state.
        logger.exception("Cloud pull propagation failed for %s user %s", provider, user_id)


async def propagate_media_server_pull(
    db, *, conn, watched_ids: set[int], ratings: RatingChanges,
    removed_watched_ids: set[int] | None = None,
) -> None:
    removed_watched_ids = removed_watched_ids or set()
    if not watched_ids and not ratings and not removed_watched_ids:
        return
    from models.tracking import StreamBaseline

    baseline = await db.get(StreamBaseline, conn.id)
    if not baseline or not baseline.approved:
        return

    try:
        from core.pull_cycle import defer_watch_removals
        deferred_removals = bool(removed_watched_ids and defer_watch_removals(
            conn.user_id, removed_watched_ids, conn.id,
        ))
        if removed_watched_ids and not deferred_removals:
            from core.watch_intents import queue_watch_intents, dispatch_watch_intents
            await queue_watch_intents(db, conn.user_id, removed_watched_ids,
                                      exclude_connection_id=conn.id)
            await db.commit()
            await dispatch_watch_intents(db, conn.user_id)
            from routers.history import _push_watch_state
            await _push_watch_state(db, conn.user_id, sorted(removed_watched_ids),
                                    watched=False, exclude_connection_id=conn.id,
                                    skip_stream_watch_writes=True)
        if watched_ids:
            from core.watch_intents import queue_watch_intents, dispatch_watch_intents
            await queue_watch_intents(
                db, conn.user_id, watched_ids,
                exclude_connection_id=conn.id,
            )
            await db.commit()
            await dispatch_watch_intents(db, conn.user_id)
        settings = (await db.execute(select(UserSettings).where(
            UserSettings.user_id == conn.user_id,
        ))).scalar_one_or_none()
        await outbound_sync.fan_out_changes(
            db, conn.user_id, conn.id, watched_ids, ratings, settings,
            durable_watch_media_ids=set(watched_ids),
        )
    except Exception:
        logger.exception("Media server pull propagation failed for connection %s", conn.id)
