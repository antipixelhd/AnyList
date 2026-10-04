"""Account-bound manual and scheduled pull cycles using existing provider workers."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import mdblist_sync, server_sync, settings_store, simkl_sync, trakt_sync
from core.provider_changes import ChangeState, check_provider_changes
from db import engine
from models.base import CollectionSource
from models.connections import MediaServerConnection
from models.sync import SyncJob, SyncStatus
from models.users import User, UserSettings

ACTIVE = (SyncStatus.pending, SyncStatus.running)
TERMINAL = (SyncStatus.completed, SyncStatus.failed, SyncStatus.cancelled)
SERVER_RUNNERS = {
    "jellyfin": server_sync.run_jellyfin_sync, "emby": server_sync.run_emby_sync,
    "plex": server_sync.run_plex_sync, "nuvio": server_sync.run_nuvio_sync,
    "stremio": server_sync.run_stremio_sync, "arvio": server_sync.run_arvio_sync,
}
CLOUD_RUNNERS = {
    "trakt": trakt_sync.run_trakt_sync, "simkl": simkl_sync.run_simkl_sync,
    "mdblist": mdblist_sync.run_mdblist_sync,
}


def has_pull_options(conn) -> bool:
    fields = ("sync_collection", "sync_watched", "sync_playback")
    if conn.type in {"jellyfin", "emby", "plex"}:
        fields += ("sync_ratings",)
    if conn.type == "plex":
        fields += ("plex_sync_watchlist",)
    return any(getattr(conn, field, False) for field in fields)


def connected_cloud_sources(settings) -> list[str]:
    if settings is None:
        return []
    result = []
    for provider in CLOUD_RUNNERS:
        credential = "api_key" if provider == "mdblist" else "access_token"
        fields = ("sync_watched", "sync_ratings", "sync_watchlist", "sync_dropped") if provider == "mdblist" else (
            "sync_watched", "sync_ratings", "sync_lists", "sync_dropped")
        if getattr(settings, f"{provider}_{credential}", None) and any(
            getattr(settings, f"{provider}_{field}", False) for field in fields
        ):
            result.append(provider)
    return result


def next_pull_due(last_finished: datetime | None, interval: float | None) -> datetime | None:
    if interval is None:
        return None
    return last_finished + timedelta(hours=interval) if last_finished else datetime.min


async def require_idle_account(db, user_id: int) -> None:
    """Serialize admission across processes, including recovery resyncs/clears."""
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    active = (await db.execute(select(SyncJob.id).where(
        SyncJob.user_id == user_id, SyncJob.status.in_(ACTIVE),
        SyncJob.job_type.in_(("pull", "pull_cycle", "clear")),
    ).limit(1))).scalar_one_or_none()
    if active is not None:
        raise HTTPException(409, "An account sync is already running")


async def queue_account_pull(db, user_id: int, *, scheduled: bool = False) -> SyncJob | None:
    await require_idle_account(db, user_id)
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one_or_none()
    if scheduled:
        interval = settings.pull_sync_interval if settings else None
        if interval is None:
            return None
        last = (await db.execute(select(SyncJob.updated_at).where(
            SyncJob.user_id == user_id, SyncJob.job_type == "pull_cycle",
            SyncJob.status.in_(TERMINAL),
        ).order_by(SyncJob.updated_at.desc()).limit(1))).scalar_one_or_none()
        if next_pull_due(last, interval) > datetime.utcnow():
            return None
    connections = (await db.execute(select(MediaServerConnection).where(
        MediaServerConnection.user_id == user_id,
    ).order_by(MediaServerConnection.id))).scalars().all()
    targets = [(conn.type, conn.id) for conn in connections if conn.type in SERVER_RUNNERS and has_pull_options(conn)]
    targets += [(provider, None) for provider in connected_cloud_sources(settings)]
    if not targets:
        if scheduled:
            return None
        raise HTTPException(400, "Connect a provider and enable at least one pull option")
    if not await settings_store.get_effective_tmdb_key(db, settings):
        raise HTTPException(400, "TMDB API key required for sync")
    parent = SyncJob(user_id=user_id, source=CollectionSource.manual, job_type="pull_cycle",
                     status=SyncStatus.pending, total_items=len(targets), current_step="Pulling connected providers")
    db.add(parent)
    await db.flush()
    children = []
    for provider, connection_id in targets:
        child = SyncJob(user_id=user_id, source=CollectionSource(provider), connection_id=connection_id,
                        job_type="pull", status=SyncStatus.pending)
        db.add(child)
        await db.flush()
        children.append(child.id)
    parent.stats = {"child_job_ids": children}
    await db.commit()
    return parent


async def _run_provider(user_id: int, job_id: int) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db:
        job = await db.get(SyncJob, job_id)
        if job is None or job.status != SyncStatus.pending:
            return
        provider, connection_id = job.source.value, job.connection_id
        settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one_or_none()
        conn = await db.get(MediaServerConnection, connection_id) if connection_id else None
        if connection_id and (conn is None or conn.user_id != user_id):
            job.status = SyncStatus.failed
            job.error_message = "Connection no longer exists"
            await db.commit()
            return
        check = await check_provider_changes(db, user_id=user_id, provider=provider, settings=settings, conn=conn)
        # A cancellation may have arrived during the network check.
        await db.refresh(job)
        if job.status != SyncStatus.pending:
            return
        if check.state == ChangeState.unchanged:
            job.status = SyncStatus.completed
            job.current_step = "Unchanged"
            job.stats = {"unchanged": True, "provider_checkpoint": check.checkpoint}
            await db.commit()
            return
        await db.commit()
    try:
        if connection_id:
            await SERVER_RUNNERS[provider](user_id, job_id, 0, 0, connection_id)
        else:
            await CLOUD_RUNNERS[provider](user_id, job_id)
    except Exception:
        async with factory() as db:
            await db.execute(update(SyncJob).where(SyncJob.id == job_id, SyncJob.status.in_(ACTIVE)).values(
                status=SyncStatus.failed, error_message="Provider pull failed"))
            await db.commit()
        return
    async with factory() as db:
        job = await db.get(SyncJob, job_id)
        if job and job.status == SyncStatus.completed and check.checkpoint and not job.errors and not job.warnings:
            stats = job.stats or {}
            if not stats.get("errors") and stats.get("provider_snapshot_complete", True):
                job.stats = {**stats, "provider_checkpoint": check.checkpoint}
                await db.commit()


async def run_account_pull(user_id: int, job_id: int) -> None:
    from core.pull_cycle import allow_cycle_delivery, coordinated_pull_cycle
    from core.scheduler import _flush_pull_cycle
    from core.sync_jobs import mark_job_running_unless_cancelled

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db:
        if not await mark_job_running_unless_cancelled(db, job_id):
            return
        parent = await db.get(SyncJob, job_id)
        children = list((parent.stats or {}).get("child_job_ids", []))
    failed = False
    try:
        async with coordinated_pull_cycle(user_id) as state:
            results = await asyncio.gather(*(_run_provider(user_id, child_id) for child_id in children), return_exceptions=True)
            failed = any(isinstance(result, BaseException) for result in results)
            async with factory() as db:
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(current_step="Reconciling and delivering changes"))
                await db.commit()
            with allow_cycle_delivery(state):
                await _flush_pull_cycle(state)
    except asyncio.CancelledError:
        raise
    except Exception:
        failed = True
    finally:
        async with factory() as db:
            # No worker may leave a pending child stranded after the cycle ends.
            await db.execute(update(SyncJob).where(SyncJob.id.in_(children), SyncJob.status.in_(ACTIVE)).values(
                status=SyncStatus.failed, error_message="Account pull did not finish"))
            statuses = list((await db.execute(select(SyncJob.status).where(SyncJob.id.in_(children)))).scalars())
            parent = await db.get(SyncJob, job_id)
            if parent:
                cancelled = (parent.stats or {}).get("cancel_requested", False) or SyncStatus.cancelled in statuses
                failed = failed or SyncStatus.failed in statuses
                parent.status = SyncStatus.cancelled if cancelled else SyncStatus.failed if failed else SyncStatus.completed
                parent.processed_items = len(children)
                parent.errors = sum(status == SyncStatus.failed for status in statuses)
                parent.current_step = "Cancelled" if cancelled else "Completed with errors" if failed else "Completed"
                parent.updated_at = datetime.utcnow()
                if failed:
                    parent.error_message = "Some providers or outbound delivery failed; see provider jobs"
                await db.commit()


async def request_cycle_cancel(db, parent: SyncJob) -> None:
    """Keep admission locked until cancelled children have actually stopped."""
    stats = parent.stats or {}
    parent.stats = {**stats, "cancel_requested": True}
    await db.execute(update(SyncJob).where(SyncJob.id.in_(stats.get("child_job_ids", [])),
                                         SyncJob.status.in_(ACTIVE)).values(status=SyncStatus.cancelled))
    await db.commit()


async def start_account_pull(background_tasks, db, user_id: int) -> dict:
    job = await queue_account_pull(db, user_id)
    background_tasks.add_task(run_account_pull, user_id, job.id)
    return {"status": "started", "job_id": job.id, "message": "Account sync started"}
