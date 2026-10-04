from core import settings_store, trakt_sync
"""Trakt.tv integration router.

Endpoints:
  POST /trakt/auth/device/start   – Start device auth flow
  POST /trakt/auth/device/poll    – Poll for token completion
  DELETE /trakt/auth/disconnect   – Revoke token and clear stored credentials
  POST /trakt/sync                – Trigger a Trakt import (watched history + ratings)
  POST /trakt/import/upload       – Import watched history/ratings/lists from a Trakt
                                     data export zip (no VIP / API app required)
"""

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import trakt as trakt_client
from core.cloud_reconciliation import require_cloud_reconciliation
from core.trakt_export import MAX_TOTAL_SIZE, parse_trakt_export
from db import get_db
from dependencies import get_current_user
from models.base import CollectionSource
from models.sync import SyncJob, SyncStatus
from models.users import User, UserSettings
from models.global_settings import GlobalSettings

router = APIRouter()

# Trakt throttles authenticated writes to ~1/s (429 beyond). Run the push queue
# one request at a time with this gap so a big push can't 429-storm itself or
# starve the user's real-time scrobbles, which share the same quota (#327).
def _require_trakt_config(settings: UserSettings):
    if not settings.trakt_client_id or not settings.trakt_client_secret:
        raise HTTPException(
            status_code=503,
            detail="Trakt Client ID and Client Secret are not configured. Add them in Settings → Sync → Trakt.",
        )


# ── Device Authentication ─────────────────────────────────────────────────────

