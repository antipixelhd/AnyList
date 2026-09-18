"""Quota-conscious catalog score enrichment."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import mdblist
from models import GlobalSettings, Media, UserSettings
from models.base import MediaType

CACHE_TTL = timedelta(hours=24)


async def effective_mdblist_key(db: AsyncSession, user_id: int | None) -> str | None:
    if user_id is not None:
        personal = (await db.execute(
            select(UserSettings.mdblist_api_key).where(UserSettings.user_id == user_id)
        )).scalar_one_or_none()
        if personal:
            return personal
    global_key = (await db.execute(
        select(GlobalSettings.mdblist_api_key).where(GlobalSettings.id == 1)
    )).scalar_one_or_none()
    return global_key or None


async def refresh_external_scores(
    db: AsyncSession,
    media: Media,
    user_id: int | None,
    *,
    force: bool = False,
) -> bool:
    """Refresh a title at most daily; failures leave the prior cache intact."""
    if not media.tmdb_id or media.media_type not in (MediaType.movie, MediaType.series):
        return False
    now = datetime.utcnow()
    if not force and media.external_scores_updated_at and now - media.external_scores_updated_at < CACHE_TTL:
        return False
    key = await effective_mdblist_key(db, user_id)
    if not key:
        return False
    kind = "movie" if media.media_type == MediaType.movie else "show"
    values: dict[str, float | None] = {}
    for source, field in (
        ("imdb", "imdb_rating"),
        ("tomatoes", "rt_critic_score"),
        ("audience", "rt_audience_score"),
    ):
        try:
            values[field] = await mdblist.get_catalog_rating(key, kind, media.tmdb_id, source)
        except mdblist.MDBListAPIError:
            return False
    for field, value in values.items():
        setattr(media, field, value)
    media.external_scores_updated_at = now
    await db.commit()
    return True
