from core import server_sync
from core import settings_store
from core.connection_identity import refresh_stream_connection
import asyncio
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from db import get_db
from models.media import Media
from models.show import Show
from models.collection import Collection, CollectionFile
from models.users import User, UserSettings
from models.connections import MediaServerConnection
from models.sync import SyncJob, SyncStatus
from models.events import WatchEvent
from models.library_selections import JellyfinLibrarySelection, EmbyLibrarySelection, PlexLibrarySelection
from models.season_override import ShowSeasonOverride
from datetime import datetime, timedelta
from models.base import MediaType, CollectionSource
from core import jellyfin, emby, plex, stremio, tmdb
from core.enrichment import (
    apply_media_change_safely,
    enrich_episode_from_tvdb,
    enrich_media,
)
from core.identity import coerce_id, link_show_ids
from core.translations import get_user_metadata_language

from dependencies import get_current_user, get_current_user_or_api_key
router = APIRouter()
SyncDb = Annotated[AsyncSession, Depends(get_db)]
SyncUser = Annotated[User, Depends(get_current_user)]


@router.post("/account")
async def sync_account(
    background_tasks: BackgroundTasks,
    db: SyncDb,
    current_user: SyncUser,
):
    from core.account_sync import start_account_pull

    return await start_account_pull(background_tasks, db, current_user.id)


class PullScheduleBody(BaseModel):
    interval: float | None = Field(default=None, ge=0.25, le=168, allow_inf_nan=False)


@router.get("/schedule")
async def get_pull_schedule(
    db: SyncDb,
    current_user: SyncUser,
):
    from core.account_sync import next_pull_due, TERMINAL

    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))).scalar_one_or_none()
    interval = settings.pull_sync_interval if settings else None
    last = (await db.execute(select(SyncJob.updated_at).where(
        SyncJob.user_id == current_user.id, SyncJob.job_type == "pull_cycle", SyncJob.status.in_(TERMINAL),
    ).order_by(SyncJob.updated_at.desc()).limit(1))).scalar_one_or_none()
    next_due = next_pull_due(last, interval)
    return {"interval": interval, "next_due_at": next_due if last else None}


@router.put("/schedule")
async def save_pull_schedule(
    body: PullScheduleBody,
    db: SyncDb,
    current_user: SyncUser,
):
    await db.execute(select(User.id).where(User.id == current_user.id).with_for_update())
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))).scalar_one_or_none()
    if settings is None:
        settings = UserSettings(user_id=current_user.id)
        db.add(settings)
    settings.pull_sync_interval = body.interval
    await db.commit()
    return {"interval": body.interval}

