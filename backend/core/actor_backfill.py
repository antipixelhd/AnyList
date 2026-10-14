"""Bounded shared cast refresh for listed titles; no calls from statistics reads."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, or_, select

from core.media_credits import fill_screen_credits
from models.base import MediaType
from models.media import Media
from models.tracking import TrackedEntry
from models.show import Show

VERSION = 4
BATCH_SIZE = 25


async def backfill_actors(db, providers, *, limit=BATCH_SIZE, force=False, user_id=None):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    attrs = Media.tmdb_data
    membership = select(TrackedEntry.id).where(TrackedEntry.media_id == Media.id)
    if user_id is not None:
        membership = membership.where(TrackedEntry.user_id == user_id)
    sources = []
    if providers.tmdb_key:
        sources.append(Media.tmdb_id.is_not(None))
    if providers.tvdb_key:
        sources.extend([
            Media.tvdb_id.is_not(None),
            and_(Media.media_type == MediaType.series, select(Show.id).where(
                Show.tmdb_id == Media.tmdb_id, Show.tvdb_id.is_not(None)).correlate(Media).exists()),
            and_(Media.media_type == MediaType.movie, Media.imdb_id.is_not(None)),
        ])
    query = select(Media).where(Media.media_type.in_([MediaType.movie, MediaType.series]),
        exists(membership), or_(*sources) if sources else False)
    if not force:
        query = query.where(or_(attrs["actor_backfill_attempted_at"].astext.is_(None),
            attrs["actor_metadata_version"].astext != str(VERSION),
            attrs["actor_backfill_attempted_at"].astext < (now - timedelta(days=7)).isoformat()))
    rows = list((await db.scalars(query.order_by(attrs["actor_backfill_attempted_at"].astext.asc().nulls_first(), Media.id).limit(limit))).all())
    result = {"examined": len(rows), "updated": 0, "failed": 0}
    for row in rows:
        try:
            status = await fill_screen_credits(db, row, providers=providers, authoritative_primary=True)
            result['updated'] += int(status == 'updated')
            result['failed'] += int(status == 'failed')
            if status != 'deferred':
                row.tmdb_data = {**(row.tmdb_data or {}), 'actor_metadata_version': VERSION,
                                 'actor_backfill_attempted_at': now.isoformat()}
                await db.commit()
        except Exception:
            await db.rollback()
            result['failed'] += 1
    return result