@router.post("/auth/device/start")
async def trakt_device_start(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Initiate device authentication. Returns user_code + verification_url."""
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = UserSettings(user_id=current_user.id)
        db.add(settings)

    _require_trakt_config(settings)

    data = await trakt_client.start_device_auth(settings.trakt_client_id)

    settings.trakt_device_code = data["device_code"]
    await db.commit()

    return {
        "user_code": data["user_code"],
        "verification_url": data["verification_url"],
        "expires_in": data["expires_in"],
        "interval": data["interval"],
    }


@router.post("/auth/device/poll")
async def trakt_device_poll(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Check if the user has authorized the device. Call repeatedly per the interval."""
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()

    if not settings or not settings.trakt_device_code:
        raise HTTPException(status_code=400, detail="No pending device authorization. Call /auth/device/start first.")

    _require_trakt_config(settings)

    try:
        token_data = await trakt_client.poll_device_token(
            settings.trakt_client_id,
            settings.trakt_client_secret,
            settings.trakt_device_code,
        )
    except Exception as exc:
        # Permanent failure (expired / denied)
        settings.trakt_device_code = None
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Authorization failed: {exc}")

    if token_data is None:
        # Still pending — tell the frontend to keep polling
        return {"status": "pending"}

    # Success — store the tokens
    settings.trakt_access_token = token_data["access_token"]
    settings.trakt_refresh_token = token_data["refresh_token"]
    settings.trakt_token_expires_at = token_data.get("expires_in", 0) + int(datetime.now(timezone.utc).timestamp())
    settings.trakt_device_code = None
    settings.trakt_history_cursor_at = None
    await db.commit()

    return {"status": "connected"}


@router.delete("/auth/disconnect")
async def trakt_disconnect(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revoke the Trakt token and clear stored credentials."""
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()

    if settings and settings.trakt_access_token:
        if settings.trakt_client_id and settings.trakt_client_secret:
            await trakt_client.revoke_token(
                settings.trakt_client_id,
                settings.trakt_client_secret,
                settings.trakt_access_token,
            )
        settings.trakt_access_token = None
        settings.trakt_refresh_token = None
        settings.trakt_token_expires_at = None
        settings.trakt_device_code = None
        settings.trakt_history_cursor_at = None
        await db.commit()

    return {"status": "disconnected"}


# ── Sync ─────────────────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_trakt(
    background_tasks: BackgroundTasks,
    full: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not full:
        from core.account_sync import start_account_pull
        return await start_account_pull(background_tasks, db, current_user.id)
    from core.account_sync import require_idle_account
    await require_idle_account(db, current_user.id)
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()

    _require_trakt_config(settings)

    if not settings or not settings.trakt_access_token:
        raise HTTPException(status_code=400, detail="Trakt is not connected")
    _tmdb_key = settings.tmdb_api_key
    if not _tmdb_key:
        _gs_r = await db.execute(select(GlobalSettings).where(GlobalSettings.id == 1))
        _gs = _gs_r.scalar_one_or_none()
        _tmdb_key = settings_store.get_server_tmdb_key(_gs)
    if not _tmdb_key:
        raise HTTPException(status_code=400, detail="TMDB API key required for sync")

    job = SyncJob(user_id=current_user.id, source=CollectionSource.trakt, status=SyncStatus.pending)
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(trakt_sync.run_trakt_sync, current_user.id, job.id, full)
    mode = "full resync" if full else "incremental sync"
    return {"status": "started", "job_id": job.id, "message": f"Trakt {mode} is running in the background"}


@router.post("/import/upload")
async def trakt_import_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    sync_watched: bool = Form(True),
    sync_ratings: bool = Form(True),
    sync_lists: bool = Form(True),
    sync_comments: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import watched history, ratings, lists, and/or comments from a Trakt data export zip.

    Unlike /trakt/sync, this doesn't require a Trakt API app (client ID/secret) —
    Trakt now requires a VIP subscription to create one, so this is the only way
    non-VIP users can get their Trakt data into Scrob. What to import is chosen
    per-upload (sync_watched/sync_ratings/sync_lists/sync_comments) rather than
    read from the trakt_sync_* preferences, which only gate the continuous OAuth pull.
    """
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip export files are accepted.")

    if not (sync_watched or sync_ratings or sync_lists or sync_comments):
        raise HTTPException(status_code=400, detail="Select at least one item to import.")

    # Read in bounded chunks rather than a single file.read() — otherwise an
    # oversized request body gets buffered into memory in full before the zip
    # is ever opened, regardless of what parse_trakt_export's own caps enforce.
    chunks: list[bytes] = []
    total_read = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total_read += len(chunk)
        if total_read > MAX_TOTAL_SIZE:
            raise HTTPException(status_code=413, detail="Export file is too large to import.")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        export_data = parse_trakt_export(content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()
    _tmdb_key = settings.tmdb_api_key if settings else None
    if not _tmdb_key:
        _gs_r = await db.execute(select(GlobalSettings).where(GlobalSettings.id == 1))
        _gs = _gs_r.scalar_one_or_none()
        _tmdb_key = settings_store.get_server_tmdb_key(_gs)
    if not _tmdb_key:
        raise HTTPException(status_code=400, detail="TMDB API key required for import")

    job = SyncJob(user_id=current_user.id, source=CollectionSource.trakt, status=SyncStatus.pending, job_type="import")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(trakt_sync.run_trakt_export_sync, current_user.id, job.id, export_data, sync_watched, sync_ratings, sync_lists, sync_comments)
    return {"status": "started", "job_id": job.id, "message": "Trakt export import is running in the background"}


@router.post("/push")
async def push_trakt(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()
    _require_trakt_config(settings)
    if not settings or not settings.trakt_access_token:
        raise HTTPException(status_code=400, detail="Trakt is not connected")
    if not (settings.trakt_push_watched or settings.trakt_push_ratings
            or settings.trakt_push_collection or settings.trakt_push_dropped):
        raise HTTPException(status_code=400, detail="Enable 'Scrob → Trakt' push flags first")
    await require_cloud_reconciliation(db, current_user.id, "trakt")
    job = SyncJob(user_id=current_user.id, source=CollectionSource.trakt, status=SyncStatus.pending, job_type="push")
    db.add(job)
    await db.commit()
    await db.refresh(job)
    background_tasks.add_task(trakt_sync._run_trakt_push, current_user.id, job.id)
    return {"status": "started", "job_id": job.id, "message": "Trakt push is running in the background"}
