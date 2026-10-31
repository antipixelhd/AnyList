"""Steam connections share account ownership and sync jobs, with a dedicated review."""
from typing import Annotated
import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import game_tracking, steam_sync
from core.account_sync import require_idle_account
from core.game_access import approved_games_viewer
from core.import_admission import require_import_slot
from core.steam import SteamError, SteamSource
from db import AsyncSessionLocal, get_db
from dependencies import get_current_user
from models import CollectionSource, MediaServerConnection, SteamConnection, SyncJob, SyncStatus, User

router = APIRouter(prefix='/connections/steam', tags=['steam'], dependencies=[Depends(approved_games_viewer)])
DB = Annotated[AsyncSession, Depends(get_db)]
Viewer = Annotated[User, Depends(get_current_user)]


class Connect(BaseModel):
    profile: str = Field(min_length=1, max_length=300)
    sync_achievements: bool = True
    sync_playtime: bool = True


class Options(BaseModel):
    sync_achievements: bool
    sync_playtime: bool


class Draft(BaseModel):
    generation: int = Field(ge=1)
    minimum_playtime: int = Field(default=30, ge=0, le=10000000)
    minimum_achievements: int = Field(default=1, ge=0, le=50000)
    playtime_enabled: bool = True
    achievements_enabled: bool = True
    overrides: dict[str, bool] = Field(default_factory=dict, max_length=100000)
    sort: str = Field(default='playtime', pattern='^(playtime|alphabetical|completion)$')


class MetadataBatch(BaseModel):
    apps: list[Annotated[str, Field(pattern=r'^[1-9][0-9]{0,9}$')]] = Field(min_length=1, max_length=8)


@router.post('/metadata')
async def metadata(body: MetadataBatch, viewer: Viewer) -> dict:
    from core.catalogue_providers import ProviderError
    source = SteamSource(AsyncSessionLocal)
    async def one(app):
        try:
            return app, await source.metadata(app)
        except (ProviderError, SteamError):
            return app, {'release_date': None}
    return dict(await asyncio.gather(*(one(app) for app in dict.fromkeys(body.apps))))


def selected_games(library, draft):
    return [row['app_id'] for row in library if draft.overrides.get(row['app_id'],
        (draft.playtime_enabled and row['playtime_minutes'] >= draft.minimum_playtime)
        or (draft.achievements_enabled and row.get('achievement_count') is not None
            and row['achievement_count'] >= draft.minimum_achievements))]


async def owned(db, user_id, connection_id, *, lock=False):
    statement = select(SteamConnection).where(SteamConnection.connection_id == connection_id, SteamConnection.user_id == user_id)
    state = await db.scalar(statement.with_for_update() if lock else statement)
    if not state:
        raise HTTPException(404, 'Steam connection not found')
    return state


async def latest_job(db, user_id, connection_id):
    return await db.scalar(select(SyncJob).where(SyncJob.user_id == user_id, SyncJob.connection_id == connection_id,
        SyncJob.job_type == 'steam_prepare').order_by(SyncJob.id.desc()).limit(1))


def summary(state):
    return {'id': state.connection_id, 'steam_id': state.steam_id, 'imported': state.imported,
            'sync_achievements': state.sync_achievements, 'sync_playtime': state.sync_playtime,
            'selected_count': len(state.selection), 'generation': state.generation}


async def queue_preparation(db, state, background):
    job = SyncJob(user_id=state.user_id, source=CollectionSource.steam, connection_id=state.connection_id,
                  job_type='steam_prepare', status=SyncStatus.pending, total_items=len(state.library), current_step='Loading Steam library')
    db.add(job)
    await db.commit()
    background.add_task(steam_sync.preparation, AsyncSessionLocal, state.user_id, state.connection_id, job.id)
    return job


@router.get('')
async def connections(db: DB, viewer: Viewer) -> list[dict]:
    return [summary(state) for state in await db.scalars(select(SteamConnection).where(SteamConnection.user_id == viewer.id))]


@router.post('', status_code=201)
async def connect(body: Connect, background: BackgroundTasks, db: DB, viewer: Viewer) -> dict:
    source = SteamSource(AsyncSessionLocal)
    try:
        profile = await source.profile(body.profile)
        library = await source.library(profile['steam_id'])
    except SteamError as error:
        raise HTTPException(422, str(error)) from None
    await require_import_slot(db, viewer.id)
    await require_idle_account(db, viewer.id)
    existing = await db.scalar(select(SteamConnection).where(SteamConnection.user_id == viewer.id,
                                                           SteamConnection.steam_id == profile['steam_id']))
    if existing:
        return summary(existing)
    conn = MediaServerConnection(user_id=viewer.id, type='steam', name=profile['name'],
        url=f"https://steamcommunity.com/profiles/{profile['steam_id']}", token='',
        provider_account_id=profile['steam_id'], libraries_confirmed=False,
        sync_collection=False, sync_watched=False, sync_ratings=False, sync_playback=False)
    db.add(conn)
    await db.flush()
    state = SteamConnection(connection_id=conn.id, user_id=viewer.id, steam_id=profile['steam_id'],
        library=await steam_sync.attach_dates(db, library), sync_achievements=body.sync_achievements, sync_playtime=body.sync_playtime)
    db.add(state)
    await db.flush()
    await queue_preparation(db, state, background)
    return summary(state)


