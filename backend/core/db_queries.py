"""Database query helpers shared by sync and projection services."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.events import WatchEvent

# Leave room below asyncpg's 32767-parameter limit for other query predicates.
_MAX_IN_PARAMS = 30_000


async def select_in_chunks(db: AsyncSession, stmt_builder, ids: list):
    """Execute a select statement using chunked IN clauses to avoid the 32767-parameter limit.
    stmt_builder(chunk) should return a SQLAlchemy select() statement for that chunk of IDs.
    Returns a flat list of all rows."""
    results = []
    for i in range(0, len(ids), _MAX_IN_PARAMS):
        chunk = ids[i : i + _MAX_IN_PARAMS]
        res = await db.execute(stmt_builder(chunk))
        results.extend(res.scalars().all())
    return results


async def latest_watched_at(db: AsyncSession, user_id: int, media_ids: list) -> dict:
    """Latest known completed watch date per media, chunked to avoid the 32767-parameter
    limit. An unknown-dated (None) play never masks an actual known date for the same
    media — only returned when it's the only play on record."""
    watched_at_by_media: dict[int, datetime | None] = {}
    for i in range(0, len(media_ids), _MAX_IN_PARAMS):
        chunk = media_ids[i : i + _MAX_IN_PARAMS]
        result = await db.execute(
            select(WatchEvent.media_id, WatchEvent.watched_at)
            .where(
                WatchEvent.user_id == user_id,
                WatchEvent.media_id.in_(chunk),
                WatchEvent.completed == True,
            )
            .order_by(WatchEvent.watched_at.desc().nulls_last())
        )
        for media_id, watched_at in result.all():
            watched_at_by_media.setdefault(media_id, watched_at)
    return watched_at_by_media
