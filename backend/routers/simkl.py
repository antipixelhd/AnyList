from core import settings_store, simkl_sync
"""Simkl integration router.

Endpoints:
  POST   /simkl/auth/pin/start   – Start PIN auth flow
  POST   /simkl/auth/pin/poll    – Poll for token completion
  DELETE /simkl/auth/disconnect  – Clear stored token
  POST   /simkl/sync             – Trigger a Simkl import (watched history + ratings + lists)
  POST   /simkl/push             – Push Scrob history/ratings to Simkl
"""


import httpx
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import simkl as simkl_client
from core.cloud_reconciliation import require_cloud_reconciliation
from db import get_db
from dependencies import get_current_user
from models.base import CollectionSource
from models.sync import SyncJob, SyncStatus
from models.users import User, UserSettings
from models.global_settings import GlobalSettings

logger = logging.getLogger(__name__)

router = APIRouter()

def _require_simkl_config(settings: UserSettings) -> None:
    if not settings.simkl_client_id:
        raise HTTPException(
            status_code=503,
            detail="Simkl Client ID is not configured. Add it in Settings → Sync → Simkl.",
        )


# ── PIN Authentication ────────────────────────────────────────────────────────

@router.post("/auth/pin/start")
async def simkl_pin_start(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Initiate PIN authentication. Returns user_code + url."""
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = UserSettings(user_id=current_user.id)
        db.add(settings)

    _require_simkl_config(settings)

    # Simkl answers a bad Client ID with an HTTP error or a 200 carrying
    # result "KO" and no user_code; either used to escape as a bare 500.
    try:
        data = await simkl_client.start_pin_auth(settings.simkl_client_id)
    except httpx.HTTPStatusError as exc:
        logger.warning("Simkl PIN start rejected: HTTP %s", exc.response.status_code)
        raise HTTPException(
            status_code=502,
            detail=f"Simkl rejected the request (HTTP {exc.response.status_code}). Check that the Client ID is correct.",
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Simkl PIN start failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Simkl. Try again in a moment.")
    if not isinstance(data, dict) or not data.get("user_code"):
        message = data.get("message") if isinstance(data, dict) else None
        raise HTTPException(
            status_code=502,
            detail=f"Simkl did not return a PIN{f': {message}' if message else ''}. Check that the Client ID is correct.",
        )

    settings.simkl_device_code = data["user_code"]
    await db.commit()

    return {
        "user_code": data["user_code"],
        "url": data.get("url") or f"https://simkl.com/pin/{data['user_code']}",
        "expires_in": data.get("expires_in", 600),
        "interval": data.get("interval", 5),
    }


@router.post("/auth/pin/poll")
async def simkl_pin_poll(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Check if the user has authorised via PIN. Call repeatedly per the interval."""
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()

    if not settings or not settings.simkl_device_code:
        raise HTTPException(status_code=400, detail="No pending PIN authorization. Call /auth/pin/start first.")

    _require_simkl_config(settings)

    try:
        access_token = await simkl_client.poll_pin_token(
            settings.simkl_client_id,
            settings.simkl_device_code,
        )
    except Exception as exc:
        settings.simkl_device_code = None
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Authorization failed: {exc}")

    if access_token is None:
        return {"status": "pending"}

    settings.simkl_access_token = access_token
    settings.simkl_device_code = None
    await db.commit()

    return {"status": "connected"}


@router.delete("/auth/disconnect")
async def simkl_disconnect(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear stored Simkl token."""
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()

    if settings:
        settings.simkl_access_token = None
        settings.simkl_device_code = None
        await db.commit()

    return {"status": "disconnected"}


# ── Sync helpers (shared with run_simkl_sync) ─────────────────────────────────

# ── Background sync job ───────────────────────────────────────────────────────

@router.post("/sync")
async def sync_simkl(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()

    _require_simkl_config(settings)

    if not settings or not settings.simkl_access_token:
        raise HTTPException(status_code=400, detail="Simkl is not connected")

    _tmdb_key = settings.tmdb_api_key if settings else None
    if not _tmdb_key:
        _gs_r = await db.execute(select(GlobalSettings).where(GlobalSettings.id == 1))
        _gs = _gs_r.scalar_one_or_none()
        _tmdb_key = settings_store.get_server_tmdb_key(_gs)
    if not _tmdb_key:
        raise HTTPException(status_code=400, detail="TMDB API key required for sync")

    job = SyncJob(user_id=current_user.id, source=CollectionSource.simkl, status=SyncStatus.pending)
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(simkl_sync.run_simkl_sync, current_user.id, job.id)
    return {"status": "started", "job_id": job.id, "message": "Simkl sync is running in the background"}


# ── Push (Scrob → Simkl) ──────────────────────────────────────────────────────

@router.post("/push")
async def push_simkl(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()
    _require_simkl_config(settings)
    if not settings or not settings.simkl_access_token:
        raise HTTPException(status_code=400, detail="Simkl is not connected")
    if not settings.simkl_push_watched and not settings.simkl_push_ratings:
        raise HTTPException(status_code=400, detail="Enable 'Scrob → Simkl' push flags first")
    await require_cloud_reconciliation(db, current_user.id, "simkl")
    job = SyncJob(user_id=current_user.id, source=CollectionSource.simkl, status=SyncStatus.pending, job_type="push")
    db.add(job)
    await db.commit()
    await db.refresh(job)
    background_tasks.add_task(simkl_sync._run_simkl_push, current_user.id, job.id)
    return {"status": "started", "job_id": job.id, "message": "Simkl push is running in the background"}
