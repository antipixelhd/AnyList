"""Shared catalogue and public-profile access policy."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from models import GlobalSettings, User
from models.base import PrivacyLevel


async def catalog_access(db, viewer):
    if viewer:
        return
    settings = await db.get(GlobalSettings, 1)
    if not settings or not settings.enable_logged_out_navigation:
        raise HTTPException(401, "Sign in to browse")


async def profile_access(db, username, viewer):
    user = (await db.execute(select(User).where(User.username == username).options(selectinload(User.profile)))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "Profile not found")
    owner = bool(viewer and viewer.id == user.id)
    if not owner and (not user.profile or user.profile.privacy_level != PrivacyLevel.public):
        raise HTTPException(403, "This profile is private")
    await catalog_access(db, viewer)
    return user, owner


async def anime_is_visible(db: AsyncSession) -> bool:
    settings=await db.get(GlobalSettings,1)
    return bool(settings and settings.show_anime)
