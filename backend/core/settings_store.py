"""Shared settings lookup and metadata credential resolution.

Request lookups cache both present and missing values on the database session.
Sync callers with an already-loaded UserSettings row resolve the global fallback
afresh, preserving the lookup behavior of long-running jobs.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from models.global_settings import GlobalSettings
from models.users import UserSettings


async def get_global_settings(db: AsyncSession, *, cached: bool = True) -> GlobalSettings | None:
    if cached and "global_settings" in db.info:
        return db.info["global_settings"]
    row = (await db.execute(select(GlobalSettings).where(GlobalSettings.id == 1))).scalar_one_or_none()
    if cached:
        db.info["global_settings"] = row
    return row


async def get_effective_tmdb_key(
    db: AsyncSession, user_settings: UserSettings | None, *, cached_global: bool = False,
) -> str | None:
    if user_settings and user_settings.tmdb_api_key:
        return user_settings.tmdb_api_key
    global_settings = await get_global_settings(db, cached=cached_global)
    return global_settings.tmdb_api_key if global_settings else None


async def get_user_tmdb_key(db: AsyncSession, user_id: int, *, cached: bool = True) -> str | None:
    cache_key = f"tmdb_key_{user_id}"
    if cached and cache_key in db.info:
        return db.info[cache_key]
    row = (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one_or_none()
    api_key = await get_effective_tmdb_key(db, row, cached_global=cached)
    if cached:
        db.info[cache_key] = api_key
    return api_key


def check_tmdb_key(api_key: str | None) -> bool:
    """The provider client can also use the configured environment fallback."""
    return bool(api_key or getattr(settings, "tmdb_api_key", None))