@router.get('/{connection_id}')
async def review(connection_id: int, db: DB, viewer: Viewer) -> dict:
    state = await owned(db, viewer.id, connection_id)
    job = await latest_job(db, viewer.id, connection_id)
    return {**summary(state), 'library': state.library if job and job.status == SyncStatus.completed else [], 'draft': state.draft,
        'job': {'status': job.status.value, 'current': job.processed_items, 'total': job.total_items,
                'error': job.error_message, 'warnings': job.warnings or []} if job else None}


@router.patch('/{connection_id}')
async def options(connection_id: int, body: Options, db: DB, viewer: Viewer) -> dict:
    await require_idle_account(db, viewer.id)
    state = await owned(db, viewer.id, connection_id, lock=True)
    if body.sync_playtime and not state.sync_playtime:
        # Start a new baseline on re-enable; never add the disabled interval twice.
        state.checkpoints = {app: {k: v for k, v in value.items() if k != 'playtime_minutes'} for app, value in state.checkpoints.items()}
    state.sync_achievements, state.sync_playtime = body.sync_achievements, body.sync_playtime
    state.generation += 1
    await db.commit()
    return summary(state)


@router.post('/{connection_id}/retry')
async def retry(connection_id: int, background: BackgroundTasks, db: DB, viewer: Viewer) -> dict:
    state = await owned(db, viewer.id, connection_id)
    if state.imported:
        raise HTTPException(409, 'This library has already been imported.')
    # Network I/O stays outside owner/state locks.
    native, generation = state.steam_id, state.generation
    try:
        library = await SteamSource(AsyncSessionLocal).library(native)
    except SteamError as error:
        raise HTTPException(422, str(error)) from None
    await require_import_slot(db, viewer.id, connection_id=connection_id)
    state = await owned(db, viewer.id, connection_id, lock=True)
    if state.generation != generation:
        raise HTTPException(409, 'Steam connection changed. Reload to continue.')
    job = await latest_job(db, viewer.id, connection_id)
    if job and job.status in (SyncStatus.pending, SyncStatus.running):
        raise HTTPException(409, 'Steam library is already loading.')
    state.generation += 1
    previous = {row['app_id']: row for row in state.library}
    for row in library:
        old = previous.get(row['app_id'], {})
        if old.get('playtime_minutes') == row['playtime_minutes'] and old.get('achievement_checked_at'):
            row.update({key: old[key] for key in ('achievement_count', 'achievement_total', 'achievement_checked_at')})
    state.library = await steam_sync.attach_dates(db, library)
    await queue_preparation(db, state, background)
    return summary(state)


async def validate_draft(db, viewer, connection_id, body):
    await game_tracking.lock_owner(db, viewer.id)
    state = await owned(db, viewer.id, connection_id, lock=True)
    if state.imported or state.generation != body.generation:
        raise HTTPException(409, 'Steam import changed. Reload to continue.')
    job = await latest_job(db, viewer.id, connection_id)
    if not job or job.status != SyncStatus.completed:
        raise HTTPException(409, 'Finish loading your Steam library before importing.')
    apps = {row['app_id'] for row in state.library}
    if body.overrides.keys() - apps:
        raise HTTPException(422, 'Selection contains games outside this Steam library.')
    return state


@router.put('/{connection_id}/draft')
async def save_draft(connection_id: int, body: Draft, db: DB, viewer: Viewer) -> dict:
    state = await validate_draft(db, viewer, connection_id, body)
    state.draft = body.model_dump(exclude={'generation'})
    await db.commit()
    return {'selected_count': len(selected_games(state.library, body))}


@router.post('/{connection_id}/import')
async def commit_import(connection_id: int, body: Draft, db: DB, viewer: Viewer) -> dict:
    await require_idle_account(db, viewer.id)
    state = await validate_draft(db, viewer, connection_id, body)
    selection = selected_games(state.library, body)
    stamp = steam_sync.now().isoformat()
    library = [{**row, 'achievement_checked_at': stamp} for row in state.library]
    await steam_sync.apply_games(db, state, library, selection, initial=True)
    accepted = [app for app in selection if app in state.checkpoints]
    state.selection, state.imported, state.draft = accepted, True, {}
    # The large review payload is temporary. Recurring sync needs only IDs/checkpoints.
    state.library = []
    conn = await db.get(MediaServerConnection, connection_id)
    conn.libraries_confirmed = True
    await db.commit()
    return {'imported': len(accepted), 'selected': len(accepted), **({'skipped': len(selection) - len(accepted)} if len(accepted) != len(selection) else {})}