# Global semaphore — at most one sync running at a time across all users
# Keep the legacy router-level lock map as an alias for any in-process callers
# that still import it. All Stremio writers and pulls now share the adapter map.
_stremio_push_locks = stremio._connection_locks
# asyncpg hard limit is 32767 parameters per query; stay well under it
# Safety circuit breaker for _remove_stale_collection_files: a full scan that
# comes back empty/truncated (transient API hiccup, a library mid-rescan on
# the media server, a stale library-selection filter) never raises - the
# fetch helpers only raise on actual HTTP errors - so it would otherwise look
# indistinguishable from "the user deleted everything". Refuse to prune when
# most of an existing collection would vanish in one pass; a real deletion of
# that size is vanishingly rare, a bad scan is not.
# How long a PlexPendingPush row is kept around waiting for its echo before
# it's treated as stale. This only bounds how long a pending row survives to
# be matched against - the actual match still requires the echo's viewedAt to
# land within PLEX_CONFIRMED_RECONCILE_WINDOW of pushed_at, so widening this
# doesn't widen what counts as a match, it only affects how long we keep
# waiting (e.g. across a slow first pull) before giving up and letting a
# stale row be silently superseded by a genuinely new play (see GitHub #320).
# How long a webhook-created (provisional) WatchEvent's watched_at — this
# server's receipt time for the completion webhook — can plausibly lag behind
# Plex's own recorded viewedAt for that same play, before this stops treating
# them as the same play. Bounded by webhook delivery/processing latency only,
# not by content runtime — unlike a runtime-based guess, this reflects the
# actual mechanism of the drift (see GitHub #135).
# Same idea, but for a *confirmed* (non-provisional) WatchEvent - e.g. one
# created directly by "mark as watched" in Scrob's own UI, not a webhook
# receipt estimate. Marking something watched pushes to every push-enabled
# connection synchronously, in the same request (see history.py's
# watch_delivery.push_watch_state) - so a Plex play that shows up within a couple minutes
# of an existing confirmed watch for the same media is almost certainly that
# same push echoing back, not an independent second viewing. Kept much
# tighter than the provisional window: unlike a webhook's receipt-time
# estimate (which is expected to drift), a confirmed event's own timestamp is
# already meaningful and shouldn't be treated as approximate over a wide
# window (see GitHub #320).
async def _get_connection_or_404(db: AsyncSession, connection_id: int, user_id: int) -> MediaServerConnection:
    result = await db.execute(
        select(MediaServerConnection).where(
            MediaServerConnection.id == connection_id,
            MediaServerConnection.user_id == user_id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


@router.get("/connection/{connection_id}/plex-friends")
async def get_plex_friends(
    connection_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    conn = await _get_connection_or_404(db, connection_id, current_user.id)
    if conn.type != "plex":
        raise HTTPException(status_code=400, detail="Connection is not a Plex server")
    from core import plex as plex_client
    friends = await plex_client.get_all_friends(conn.plex_account_token)
    return {"friends": friends}


@router.get("/connection/{connection_id}/libraries")
async def get_connection_libraries(
    connection_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    conn = await _get_connection_or_404(db, connection_id, current_user.id)

    try:
        if conn.type in ("jellyfin", "emby"):
            client = jellyfin if conn.type == "jellyfin" else emby
            selection = JellyfinLibrarySelection if conn.type == "jellyfin" else EmbyLibrarySelection
            available = await client.get_libraries(conn.url, conn.token, conn.server_user_id)
            sel_result = await db.execute(
                select(selection).where(selection.connection_id == conn.id)
            )
            selected_ids = {row.library_id for row in sel_result.scalars().all()}
            libraries = [
                {"id": lib["Id"], "name": lib["Name"], "type": lib.get("CollectionType"), "selected": lib["Id"] in selected_ids}
                for lib in available if lib.get("CollectionType") in ("movies", "tvshows", "tv")
            ]
            return {"libraries": libraries, "all_selected": len(selected_ids) == 0}

        elif conn.type == "plex":
            available = await plex.get_libraries(conn.url, conn.token)
            sel_result = await db.execute(
                select(PlexLibrarySelection).where(PlexLibrarySelection.connection_id == conn.id)
            )
            selected_keys = {row.library_key for row in sel_result.scalars().all()}
            libraries = [
                {"key": lib["key"], "name": lib["title"], "type": lib.get("type"), "selected": lib["key"] in selected_keys}
                for lib in available if lib.get("type") in ("movie", "show")
            ]
            return {"libraries": libraries, "all_selected": len(selected_keys) == 0}

        elif conn.type in ("nuvio", "stremio", "arvio"):
            return {"libraries": [], "all_selected": True}

        else:
            raise HTTPException(status_code=400, detail=f"Unknown connection type: {conn.type}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach server: {e}")


@router.post("/connection/{connection_id}/scan")
async def trigger_library_scan(
    connection_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = await _get_connection_or_404(db, connection_id, current_user.id)

    try:
        if conn.type in ("nuvio", "stremio", "arvio"):
            settings_result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == current_user.id)
            )
            settings = settings_result.scalar_one_or_none()
            if not await settings_store.get_effective_tmdb_key(db, settings):
                raise HTTPException(status_code=400, detail="TMDB API key required")
            active_result = await db.execute(
                select(SyncJob)
                .where(
                    SyncJob.user_id == current_user.id,
                    SyncJob.connection_id == conn.id,
                    SyncJob.status.in_([SyncStatus.pending, SyncStatus.running]),
                )
                .limit(1)
            )
            active_job = active_result.scalar_one_or_none()
            if active_job:
                return {
                    "status": "started",
                    "job_id": active_job.id,
                    "message": f"{conn.type.capitalize()} sync is already running",
                }
            job = SyncJob(
                user_id=current_user.id,
                source=CollectionSource(conn.type),
                status=SyncStatus.pending,
                connection_id=conn.id,
                job_type="pull",
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            runner_fn = server_sync.run_arvio_sync if conn.type == "arvio" else (server_sync.run_nuvio_sync if conn.type == "nuvio" else server_sync.run_stremio_sync)
            background_tasks.add_task(
                runner_fn,
                current_user.id,
                job.id,
                0,
                0,
                conn.id,
            )
            return {
                "status": "started",
                "job_id": job.id,
                "message": f"{conn.type.capitalize()} library, watched status, and playback progress sync started",
            }
        if conn.type in ("jellyfin", "emby"):
            client = jellyfin if conn.type == "jellyfin" else emby
            ok = await client.scan_libraries(conn.url, conn.token)
        elif conn.type == "plex":
            sel_result = await db.execute(
                select(PlexLibrarySelection).where(PlexLibrarySelection.connection_id == conn.id)
            )
            selected_keys = [row.library_key for row in sel_result.scalars().all()]
            ok = await plex.scan_libraries(conn.url, conn.token, selected_keys)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown connection type: {conn.type}")

        if not ok:
            raise HTTPException(status_code=502, detail="Library scan request failed")
        return {"status": "ok", "message": "Library scan triggered successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach server: {e}")


@router.put("/connection/{connection_id}/libraries")
async def save_connection_libraries(
    connection_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = await _get_connection_or_404(db, connection_id, current_user.id)

    try:
        if conn.type in ("jellyfin", "emby"):
            client = jellyfin if conn.type == "jellyfin" else emby
            selection = JellyfinLibrarySelection if conn.type == "jellyfin" else EmbyLibrarySelection
            library_ids: list[str] = body.get("library_ids", [])
            available = await client.get_libraries(conn.url, conn.token, conn.server_user_id)
            name_map = {lib["Id"]: lib["Name"] for lib in available}
            await db.execute(delete(selection).where(selection.connection_id == conn.id))
            for lid in library_ids:
                if lid in name_map:
                    db.add(selection(user_id=current_user.id, connection_id=conn.id, library_id=lid, library_name=name_map[lid]))
            await db.commit()
            return {"saved": len(library_ids)}

        elif conn.type == "plex":
            library_keys: list[str] = body.get("library_keys", [])
            available = await plex.get_libraries(conn.url, conn.token)
            name_map = {lib["key"]: lib["title"] for lib in available}
            await db.execute(delete(PlexLibrarySelection).where(PlexLibrarySelection.connection_id == conn.id))
            for key in library_keys:
                if key in name_map:
                    db.add(PlexLibrarySelection(user_id=current_user.id, connection_id=conn.id, library_key=key, library_name=name_map[key]))
            await db.commit()
            return {"saved": len(library_keys)}

        elif conn.type in ("nuvio", "stremio", "arvio"):
            return {"saved": 0}

        else:
            raise HTTPException(status_code=400, detail=f"Unknown connection type: {conn.type}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach server: {e}")


@router.post("/connection/{connection_id}")
async def sync_connection(
    connection_id: int,
    background_tasks: BackgroundTasks,
    movie_limit: int = Query(default=0),
    show_limit: int = Query(default=0),
    full: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = await _get_connection_or_404(db, connection_id, current_user.id)
    if not full and not movie_limit and not show_limit:
        return await sync_account(background_tasks, db, current_user)
    from core.account_sync import require_idle_account
    await require_idle_account(db, current_user.id)

    if conn.type in ("stremio", "nuvio"):
        await db.execute(select(User.id).where(User.id == current_user.id).with_for_update())
        active_clear = (await db.execute(select(SyncJob.id).where(
            SyncJob.connection_id == connection_id,
            SyncJob.job_type == "clear",
            SyncJob.status.in_((SyncStatus.pending, SyncStatus.running)),
        ).limit(1))).scalar_one_or_none()
        if active_clear is not None:
            raise HTTPException(status_code=409, detail="A provider clear is in progress; retry sync after it completes")
        if full and (movie_limit or show_limit):
            raise HTTPException(status_code=400, detail="A full Stremio or Nuvio resync must fetch the complete provider snapshot")

    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = settings_result.scalar_one_or_none()
    if not await settings_store.get_effective_tmdb_key(db, settings):
        raise HTTPException(status_code=400, detail="TMDB API key required")


    source_map = {
        "jellyfin": CollectionSource.jellyfin,
        "emby": CollectionSource.emby,
        "plex": CollectionSource.plex,
        "nuvio": CollectionSource.nuvio,
        "stremio": CollectionSource.stremio,
        "arvio": CollectionSource.arvio,
    }
    source = source_map.get(conn.type)
    if not source:
        raise HTTPException(status_code=400, detail=f"Unknown connection type: {conn.type}")

    job = SyncJob(user_id=current_user.id, source=source, status=SyncStatus.pending, connection_id=connection_id, job_type="pull")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    runner_map = {
        "jellyfin": server_sync.run_jellyfin_sync,
        "emby": server_sync.run_emby_sync,
        "plex": server_sync.run_plex_sync,
        "nuvio": server_sync.run_nuvio_sync,
        "stremio": server_sync.run_stremio_sync,
        "arvio": server_sync.run_arvio_sync,
    }
    runner_args = (current_user.id, job.id, movie_limit, show_limit, connection_id)
    if conn.type in ("stremio", "nuvio"):
        runner_args = (*runner_args, full)
    background_tasks.add_task(runner_map[conn.type], *runner_args)
    return {"status": "started", "job_id": job.id, "message": f"{conn.type.capitalize()} sync is running in the background"}


# Shown on Connections when a full push cannot resolve a WatchEvent to a
# server item (#304). Must not look like an unmatched-TMDB pull warning:
# those have `title` + `reason` + `media_type` and a Match button.
@router.post("/connection/{connection_id}/push")
async def push_upstream(
    connection_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = await _get_connection_or_404(db, connection_id, current_user.id)
    from core.tracking_snapshot import require_stream_reconciliation
    await require_stream_reconciliation(db, conn)
    if not conn.push_enabled:
        raise HTTPException(
            status_code=400,
            detail="Enable 'Scrob → Server' push flags for this connection first",
        )

    source_map = {
        "jellyfin": CollectionSource.jellyfin,
        "emby": CollectionSource.emby,
        "plex": CollectionSource.plex,
        "nuvio": CollectionSource.nuvio,
        "stremio": CollectionSource.stremio,
    }
    source = source_map.get(conn.type, CollectionSource.jellyfin)
    job = SyncJob(user_id=current_user.id, source=source, status=SyncStatus.pending, connection_id=connection_id, job_type="push")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(server_sync._run_full_push, current_user.id, connection_id, job.id)
    return {"status": "started", "job_id": job.id, "message": "Full upstream push is running in the background"}


@router.post("/connection/{connection_id}/clear-data")
async def clear_connection_data(
    connection_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = await _get_connection_or_404(db, connection_id, current_user.id)
    if conn.type not in ("stremio", "nuvio"):
        raise HTTPException(status_code=400, detail="Clear data is supported only for Stremio and Nuvio")

    await db.execute(select(User.id).where(User.id == current_user.id).with_for_update())
    await refresh_stream_connection(db, conn)

    from core.streaming_clear import run_clear_data_job, selected_scope, target_identity
    scope = selected_scope(conn)
    if not any(scope.values()):
        raise HTTPException(status_code=400, detail="Enable at least one outbound collection, watched, or playback flag first")
    try:
        identity = target_identity(conn)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    active = (await db.execute(select(SyncJob.id).where(
        SyncJob.user_id == current_user.id,
        or_(SyncJob.connection_id == connection_id, SyncJob.job_type == "pull_cycle"),
        SyncJob.status.in_((SyncStatus.pending, SyncStatus.running)),
    ).limit(1))).scalar_one_or_none()
    if active:
        raise HTTPException(status_code=409, detail="Another sync or push is already running for this connection")

    from models.tracking import StreamBaseline
    baseline = (await db.execute(select(StreamBaseline).where(
        StreamBaseline.connection_id == connection_id,
    ).execution_options(populate_existing=True))).scalar_one_or_none()
    existing_guard = (baseline.snapshot or {}).get("clear_guard") if baseline else None
    if existing_guard:
        if not isinstance(existing_guard, dict) or existing_guard.get("scope") != scope or existing_guard.get("identity") != identity:
            raise HTTPException(
                status_code=409,
                detail="A previous clear did not complete for the same scope and account. Run a full resync before starting a different clear.",
            )

    source = CollectionSource.nuvio if conn.type == "nuvio" else CollectionSource.stremio
    job = SyncJob(
        user_id=current_user.id,
        source=source,
        status=SyncStatus.pending,
        connection_id=connection_id,
        job_type="clear",
        stats={"clear_scope": scope, "target_identity": identity},
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    background_tasks.add_task(run_clear_data_job, current_user.id, connection_id, job.id)
    return {"status": "started", "job_id": job.id, "message": "Selected provider data is being cleared in the background"}


async def _start_server_sync(
    provider: Literal["jellyfin", "emby", "plex"],
    background_tasks: BackgroundTasks,
    movie_limit: int,
    show_limit: int,
    db: AsyncSession,
    current_user: User,
):
    if not movie_limit and not show_limit:
        return await sync_account(background_tasks, db, current_user)
    from core.account_sync import require_idle_account
    await require_idle_account(db, current_user.id)
    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = settings_result.scalar_one_or_none()
    if not await settings_store.get_effective_tmdb_key(db, settings):
        raise HTTPException(status_code=400, detail="TMDB API key required")

    conn_result = await db.execute(
        select(MediaServerConnection).where(
            MediaServerConnection.user_id == current_user.id,
            MediaServerConnection.type == provider,
        ).order_by(MediaServerConnection.id.asc()).limit(1)
    )
    name = {"jellyfin": "Jellyfin", "emby": "Emby", "plex": "Plex"}[provider]
    if not conn_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"No {name} connection configured")

    job = SyncJob(user_id=current_user.id, source=CollectionSource(provider), status=SyncStatus.pending)
    db.add(job)
    await db.commit()
    await db.refresh(job)

    runner = {"jellyfin": server_sync.run_jellyfin_sync, "emby": server_sync.run_emby_sync, "plex": server_sync.run_plex_sync}[provider]
    background_tasks.add_task(runner, current_user.id, job.id, movie_limit, show_limit)
    return {"status": "started", "job_id": job.id, "message": f"{name} sync is running in the background"}


@router.post("/jellyfin")
async def sync_jellyfin(
    background_tasks: BackgroundTasks,
    movie_limit: int = Query(default=0),
    show_limit: int = Query(default=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _start_server_sync("jellyfin", background_tasks, movie_limit, show_limit, db, current_user)


@router.post("/emby")
async def sync_emby(
    background_tasks: BackgroundTasks,
    movie_limit: int = Query(default=0),
    show_limit: int = Query(default=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _start_server_sync("emby", background_tasks, movie_limit, show_limit, db, current_user)


@router.post("/plex")
async def sync_plex(
    background_tasks: BackgroundTasks,
    movie_limit: int = Query(default=0),
    show_limit: int = Query(default=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _start_server_sync("plex", background_tasks, movie_limit, show_limit, db, current_user)


@router.get("/status")
async def get_sync_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    # Keep the latest history bounded, but always include queued/running work.
    # Other jobs completing must not make a long-running job disappear.
    recent_ids = (
        select(SyncJob.id)
        .where(SyncJob.user_id == current_user.id)
        .order_by(SyncJob.updated_at.desc(), SyncJob.id.desc())
        .limit(20)
    )
    query = (
        select(SyncJob)
        .where(
            SyncJob.user_id == current_user.id,
            or_(SyncJob.status.in_([SyncStatus.pending, SyncStatus.running]), SyncJob.id.in_(recent_ids)),
        )
        .order_by(SyncJob.created_at.desc(), SyncJob.id.desc())
    )
    result = await db.execute(query)
    jobs = result.scalars().all()
    return jobs


@router.post("/heal")
async def heal_metadata(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-enrich all collection items that are missing poster/date metadata."""
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()
    if not await settings_store.get_effective_tmdb_key(db, settings):
        raise HTTPException(status_code=400, detail="TMDB API key required")

    effective_key = await settings_store.get_effective_tmdb_key(db, settings)
    job = SyncJob(user_id=current_user.id, source=CollectionSource.tmdb, job_type="heal", status=SyncStatus.pending)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    background_tasks.add_task(server_sync.run_heal, current_user.id, effective_key, job.id)
    return {"status": "started", "message": "Metadata heal is running in the background"}


@router.post("/abort")
async def abort_sync(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel active sync jobs, preserving destructive clears already running."""
    from core.account_sync import request_cycle_cancel
    parents = (await db.execute(select(SyncJob).where(
        SyncJob.user_id == current_user.id, SyncJob.job_type == "pull_cycle",
        SyncJob.status.in_([SyncStatus.pending, SyncStatus.running]),
    ))).scalars().all()
    for parent in parents:
        await request_cycle_cancel(db, parent)
    await db.execute(
        update(SyncJob)
        .where(SyncJob.user_id == current_user.id)
        .where(SyncJob.status.in_([SyncStatus.pending, SyncStatus.running]))
        .where(SyncJob.job_type != "pull_cycle")
        .where(~((SyncJob.job_type == "clear") & (SyncJob.status == SyncStatus.running)))
        .values(status=SyncStatus.cancelled, error_message="Cancelled by user", updated_at=func.now())
    )
    await db.commit()
    return {"status": "ok", "message": "Cancellable sync jobs have been cancelled"}


@router.post("/{job_id}/cancel")
async def cancel_sync_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel a single pending or running sync job owned by the current user.

    The background loop only notices on its next cooperative checkpoint (see
    raise_if_cancelled), so the job may keep running briefly after this returns.
    A destructive clear that has started must finish its verification instead.
    """
    parent = (await db.execute(select(SyncJob).where(
        SyncJob.id == job_id, SyncJob.user_id == current_user.id,
        SyncJob.job_type == "pull_cycle", SyncJob.status.in_([SyncStatus.pending, SyncStatus.running]),
    ))).scalar_one_or_none()
    if parent:
        from core.account_sync import request_cycle_cancel
        await request_cycle_cancel(db, parent)
        return {"status": "ok", "job_id": job_id}
    result = await db.execute(
        update(SyncJob)
        .where(SyncJob.id == job_id, SyncJob.user_id == current_user.id)
        .where(SyncJob.status.in_([SyncStatus.pending, SyncStatus.running]))
        .where(~((SyncJob.job_type == "clear") & (SyncJob.status == SyncStatus.running)))
        .values(status=SyncStatus.cancelled, error_message="Cancelled by user", updated_at=func.now())
        .returning(SyncJob.id)
    )
    cancelled_id = result.scalar_one_or_none()
    await db.commit()
    if cancelled_id is None:
        raise HTTPException(status_code=404, detail="No cancellable sync job with that id")
    return {"status": "ok", "job_id": job_id}


# ── Season override endpoints ─────────────────────────────────────────────────

class SeasonOverrideBody(BaseModel):
    source_show_tmdb_id: int
    source_season_number: int
    # Exactly one of these two must be set - the target is either a TMDB show
    # or a TVDB show (#178).
    target_show_tmdb_id: int | None = None
    target_show_tvdb_id: int | None = None
    target_season_number: int

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "SeasonOverrideBody":
        if bool(self.target_show_tmdb_id) == bool(self.target_show_tvdb_id):
            raise ValueError("Exactly one of target_show_tmdb_id or target_show_tvdb_id must be set")
        return self


@router.get("/season-overrides")
async def list_season_overrides(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    result = await db.execute(
        select(ShowSeasonOverride).where(ShowSeasonOverride.user_id == current_user.id)
    )
    overrides = result.scalars().all()

    # Resolve show titles for all distinct TMDB/TVDB IDs referenced by overrides
    all_tmdb_ids = {o.source_show_tmdb_id for o in overrides} | {o.target_show_tmdb_id for o in overrides if o.target_show_tmdb_id}
    all_tvdb_ids = {o.target_show_tvdb_id for o in overrides if o.target_show_tvdb_id}
    tmdb_title_map: dict[int, str] = {}
    tvdb_title_map: dict[int, str] = {}
    if all_tmdb_ids:
        shows_res = await db.execute(select(Show.tmdb_id, Show.title).where(Show.tmdb_id.in_(list(all_tmdb_ids))))
        for tmdb_id, title in shows_res.all():
            if tmdb_id is not None:
                tmdb_title_map[tmdb_id] = title
    if all_tvdb_ids:
        shows_res = await db.execute(select(Show.tvdb_id, Show.title).where(Show.tvdb_id.in_(list(all_tvdb_ids))))
        for tvdb_id, title in shows_res.all():
            if tvdb_id is not None:
                tvdb_title_map[tvdb_id] = title

    return [
        {
            "id": o.id,
            "source_show_tmdb_id": o.source_show_tmdb_id,
            "source_season_number": o.source_season_number,
            "source_show_title": tmdb_title_map.get(o.source_show_tmdb_id),
            "target_show_tmdb_id": o.target_show_tmdb_id,
            "target_show_tvdb_id": o.target_show_tvdb_id,
            "target_source": "tvdb" if o.target_show_tvdb_id else "tmdb",
            "target_season_number": o.target_season_number,
            "target_show_title": (
                tvdb_title_map.get(o.target_show_tvdb_id) if o.target_show_tvdb_id
                else tmdb_title_map.get(o.target_show_tmdb_id)
            ),
        }
        for o in overrides
    ]


@router.post("/season-overrides")
async def create_season_override(
    body: SeasonOverrideBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = await db.execute(
        select(ShowSeasonOverride).where(
            ShowSeasonOverride.user_id == current_user.id,
            ShowSeasonOverride.source_show_tmdb_id == body.source_show_tmdb_id,
            ShowSeasonOverride.source_season_number == body.source_season_number,
        )
    )
    override = existing.scalar_one_or_none()
    if override:
        # Clear whichever target field isn't set - editing a TMDB-targeted
        # remap into a TVDB one (or vice versa) must not leave a stale id
        # from the previous target behind.
        override.target_show_tmdb_id = body.target_show_tmdb_id
        override.target_show_tvdb_id = body.target_show_tvdb_id
        override.target_season_number = body.target_season_number
    else:
        override = ShowSeasonOverride(
            user_id=current_user.id,
            source_show_tmdb_id=body.source_show_tmdb_id,
            source_season_number=body.source_season_number,
            target_show_tmdb_id=body.target_show_tmdb_id,
            target_show_tvdb_id=body.target_show_tvdb_id,
            target_season_number=body.target_season_number,
        )
        db.add(override)
    await db.commit()
    await db.refresh(override)
    return {
        "id": override.id,
        "source_show_tmdb_id": override.source_show_tmdb_id,
        "source_season_number": override.source_season_number,
        "target_show_tmdb_id": override.target_show_tmdb_id,
        "target_show_tvdb_id": override.target_show_tvdb_id,
        "target_season_number": override.target_season_number,
    }


@router.delete("/season-overrides/{override_id}")
async def delete_season_override(
    override_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ShowSeasonOverride).where(
            ShowSeasonOverride.id == override_id,
            ShowSeasonOverride.user_id == current_user.id,
        )
    )
    override = result.scalar_one_or_none()
    if not override:
        raise HTTPException(status_code=404, detail="Override not found")
    await db.delete(override)
    await db.commit()
    return {"status": "ok"}


@router.post("/season-overrides/{override_id}/apply")
async def apply_season_override(
    override_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remap existing collection episodes to the target show/season and re-enrich metadata."""
    result = await db.execute(
        select(ShowSeasonOverride).where(
            ShowSeasonOverride.id == override_id,
            ShowSeasonOverride.user_id == current_user.id,
        )
    )
    override = result.scalar_one_or_none()
    if not override:
        raise HTTPException(status_code=404, detail="Override not found")

    tmdb_api_key = await settings_store.get_effective_tmdb_key(db, None)
    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = settings_result.scalar_one_or_none()
    if settings and settings.tmdb_api_key:
        tmdb_api_key = settings.tmdb_api_key
    if not tmdb_api_key:
        raise HTTPException(status_code=400, detail="TMDB API key required")

    # Find source show by tmdb_id
    source_show_result = await db.execute(
        select(Show).where(Show.tmdb_id == override.source_show_tmdb_id)
    )
    source_show = source_show_result.scalar_one_or_none()
    if not source_show:
        raise HTTPException(status_code=404, detail="Source show not found in local DB")

    # Find all user-collection episodes for (source_show, source_season)
    ep_result = await db.execute(
        select(Media)
        .join(Collection, Collection.media_id == Media.id)
        .where(
            Collection.user_id == current_user.id,
            Media.show_id == source_show.id,
            Media.season_number == override.source_season_number,
            Media.media_type == MediaType.episode,
        )
    )
    episodes = ep_result.scalars().all()
    if not episodes:
        return {"status": "ok", "remapped": 0}

    if override.target_show_tvdb_id:
        # ── TVDB target (#178) - some shows have season/episode structures
        # that only line up under TVDB's numbering, not TMDB's, so the remap
        # target needs to be able to point at a TVDB show too. ──────────────
        from core import tvdb as tvdb_client

        tvdb_api_key = await settings_store.get_user_tvdb_key(db, current_user.id)
        if not tvdb_api_key:
            raise HTTPException(status_code=400, detail="TVDB API key required")
        tvdb_lang = tvdb_client.tvdb_language(await get_user_metadata_language(db, current_user.id))

        target_show_result = await db.execute(select(Show).where(Show.tvdb_id == override.target_show_tvdb_id))
        target_show = target_show_result.scalar_one_or_none()
        if not target_show:
            try:
                raw_series = await tvdb_client.get_series(override.target_show_tvdb_id, tvdb_api_key)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Could not fetch target show from TVDB: {e}")
            show_fmt = tvdb_client.format_series(raw_series, language=tvdb_lang)
            target_show = Show(
                tvdb_id=override.target_show_tvdb_id,
                tmdb_id=None,
                canonical_source="tvdb",
                title=show_fmt.get("title") or f"TVDB #{override.target_show_tvdb_id}",
                original_title=show_fmt.get("original_title"),
                overview=show_fmt.get("overview"),
                poster_path=show_fmt.get("poster_path"),
                backdrop_path=show_fmt.get("backdrop_path"),
                status=show_fmt.get("status"),
                first_air_date=show_fmt.get("first_air_date"),
                last_air_date=show_fmt.get("last_air_date"),
                tmdb_data={"seasons": show_fmt.get("seasons", []), "genres": show_fmt.get("genres", []), "source": "tvdb"},
            )
            db.add(target_show)
            await db.flush()

        try:
            raw_eps = await tvdb_client.get_series_episodes(
                override.target_show_tvdb_id, override.target_season_number, tvdb_api_key, language=tvdb_lang
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Could not fetch target season from TVDB: {e}")
        tvdb_ep_map = {e.get("number"): e for e in raw_eps}

        async def remap_episode_tvdb(media: Media, raw_ep: dict | None) -> None:
            media.show_id = target_show.id
            media.season_number = override.target_season_number
            if raw_ep:
                await enrich_episode_from_tvdb(media, tvdb_client.format_episode(raw_ep))

        for media in episodes:
            raw_ep = tvdb_ep_map.get(media.episode_number)
            await apply_media_change_safely(
                db, media, lambda media=media, raw_ep=raw_ep: remap_episode_tvdb(media, raw_ep)
            )

        await db.commit()
        return {"status": "ok", "remapped": len(episodes)}

    # Find or create the target Show
    target_show_result = await db.execute(
        select(Show).where(Show.tmdb_id == override.target_show_tmdb_id)
    )
    target_show = target_show_result.scalar_one_or_none()
    if not target_show:
        try:
            show_data = await tmdb.get_show(override.target_show_tmdb_id, api_key=tmdb_api_key)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Could not fetch target show from TMDB: {e}")
        seasons_meta = [
            {
                "season_number": s["season_number"],
                "name": s.get("name"),
                "overview": s.get("overview"),
                "poster_path": tmdb.poster_url(s.get("poster_path")),
                "episode_count": s.get("episode_count"),
                "air_date": s.get("air_date"),
            }
            for s in show_data.get("seasons", [])
        ]
        target_show = Show(
            tmdb_id=override.target_show_tmdb_id,
            title=show_data.get("name") or show_data.get("original_name"),
            original_title=show_data.get("original_name"),
            overview=show_data.get("overview"),
            poster_path=tmdb.poster_url(show_data.get("poster_path")),
            backdrop_path=tmdb.poster_url(show_data.get("backdrop_path"), size="w1280"),
            tmdb_rating=show_data.get("vote_average"),
            status=show_data.get("status"),
            tagline=show_data.get("tagline"),
            first_air_date=show_data.get("first_air_date"),
            last_air_date=show_data.get("last_air_date"),
            tmdb_data={**show_data, "seasons": seasons_meta, "genres": [g["name"] if isinstance(g, dict) else g for g in show_data.get("genres", [])]},
        )
        db.add(target_show)
        await db.flush()

    # Fetch TMDB season data for the target season
    try:
        season_data = await tmdb.get_season(override.target_show_tmdb_id, override.target_season_number, api_key=tmdb_api_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch target season from TMDB: {e}")

    ep_map = {ep["episode_number"]: ep for ep in season_data.get("episodes", [])}

    def remap_episode(media: Media, ep: dict | None) -> None:
        media.show_id = target_show.id
        media.season_number = override.target_season_number
        if ep:
            media.tmdb_id = ep.get("id") or media.tmdb_id
            media.title = ep.get("name") or media.title
            media.overview = ep.get("overview")
            media.poster_path = tmdb.poster_url(ep.get("still_path"), size="w500")
            media.release_date = ep.get("air_date")
            media.tmdb_rating = ep.get("vote_average")
            media.runtime = ep.get("runtime") or media.runtime  # see #169
            media.tmdb_data = {"runtime": ep.get("runtime"), "cast": []}

    # Remap and re-enrich episodes
    for media in episodes:
        ep = ep_map.get(media.episode_number)
        await apply_media_change_safely(db, media, lambda media=media, ep=ep: remap_episode(media, ep))

    await db.commit()
    return {"status": "ok", "remapped": len(episodes)}


# ── Unmatched show matching ───────────────────────────────────────────────────

class MatchUnmatchedBody(BaseModel):
    show_title: str
    tmdb_id: int | None = None
    tvdb_id: int | None = None


@router.post("/match-unmatched-show")
async def match_unmatched_show(
    body: MatchUnmatchedBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Link unmatched local episodes (no tmdb_id/show_id) to a TMDB or TVDB show."""
    if not body.tmdb_id and not body.tvdb_id:
        raise HTTPException(status_code=400, detail="Either tmdb_id or tvdb_id is required")

    from sqlalchemy import func as sa_func

    ep_result = await db.execute(
        select(Media)
        .join(Collection, Collection.media_id == Media.id)
        .where(
            Collection.user_id == current_user.id,
            Media.tmdb_id.is_(None),
            Media.show_id.is_(None),
            Media.media_type == MediaType.episode,
            sa_func.lower(Media.tmdb_data["show_title"].astext) == body.show_title.lower(),
        )
        .distinct()
        .order_by(Media.id)
    )
    episodes = ep_result.scalars().all()
    if not episodes:
        # Episodes may already be linked (matched in a previous session before warning stamping
        # existed). Detect that case: find any matched episode for this show_title and stamp
        # warnings so the panel reflects the existing match.
        already_matched_result = await db.execute(
            select(Media)
            .join(Collection, Collection.media_id == Media.id)
            .where(
                Collection.user_id == current_user.id,
                Media.show_id.isnot(None),
                Media.media_type == MediaType.episode,
                sa_func.lower(Media.tmdb_data["show_title"].astext) == body.show_title.lower(),
            )
            .options(selectinload(Media.show))
            .limit(1)
        )
        already_matched_ep = already_matched_result.scalar_one_or_none()
        if already_matched_ep and already_matched_ep.show:
            target_show = already_matched_ep.show
            # Stamp warnings for shows that were matched before stamping was introduced
            title_lower = body.show_title.lower()
            jobs_res = await db.execute(
                select(SyncJob).where(
                    SyncJob.user_id == current_user.id,
                    SyncJob.status == SyncStatus.completed,
                    SyncJob.warnings.isnot(None),
                )
            )
            for job in jobs_res.scalars().all():
                if not job.warnings:
                    continue
                new_warnings = []
                changed = False
                for w in job.warnings:
                    if w.get("matched"):
                        new_warnings.append(w)
                        continue
                    if (
                        (w.get("series_name") or "").lower() == title_lower
                        or (w.get("title") or "").lower() == title_lower
                    ):
                        new_warnings.append({
                            **w,
                            "matched": True,
                            "matched_tvdb_id": target_show.tvdb_id,
                            "matched_show_id": target_show.tmdb_id,
                            "matched_show_title": target_show.title,
                        })
                        changed = True
                    else:
                        new_warnings.append(w)
                if changed:
                    job.warnings = new_warnings
                    flag_modified(job, "warnings")
            await db.commit()
            return {
                "status": "ok",
                "matched": 0,
                "skipped": 0,
                "tvdb_id": target_show.tvdb_id,
                "show_id": target_show.tmdb_id,
            }

        # Locate stub episodes via source_id recorded in SyncJob warnings.
        # Scan all warnings (stamped or not) — once all episode warnings are stamped,
        # the unmatched-only filter would find nothing and we'd never reach the TVDB/TMDB path.
        title_lower = body.show_title.lower()
        stub_source_ids: list[str] = []
        stub_warn_res = await db.execute(
            select(SyncJob.warnings).where(
                SyncJob.user_id == current_user.id,
                SyncJob.status == SyncStatus.completed,
                SyncJob.warnings.isnot(None),
            ).order_by(SyncJob.created_at.desc())
        )
        for (warnings,) in stub_warn_res.all():
            if not warnings:
                continue
            for w in warnings:
                warn_title = (w.get("series_name") or "").lower()
                if warn_title == title_lower and w.get("source_id"):
                    stub_source_ids.append(str(w["source_id"]))
        if stub_source_ids:
            stub_ep_res = await db.execute(
                select(Media)
                .join(Collection, Collection.media_id == Media.id)
                .join(CollectionFile, CollectionFile.collection_id == Collection.id)
                .where(
                    Collection.user_id == current_user.id,
                    CollectionFile.source_id.in_(stub_source_ids),
                    Media.media_type == MediaType.episode,
                )
                .options(selectinload(Media.show))
                .distinct()
            )
            stub_episodes = stub_ep_res.scalars().all()
            # Use all stub episodes for matching, including any already linked to a stale show.
            episodes = stub_episodes

        if not episodes:
            raise HTTPException(status_code=404, detail="No unmatched episodes found for this show title")

    from collections import defaultdict
    seasons_map: dict[int, list] = defaultdict(list)
    for ep in episodes:
        if ep.season_number is not None:
            seasons_map[ep.season_number].append(ep)

    matched = 0
    skipped = 0
    sem = asyncio.Semaphore(10)

    from core.episode_order import _merge_episode_media

    async def _apply_episode(media: Media, mutate) -> None:
        """Apply one episode match. If another stub already owns this episode's
        id - which happens when the user keeps two library versions of the same
        show (a colour and a B&W cut, say) - fold this stub's files and history
        into that row instead of leaving it stranded as 'unmatched'."""
        loser_id = media.id
        result = await apply_media_change_safely(db, media, mutate)
        if result is not None and result is not media:
            try:
                async with db.begin_nested():
                    await db.refresh(media)
                    await _merge_episode_media(db, result, media, keep_divergent_files=True)
            except Exception:
                server_sync.logger.exception(
                    "match-unmatched-show: could not fold duplicate stub media %s into %s",
                    loser_id, result.id,
                )

    if body.tvdb_id:
        # ── TVDB path ──────────────────────────────────────────────────────
        from core import tvdb as tvdb_client

        tvdb_api_key = await settings_store.get_user_tvdb_key(db, current_user.id)
        if not tvdb_api_key:
            raise HTTPException(status_code=400, detail="TVDB API key required")
        tvdb_lang = tvdb_client.tvdb_language(await get_user_metadata_language(db, current_user.id))

        # Find or create Show row keyed by tvdb_id
        target_show_result = await db.execute(select(Show).where(Show.tvdb_id == body.tvdb_id))
        target_show = target_show_result.scalar_one_or_none()
        try:
            raw = await tvdb_client.get_series(body.tvdb_id, tvdb_api_key)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Could not fetch show from TVDB: {e}")
        show_fmt = tvdb_client.format_series(raw, language=tvdb_lang)
        if not target_show:
            target_show = Show(
                tvdb_id=body.tvdb_id,
                tmdb_id=None,
                canonical_source="tvdb",
                title=show_fmt["title"] or body.show_title,
                original_title=show_fmt.get("original_title"),
                overview=show_fmt.get("overview"),
                poster_path=show_fmt.get("poster_path"),
                backdrop_path=show_fmt.get("backdrop_path"),
                status=show_fmt.get("status"),
                first_air_date=show_fmt.get("first_air_date"),
                last_air_date=show_fmt.get("last_air_date"),
                tmdb_data={"seasons": show_fmt.get("seasons", []), "genres": show_fmt.get("genres", []), "source": "tvdb"},
            )
            db.add(target_show)
            await db.flush()
        else:
            target_show.title = show_fmt["title"] or body.show_title or target_show.title
            target_show.original_title = show_fmt.get("original_title") or target_show.original_title
            target_show.overview = show_fmt.get("overview") or target_show.overview
            target_show.poster_path = show_fmt.get("poster_path") or target_show.poster_path
            target_show.backdrop_path = show_fmt.get("backdrop_path") or target_show.backdrop_path
            target_show.status = show_fmt.get("status") or target_show.status
            target_show.first_air_date = show_fmt.get("first_air_date") or target_show.first_air_date
            target_show.last_air_date = show_fmt.get("last_air_date") or target_show.last_air_date
            target_show.tmdb_data = {"seasons": show_fmt.get("seasons", []), "genres": show_fmt.get("genres", []), "source": "tvdb"}

        async def _fetch_season_tvdb(season_number: int) -> dict | None:
            async with sem:
                try:
                    raw_eps = await tvdb_client.get_series_episodes(body.tvdb_id, season_number, tvdb_api_key, language=tvdb_lang)
                except Exception:
                    return None
                return {e.get("number"): e for e in raw_eps}

        def apply_tvdb_episode(media: Media, ep: dict | None) -> None:
            media.show_id = target_show.id
            if ep:
                tvdb_ep_id = ep.get("id")
                # TVDB episode id lives in its own column (migration tvdb1st);
                # tmdb_id stays NULL for an episode TMDB doesn't have.
                if tvdb_ep_id:
                    media.tvdb_id = int(tvdb_ep_id)
                # TVDB sometimes has an episode with no name at all (see #173) -
                # media.title is NOT NULL, so a brand-new row needs a fallback.
                # Episode 0 is a real episode number, not "missing", hence the
                # explicit None checks rather than truthiness.
                ep_number = ep.get("number")
                if ep_number is None:
                    ep_number = media.episode_number
                fallback_title = f"Episode {ep_number}" if ep_number is not None else "Untitled Episode"
                media.title = ep.get("name") or media.title or fallback_title
                media.overview = ep.get("overview")
                if ep.get("image"):
                    media.poster_path = tvdb_client._image_url(ep["image"])
                media.release_date = ep.get("aired")
                # See enrich_media's matching comment (#169) - top-level runtime,
                # not just tmdb_data.runtime, is what Now Playing needs.
                media.runtime = ep.get("runtime") or media.runtime
                media.tmdb_data = {**(media.tmdb_data or {}), "runtime": ep.get("runtime"), "tvdb_episode_id": tvdb_ep_id, "source": "tvdb"}

        season_numbers = list(seasons_map.keys())
        # Fetches run concurrently (bounded by sem); mutations are applied sequentially
        # afterward since a single AsyncSession can't safely flush from concurrent tasks.
        ep_maps = await asyncio.gather(*[_fetch_season_tvdb(sn) for sn in season_numbers])
        for season_number, ep_map in zip(season_numbers, ep_maps):
            season_episodes = seasons_map[season_number]
            if ep_map is None:
                for media in season_episodes:
                    await apply_media_change_safely(db, media, lambda media=media: apply_tvdb_episode(media, None))
                skipped += len(season_episodes)
                continue
            for media in season_episodes:
                ep = ep_map.get(media.episode_number)
                await _apply_episode(media, lambda media=media, ep=ep: apply_tvdb_episode(media, ep))
                if ep:
                    matched += 1
                else:
                    skipped += 1

    else:
        # ── TMDB path (original behaviour) ────────────────────────────────
        settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
        settings = settings_result.scalar_one_or_none()
        tmdb_api_key = await settings_store.get_effective_tmdb_key(db, settings)
        if not tmdb_api_key:
            raise HTTPException(status_code=400, detail="TMDB API key required")

        try:
            show_data = await tmdb.get_show(body.tmdb_id, api_key=tmdb_api_key)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Could not fetch show from TMDB: {e}")
        seasons_meta = [
            {
                "season_number": s["season_number"],
                "name": s.get("name"),
                "overview": s.get("overview"),
                "poster_path": tmdb.poster_url(s.get("poster_path")),
                "episode_count": s.get("episode_count"),
                "air_date": s.get("air_date"),
            }
            for s in show_data.get("seasons", [])
        ]

        # Prefer an existing show that shares the TVDB cross-reference from TMDB external_ids.
        # This consolidates TMDB matches with auto-matched TVDB shows so both versions of
        # the same Plex show (e.g. color + B&W) end up on the same show page.
        tmdb_tvdb_id = (show_data.get("external_ids") or {}).get("tvdb_id")
        target_show = None
        if tmdb_tvdb_id:
            tvdb_cross_result = await db.execute(select(Show).where(Show.tvdb_id == tmdb_tvdb_id))
            target_show = tvdb_cross_result.scalar_one_or_none()

        if target_show is not None:
            # Found a TVDB-matched show to consolidate with.
            # Clear tmdb_id from any stale TMDB-only show that previously claimed this TMDB ID,
            # then re-home its episodes to target_show so they aren't orphaned.
            if target_show.tmdb_id != body.tmdb_id:
                displaced_result = await db.execute(select(Show).where(Show.tmdb_id == body.tmdb_id))
                displaced_show = displaced_result.scalar_one_or_none()
                if displaced_show and displaced_show.id != target_show.id:
                    await db.execute(
                        update(Media).where(Media.show_id == displaced_show.id).values(show_id=target_show.id)
                    )
                    # Release the unique tmdb_id on the displaced row *in the database*
                    # before target_show claims it. Both writes otherwise stay pending
                    # until the first episode flush, which emits them in primary-key
                    # order - and when target_show.id sorts first it hits
                    # shows_tmdb_id_key while the displaced row still holds the value.
                    await db.execute(
                        update(Show).where(Show.id == displaced_show.id).values(tmdb_id=None)
                    )
                    displaced_show.tmdb_id = None
            target_show.tmdb_id = body.tmdb_id
            await db.flush()
        else:
            tmdb_show_result = await db.execute(select(Show).where(Show.tmdb_id == body.tmdb_id))
            target_show = tmdb_show_result.scalar_one_or_none()

        if not target_show:
            # No show holds tmdb_tvdb_id (the cross-reference lookup above came
            # back empty), so it is safe to claim it on the new row.
            target_show = Show(
                tmdb_id=body.tmdb_id,
                tvdb_id=coerce_id(tmdb_tvdb_id),
                title=show_data.get("name") or show_data.get("original_name"),
                original_title=show_data.get("original_name"),
                overview=show_data.get("overview"),
                poster_path=tmdb.poster_url(show_data.get("poster_path")),
                backdrop_path=tmdb.poster_url(show_data.get("backdrop_path"), size="w1280"),
                tmdb_rating=show_data.get("vote_average"),
                status=show_data.get("status"),
                tagline=show_data.get("tagline"),
                first_air_date=show_data.get("first_air_date"),
                last_air_date=show_data.get("last_air_date"),
                tmdb_data={**show_data, "seasons": seasons_meta, "genres": [g["name"] if isinstance(g, dict) else g for g in show_data.get("genres", [])]},
            )
            db.add(target_show)
            await db.flush()
        else:
            # Refresh metadata in case it has stale data from a previous wrong match.
            target_show.title = show_data.get("name") or show_data.get("original_name") or target_show.title
            target_show.original_title = show_data.get("original_name") or target_show.original_title
            target_show.overview = show_data.get("overview") or target_show.overview
            target_show.poster_path = tmdb.poster_url(show_data.get("poster_path")) or target_show.poster_path
            target_show.backdrop_path = tmdb.poster_url(show_data.get("backdrop_path"), size="w1280") or target_show.backdrop_path
            target_show.tmdb_rating = show_data.get("vote_average") or target_show.tmdb_rating
            target_show.status = show_data.get("status") or target_show.status
            target_show.tagline = show_data.get("tagline") or target_show.tagline
            target_show.first_air_date = show_data.get("first_air_date") or target_show.first_air_date
            target_show.last_air_date = show_data.get("last_air_date") or target_show.last_air_date
            target_show.tmdb_data = {**show_data, "seasons": seasons_meta}
            await link_show_ids(db, target_show, tvdb_id=tmdb_tvdb_id)

        async def _fetch_season(season_number: int) -> dict | None:
            async with sem:
                try:
                    season_data = await tmdb.get_season(body.tmdb_id, season_number, api_key=tmdb_api_key)
                except Exception:
                    return None
                return {ep["episode_number"]: ep for ep in season_data.get("episodes", [])}

        def apply_tmdb_episode(media: Media, ep: dict | None) -> None:
            media.show_id = target_show.id
            if ep:
                media.tmdb_id = ep.get("id") or media.tmdb_id
                media.title = ep.get("name") or media.title
                media.overview = ep.get("overview")
                media.poster_path = tmdb.poster_url(ep.get("still_path"), size="w500")
                media.release_date = ep.get("air_date")
                media.tmdb_rating = ep.get("vote_average")
                media.runtime = ep.get("runtime") or media.runtime  # see #169
                media.tmdb_data = {"runtime": ep.get("runtime"), "cast": []}

        season_numbers = list(seasons_map.keys())
        # Fetches run concurrently (bounded by sem); mutations are applied sequentially
        # afterward since a single AsyncSession can't safely flush from concurrent tasks.
        ep_maps = await asyncio.gather(*[_fetch_season(sn) for sn in season_numbers])
        for season_number, ep_map in zip(season_numbers, ep_maps):
            season_episodes = seasons_map[season_number]
            if ep_map is None:
                skipped += len(season_episodes)
                continue
            for media in season_episodes:
                ep = ep_map.get(media.episode_number)
                await _apply_episode(media, lambda media=media, ep=ep: apply_tmdb_episode(media, ep))
                if ep:
                    matched += 1
                else:
                    skipped += 1

    # Stamp the matched state into all relevant SyncJob warnings so the panel
    # reflects the match immediately without a re-sync.
    title_lower = body.show_title.lower()
    jobs_res = await db.execute(
        select(SyncJob).where(
            SyncJob.user_id == current_user.id,
            SyncJob.status == SyncStatus.completed,
            SyncJob.warnings.isnot(None),
        )
    )
    for job in jobs_res.scalars().all():
        if not job.warnings:
            continue
        new_warnings = []
        changed = False
        for w in job.warnings:
            if (
                (w.get("series_name") or "").lower() == title_lower
                or (w.get("title") or "").lower() == title_lower
            ):
                new_warnings.append({
                    **w,
                    "matched": True,
                    "matched_tvdb_id": body.tvdb_id,
                    "matched_show_id": target_show.tmdb_id if target_show else None,
                    "matched_show_title": target_show.title if target_show else None,
                })
                changed = True
            else:
                new_warnings.append(w)
        if changed:
            job.warnings = new_warnings
            flag_modified(job, "warnings")
    await db.commit()

    return {
        "status": "ok",
        "matched": matched,
        "skipped": skipped,
        "tvdb_id": body.tvdb_id,
        "show_id": target_show.tmdb_id if target_show else None,
    }


@router.post("/heal-stub-show-titles")
async def heal_stub_show_titles(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Backfill tmdb_data['show_title'] for stub episodes that have it NULL,
    using series_name from SyncJob warnings matched via CollectionFile source_id."""
    warn_res = await db.execute(
        select(SyncJob.warnings).where(
            SyncJob.user_id == current_user.id,
            SyncJob.warnings.isnot(None),
        )
    )
    # Build source_id → series_name map from all warnings
    source_to_title: dict[str, str] = {}
    for (warnings,) in warn_res.all():
        for w in (warnings or []):
            sn = w.get("series_name")
            sid = w.get("source_id")
            if sn and sid:
                source_to_title[str(sid)] = sn

    if not source_to_title:
        return {"status": "ok", "healed": 0}

    # Find stub episodes with NULL tmdb_data via those source_ids
    ep_res = await db.execute(
        select(Media, CollectionFile.source_id)
        .join(Collection, Collection.media_id == Media.id)
        .join(CollectionFile, CollectionFile.collection_id == Collection.id)
        .where(
            Collection.user_id == current_user.id,
            Media.media_type == MediaType.episode,
            Media.tmdb_data.is_(None),
            CollectionFile.source_id.in_(list(source_to_title.keys())),
        )
        .distinct()
    )
    healed = 0
    for media, source_id in ep_res.all():
        title = source_to_title.get(source_id)
        if title:
            media.tmdb_data = {"show_title": title}
            healed += 1

    await db.commit()
    return {"status": "ok", "healed": healed}


@router.post("/heal-push-echo-duplicates")
async def heal_push_echo_duplicates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Removes duplicate WatchEvents created by a Jellyfin/Emby mark-watched
    push echoing straight back as a UserDataSaved webhook, before the fix in
    #247/#251 - every pushed item got a brand new WatchEvent stamped at push
    time, on top of whatever real watch record it already had (that's the
    only reason it was being pushed in the first place).

    Only removes a provisional watch event when both hold:
    - it's NOT the earliest recorded watch for that item (so the item was
      already known-watched before this one was created - never true for a
      genuinely new watch, which this must not touch), and
    - it landed inside a burst of BURST_MIN_SIZE+ other provisional
      completions within BURST_GAP of each other for this user - the push's
      bulk-timing signature, not an isolated real completion, and not a
      legitimate bulk "mark season watched" from the media server's own UI
      (which only ever produces genuinely-first watch events, excluded by
      the first condition above regardless of burst size).
    """
    BURST_GAP = timedelta(minutes=2)
    BURST_MIN_SIZE = 5

    rows = (await db.execute(
        select(WatchEvent.id, WatchEvent.media_id, WatchEvent.watched_at)
        .where(
            WatchEvent.user_id == current_user.id,
            WatchEvent.provisional == True,
            WatchEvent.completed == True,
            WatchEvent.watched_at.isnot(None),
        )
        .order_by(WatchEvent.watched_at)
    )).all()

    if not rows:
        return {"status": "ok", "healed": 0}

    media_ids = {r.media_id for r in rows}
    earliest_res = await db.execute(
        select(WatchEvent.media_id, func.min(WatchEvent.id))
        .where(WatchEvent.user_id == current_user.id, WatchEvent.media_id.in_(media_ids))
        .group_by(WatchEvent.media_id)
    )
    earliest_id_by_media: dict[int, int] = dict(earliest_res.all())

    to_delete: list[int] = []
    burst: list = []

    def _flush_burst():
        if len(burst) >= BURST_MIN_SIZE:
            for row in burst:
                if row.id != earliest_id_by_media.get(row.media_id):
                    to_delete.append(row.id)

    prev_at = None
    for row in rows:
        if prev_at is not None and (row.watched_at - prev_at) > BURST_GAP:
            _flush_burst()
            burst = []
        burst.append(row)
        prev_at = row.watched_at
    _flush_burst()

    if not to_delete:
        return {"status": "ok", "healed": 0}

    await db.execute(delete(WatchEvent).where(WatchEvent.id.in_(to_delete)))
    await db.commit()
    return {"status": "ok", "healed": len(to_delete)}


class HealDuplicateHistoryBody(BaseModel):
    window_minutes: int


@router.post("/heal-duplicate-history")
async def heal_duplicate_history(
    body: HealDuplicateHistoryBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually collapse this user's WatchEvent rows that are within
    window_minutes of another watch of the same movie/episode, regardless of
    which source produced either one - e.g. the same play recorded once by a
    Plex webhook and again hours later by a daily Trakt import (#390). Unlike
    the live prevention check (Settings > duplicate_watch_window_minutes),
    this is a one-off retroactive sweep the user runs on demand with their
    own window, for history that already has duplicates in it.

    Adjacent watches within the window chain into one cluster (A-B close and
    B-C close merges all three, even if A-C alone would exceed the window) -
    within a cluster, keeps the row most likely to be the real one: completed
    over provisional, then earliest.
    """
    if body.window_minutes < 1:
        raise HTTPException(status_code=422, detail="window_minutes must be at least 1")

    result = await db.execute(
        select(WatchEvent)
        .where(WatchEvent.user_id == current_user.id)
        .order_by(WatchEvent.media_id, WatchEvent.id)
    )
    events = result.scalars().all()

    def _effective_time(event: WatchEvent) -> datetime:
        return event.watched_at or event.created_at

    def _rank(event: WatchEvent) -> tuple[int, datetime]:
        if event.completed and not event.provisional:
            tier = 0
        elif event.completed:
            tier = 1
        else:
            tier = 2
        return (tier, _effective_time(event))

    by_media: dict[int, list[WatchEvent]] = {}
    for event in events:
        by_media.setdefault(event.media_id, []).append(event)

    tolerance = timedelta(minutes=body.window_minutes).total_seconds()
    to_delete: list[int] = []
    for media_events in by_media.values():
        media_events.sort(key=_effective_time)
        cluster: list[WatchEvent] = []
        for event in media_events:
            if cluster and (_effective_time(event) - _effective_time(cluster[-1])).total_seconds() > tolerance:
                if len(cluster) > 1:
                    keeper = min(cluster, key=_rank)
                    to_delete.extend(e.id for e in cluster if e is not keeper)
                cluster = []
            cluster.append(event)
        if len(cluster) > 1:
            keeper = min(cluster, key=_rank)
            to_delete.extend(e.id for e in cluster if e is not keeper)

    if not to_delete:
        return {"status": "ok", "healed": 0}

    await db.execute(delete(WatchEvent).where(WatchEvent.id.in_(to_delete)))
    await db.commit()
    return {"status": "ok", "healed": len(to_delete)}


@router.post("/heal-stuck-unwatched")
async def heal_stuck_unwatched(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Removes WatchEvent rows stuck as completed=False from the #253 sync bug -
    Jellyfin/Emby can report PlayCount > 0 while Played is still False (a
    partial watch that never crossed their own played threshold), and before
    the fix that row blocked every later sync from ever recording the real
    completion once it happened - "Next Up" in particular kept resurfacing an
    episode the user had actually already finished.

    Deletes the stale row rather than assuming it's now complete - that lets
    the next sync (now fixed) re-record the item's real current state,
    whether that's now finished or still genuinely in progress.

    Scoped to items actually collected from Jellyfin/Emby: completed=False
    can also come from a deliberate manual "log a partial watch" entry (see
    the manual-add endpoint in routers/history.py), which this must leave
    alone entirely.
    """
    ids_res = await db.execute(
        select(WatchEvent.id)
        .join(Media, Media.id == WatchEvent.media_id)
        .join(Collection, Collection.media_id == Media.id)
        .join(CollectionFile, CollectionFile.collection_id == Collection.id)
        .where(
            WatchEvent.user_id == current_user.id,
            WatchEvent.completed == False,
            Collection.user_id == current_user.id,
            CollectionFile.source.in_([CollectionSource.jellyfin, CollectionSource.emby]),
        )
        .distinct()
    )
    ids = [row[0] for row in ids_res.all()]
    if not ids:
        return {"status": "ok", "healed": 0}

    await db.execute(delete(WatchEvent).where(WatchEvent.id.in_(ids)))
    await db.commit()
    return {"status": "ok", "healed": len(ids)}


class UnmatchShowBody(BaseModel):
    show_title: str


@router.post("/unmatch-show")
async def unmatch_show(
    body: UnmatchShowBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Unlink stub episodes from their manually-matched Show row so they can be re-matched."""
    from sqlalchemy import func as sa_func

    ep_result = await db.execute(
        select(Media)
        .join(Collection, Collection.media_id == Media.id)
        .where(
            Collection.user_id == current_user.id,
            Media.media_type == MediaType.episode,
            Media.show_id.isnot(None),
            sa_func.lower(Media.tmdb_data["show_title"].astext) == body.show_title.lower(),
        )
        .distinct()
    )
    episodes = ep_result.scalars().all()
    if not episodes:
        # Fallback: find via source_ids from SyncJob warnings (tmdb_data may be NULL)
        title_lower_u = body.show_title.lower()
        src_warn_res = await db.execute(
            select(SyncJob.warnings).where(
                SyncJob.user_id == current_user.id,
                SyncJob.warnings.isnot(None),
            )
        )
        fallback_source_ids: list[str] = []
        for (warnings,) in src_warn_res.all():
            for w in (warnings or []):
                if (w.get("series_name") or w.get("title") or "").lower() == title_lower_u and w.get("source_id"):
                    fallback_source_ids.append(str(w["source_id"]))
        if fallback_source_ids:
            fb_res = await db.execute(
                select(Media)
                .join(Collection, Collection.media_id == Media.id)
                .join(CollectionFile, CollectionFile.collection_id == Collection.id)
                .where(
                    Collection.user_id == current_user.id,
                    Media.media_type == MediaType.episode,
                    CollectionFile.source_id.in_(fallback_source_ids),
                )
                .distinct()
            )
            episodes = fb_res.scalars().all()
    if not episodes:
        raise HTTPException(status_code=404, detail="No matched stub episodes found for this show title")

    show_ids_to_check: set[int] = set()
    for ep in episodes:
        if ep.show_id:
            show_ids_to_check.add(ep.show_id)
        ep.show_id = None
        ep.tmdb_id = None
        ep.overview = None
        ep.poster_path = None
        ep.release_date = None
        ep.tmdb_rating = None

    await db.commit()

    # Remove Show rows that are now orphaned (no remaining linked media).
    # TMDB-only shows (no tvdb_id) are deleted so a future match creates a fresh row
    # instead of reusing a show row that may have stale/wrong metadata.
    # TVDB-tagged shows are kept — they carry a canonical TVDB ID used elsewhere.
    for show_id in show_ids_to_check:
        remaining = await db.execute(
            select(func.count()).select_from(Media).where(Media.show_id == show_id)
        )
        if remaining.scalar_one() == 0:
            show_q = await db.execute(
                select(Show).where(Show.id == show_id, Show.tvdb_id.is_(None))
            )
            orphaned = show_q.scalar_one_or_none()
            if orphaned:
                await db.delete(orphaned)

    # Clear matched stamps from SyncJob warnings
    title_lower = body.show_title.lower()
    jobs_res = await db.execute(
        select(SyncJob).where(
            SyncJob.user_id == current_user.id,
            SyncJob.status == SyncStatus.completed,
            SyncJob.warnings.isnot(None),
        )
    )
    for job in jobs_res.scalars().all():
        if not job.warnings:
            continue
        new_warnings = []
        changed = False
        for w in job.warnings:
            if w.get("matched") and (
                (w.get("series_name") or "").lower() == title_lower
                or (w.get("title") or "").lower() == title_lower
            ):
                cleared = {k: v for k, v in w.items() if not k.startswith("matched")}
                new_warnings.append(cleared)
                changed = True
            else:
                new_warnings.append(w)
        if changed:
            await db.execute(
                update(SyncJob).where(SyncJob.id == job.id).values(warnings=new_warnings)
            )
    await db.commit()

    return {"status": "ok", "unmatched": len(episodes)}


# ── Unmatched movie matching ──────────────────────────────────────────────────

class MatchUnmatchedMovieBody(BaseModel):
    movie_title: str
    tmdb_id: int


@router.post("/match-unmatched-movie")
async def match_unmatched_movie(
    body: MatchUnmatchedMovieBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Link unmatched local movies (no tmdb_id) to a TMDB movie."""
    from sqlalchemy import func as sa_func

    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = settings_result.scalar_one_or_none()
    tmdb_api_key = await settings_store.get_effective_tmdb_key(db, settings)
    if not tmdb_api_key:
        raise HTTPException(status_code=400, detail="TMDB API key required")

    movie_result = await db.execute(
        select(Media)
        .join(Collection, Collection.media_id == Media.id)
        .where(
            Collection.user_id == current_user.id,
            Media.tmdb_id.is_(None),
            Media.media_type == MediaType.movie,
            sa_func.lower(Media.title) == body.movie_title.lower(),
        )
        .distinct()
    )
    movies = movie_result.scalars().all()
    if not movies:
        raise HTTPException(status_code=404, detail="No unmatched movies found for this title")

    # Fetch TMDB metadata once to get the canonical title
    try:
        movie_data = await tmdb.get_movie(body.tmdb_id, api_key=tmdb_api_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch movie from TMDB: {e}")

    matched_title = movie_data.get("title") or body.movie_title
    for media in movies:
        # Multiple unmatched stubs can share this title; if an earlier one in
        # this loop already claimed body.tmdb_id, this one stays unmatched
        # rather than colliding with it.
        result = await apply_media_change_safely(
            db, media, lambda media=media: setattr(media, "tmdb_id", body.tmdb_id)
        )
        if result.id == media.id:
            await enrich_media(media, api_key=tmdb_api_key)

    # Stamp the matched state into all relevant SyncJob warnings
    title_lower = body.movie_title.lower()
    jobs_res = await db.execute(
        select(SyncJob).where(
            SyncJob.user_id == current_user.id,
            SyncJob.status == SyncStatus.completed,
            SyncJob.warnings.isnot(None),
        )
    )
    for job in jobs_res.scalars().all():
        if not job.warnings:
            continue
        new_warnings = []
        changed = False
        for w in job.warnings:
            if (
                w.get("media_type") == "movie"
                and not w.get("matched")
                and (w.get("title") or "").lower() == title_lower
            ):
                new_warnings.append({
                    **w,
                    "matched": True,
                    "matched_tmdb_id": body.tmdb_id,
                    "matched_movie_title": matched_title,
                })
                changed = True
            else:
                new_warnings.append(w)
        if changed:
            job.warnings = new_warnings
            flag_modified(job, "warnings")

    await db.commit()
    return {"status": "ok", "matched": len(movies), "tmdb_id": body.tmdb_id}


class UnmatchMovieBody(BaseModel):
    movie_title: str


@router.post("/unmatch-movie")
async def unmatch_movie(
    body: UnmatchMovieBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear TMDB link from locally-matched movies so they can be re-matched."""
    from sqlalchemy import func as sa_func

    movie_result = await db.execute(
        select(Media)
        .join(Collection, Collection.media_id == Media.id)
        .where(
            Collection.user_id == current_user.id,
            Media.media_type == MediaType.movie,
            Media.tmdb_id.isnot(None),
            sa_func.lower(Media.title) == body.movie_title.lower(),
        )
        .distinct()
    )
    movies = movie_result.scalars().all()
    if not movies:
        raise HTTPException(status_code=404, detail="No matched movies found for this title")

    for media in movies:
        media.tmdb_id = None
        media.overview = None
        media.poster_path = None
        media.backdrop_path = None
        media.release_date = None
        media.tmdb_rating = None
        media.tmdb_data = None

    # Clear matched stamps from SyncJob warnings
    title_lower = body.movie_title.lower()
    jobs_res = await db.execute(
        select(SyncJob).where(
            SyncJob.user_id == current_user.id,
            SyncJob.status == SyncStatus.completed,
            SyncJob.warnings.isnot(None),
        )
    )
    for job in jobs_res.scalars().all():
        if not job.warnings:
            continue
        new_warnings = []
        changed = False
        for w in job.warnings:
            if (
                w.get("matched")
                and w.get("media_type") == "movie"
                and (w.get("title") or "").lower() == title_lower
            ):
                cleared = {k: v for k, v in w.items() if not k.startswith("matched")}
                new_warnings.append(cleared)
                changed = True
            else:
                new_warnings.append(w)
        if changed:
            job.warnings = new_warnings
            flag_modified(job, "warnings")

    await db.commit()
    return {"status": "ok", "unmatched": len(movies)}


@router.get("/matched-shows")
async def list_matched_shows(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    """Return all matched shows (TMDB or TVDB) for the current user.

    Used by the settings panel to overlay matched state onto SyncJob warnings that
    were stamped before the auto-stamping logic existed, without requiring a resync.

    Two sources are combined:
    1. Media rows with show_id → matched Show, keyed by tmdb_data["show_title"].
    2. SyncJob warnings with matched:true (covers shows where show_title is absent from
       tmdb_data, e.g. episodes that weren't created as stubs or had tmdb_data overwritten).
    """

    # Fetch all shows in the system to build a db_id -> Show mapping
    # This allows us to map any database show.id to its tmdb_id dynamically,
    # correcting any legacy/already-stamped warning entries where matched_show_id was show.id.
    shows_res = await db.execute(select(Show.id, Show.tmdb_id, Show.tvdb_id, Show.title))
    show_id_map = {
        row.id: {
            "tmdb_id": row.tmdb_id,
            "tvdb_id": row.tvdb_id,
            "title": row.title,
        }
        for row in shows_res.all()
    }

    seen: dict[str, dict] = {}

    # Source 1: episodes linked to matched shows (TMDB or TVDB), keyed by show_title in tmdb_data
    result = await db.execute(
        select(
            Media.tmdb_data["show_title"].astext.label("show_title"),
            Show.tmdb_id.label("show_id"),
            Show.tvdb_id,
            Show.title.label("show_title_matched"),
        )
        .join(Collection, Collection.media_id == Media.id)
        .join(Show, Show.id == Media.show_id)
        .where(
            Collection.user_id == current_user.id,
            Media.media_type == MediaType.episode,
            Media.show_id.isnot(None),
            (Show.tvdb_id.isnot(None) | Show.tmdb_id.isnot(None)),
            Media.tmdb_data["show_title"].astext.isnot(None),
        )
        .distinct()
    )
    for row in result.all():
        key = (row.show_title or "").lower()
        if key and key not in seen:
            seen[key] = {
                "show_title": row.show_title,
                "show_id": row.show_id,
                "tvdb_id": row.tvdb_id,
                "show_title_matched": row.show_title_matched,
            }

    # Source 2: SyncJob warnings stamped with matched:true (fallback for missing show_title)
    jobs_res = await db.execute(
        select(SyncJob.warnings).where(
            SyncJob.user_id == current_user.id,
            SyncJob.status == SyncStatus.completed,
            SyncJob.warnings.isnot(None),
        ).order_by(SyncJob.created_at.desc()).limit(10)
    )
    for (warnings,) in jobs_res.all():
        if not warnings:
            continue
        for w in warnings:
            if not w.get("matched"):
                continue
            title = w.get("series_name") or w.get("title")
            if not title:
                continue
            key = title.lower()
            if key not in seen:
                legacy_show_id = w.get("matched_show_id")
                matched_tvdb_id = w.get("matched_tvdb_id")
                matched_show_id = legacy_show_id

                if legacy_show_id in show_id_map:
                    show_info = show_id_map[legacy_show_id]
                    # Only map if titles match (case-insensitive) or tmdb_id matches, preventing collisions
                    db_title = show_info["title"].lower()
                    matched_title = (w.get("matched_show_title") or "").lower()
                    if db_title == key or db_title == matched_title or show_info["tmdb_id"] == legacy_show_id:
                        matched_show_id = show_info["tmdb_id"]
                        if show_info["tvdb_id"]:
                            matched_tvdb_id = show_info["tvdb_id"]

                seen[key] = {
                    "show_title": title,
                    "show_id": matched_show_id,
                    "tvdb_id": matched_tvdb_id,
                    "show_title_matched": w.get("matched_show_title"),
                }

    return list(seen.values())
