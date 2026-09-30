"""MDBList cloud synchronization endpoints."""

from __future__ import annotations
from core import mdblist_sync


from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.cloud_reconciliation import require_cloud_reconciliation
from db import get_db
from dependencies import get_current_user
from models.base import CollectionSource
from models.sync import SyncJob, SyncStatus
from models.users import User, UserSettings


router = APIRouter()
# MDBList's own watched_at for the same play can differ slightly between pulls
# (and doesn't always agree with a timestamp for the same watch reported by
# another source, e.g. after a push round-trips through a media server). A
# watch reported within this window of one we already have for the title is
# treated as the same watch rather than a rewatch. Mirrors
# core.server_sync.PLEX_WEBHOOK_RECONCILE_WINDOW, which reconciles the exact same
# kind of same-play-different-timestamp drift. See #148.
def _require_key(settings: UserSettings | None) -> UserSettings:
    if not settings or not settings.mdblist_api_key:
        raise HTTPException(
            status_code=400,
            detail="Configure a valid MDBList API key in Settings first",
        )
    return settings


@router.post("/sync")
async def sync_mdblist(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = _require_key(result.scalar_one_or_none())
    if not any((settings.mdblist_sync_watched, settings.mdblist_sync_ratings, settings.mdblist_sync_watchlist, settings.mdblist_sync_dropped)):
        raise HTTPException(status_code=400, detail="Enable at least one MDBList pull option")

    job = SyncJob(user_id=current_user.id, source=CollectionSource.mdblist, status=SyncStatus.pending)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    background_tasks.add_task(mdblist_sync.run_mdblist_sync, current_user.id, job.id)
    return {"status": "started", "job_id": job.id, "message": "MDBList sync is running in the background"}


@router.post("/push")
async def push_mdblist(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = _require_key(result.scalar_one_or_none())
    if not any((settings.mdblist_push_watched, settings.mdblist_push_ratings, settings.mdblist_push_watchlist,
                settings.mdblist_push_collection, settings.mdblist_push_dropped)):
        raise HTTPException(status_code=400, detail="Enable at least one MDBList push option")
    await require_cloud_reconciliation(db, current_user.id, "mdblist")

    job = SyncJob(
        user_id=current_user.id,
        source=CollectionSource.mdblist,
        status=SyncStatus.pending,
        job_type="push",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    background_tasks.add_task(mdblist_sync.run_mdblist_push, current_user.id, job.id)
    return {"status": "started", "job_id": job.id, "message": "MDBList push is running in the background"}


@router.delete("/auth/disconnect")
async def mdblist_disconnect(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear the stored MDBList API key."""
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()

    if settings:
        settings.mdblist_api_key = None
        await db.commit()

    return {"status": "disconnected"}
