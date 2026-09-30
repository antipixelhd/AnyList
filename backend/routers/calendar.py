from core import calendar_service
"""Episode calendar: real per-day schedule for the next 14 days, for shows
the user is collecting or watching (#194).

The original attempt at this (#243) built the calendar from each show's
next_episode_to_air/last_episode_to_air - single TMDB pointers, not a real
per-day list, so a multi-episode drop only ever showed one entry and the
calendar had gaps. This version fetches the actual season episode list (with
a real air_date per episode) for each candidate show's currently-airing
season and filters that to the target window instead.

Building it costs two TMDB calls per followed, still-running show (show
details for next_episode_to_air, then that season's full episode list), so
the computed payload is cached whole in user_calendar_cache and recomputed at
most once per TTL or on explicit refresh. cached_only=true (used by pages
that must render instantly) never waits on TMDB: it serves whatever cache
exists and warms it in the background otherwise.
"""

import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from dependencies import get_current_user_or_api_key
from models.calendar_cache import UserCalendarCache
from models.users import User

router = APIRouter()

@router.get("")
async def get_calendar(
    cached_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    if cached_only:
        row = (
            await db.execute(select(UserCalendarCache).where(UserCalendarCache.user_id == current_user.id))
        ).scalars().first()
        if calendar_service._is_cache_fresh(row):
            return {"computed_at": row.computed_at.isoformat(), "cached": True, "calendar": row.payload}
        asyncio.create_task(calendar_service._background_compute(current_user.id))
        return {"computed_at": None, "cached": False, "calendar": {"entries": []}}
    return await calendar_service._load_or_compute(db, current_user.id, force=False)


@router.post("/refresh")
async def refresh_calendar(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    return await calendar_service._load_or_compute(db, current_user.id, force=True)
