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


def get_server_tmdb_key(global_settings: GlobalSettings | None) -> str | None:
    return (global_settings.tmdb_api_key if global_settings else None) or settings.tmdb_api_key


def get_server_tvdb_credentials(global_settings: GlobalSettings | None) -> tuple[str | None, str | None]:
    if global_settings and global_settings.tvdb_api_key:
        return global_settings.tvdb_api_key, global_settings.tvdb_subscriber_pin
    return settings.tvdb_api_key, settings.tvdb_subscriber_pin


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
    return get_server_tmdb_key(global_settings)


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


async def get_user_tvdb_key(db: AsyncSession, user_id: int) -> str | None:
    """Resolve the effective TVDB key (personal override, else server-wide) and
    register its subscriber PIN with the TVDB client so every downstream request
    for that key sends it on /login (#322/#325)."""
    from core import tvdb
    from models.global_settings import GlobalSettings
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    s = result.scalar_one_or_none()
    if s and s.tvdb_api_key:
        tvdb.set_subscriber_pin(s.tvdb_api_key, s.tvdb_subscriber_pin)
        return s.tvdb_api_key
    gs_result = await db.execute(select(GlobalSettings).where(GlobalSettings.id == 1))
    gs = gs_result.scalar_one_or_none()
    key, pin = get_server_tvdb_credentials(gs)
    if key:
        tvdb.set_subscriber_pin(key, pin)
        return key
    return None
