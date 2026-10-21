"""Shared catalogue and public-profile access policy."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from models import GlobalSettings, User
from models.base import PrivacyLevel


async def profile_header(db, user, owner, viewer):
    """Focused shared header; neither personal entries nor provider payloads."""
    from models.follows import Follow
    from models.tracking import TrackingPreferences
    combined, following, follows_you = (await db.execute(select(
        select(TrackingPreferences.combine_lists).where(TrackingPreferences.user_id == user.id).scalar_subquery(),
        select(Follow.id).where(Follow.follower_id == (viewer.id if viewer else -1), Follow.following_id == user.id).exists(),
        select(Follow.id).where(Follow.follower_id == user.id, Follow.following_id == (viewer.id if viewer else -1)).exists(),
    ))).one()
    profile = user.profile
    return dict(id=user.id, username=user.username, display_name=user.display_name or user.username,
        bio=profile.bio if profile else None,
        profile_color=profile.profile_color if profile else '#3db4f2',
        background_url=profile.background_url if profile else None, has_avatar=bool(profile and profile.avatar_path),
        owner=owner, following=following, follows_you=follows_you, combine_lists=combined is not False)


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
