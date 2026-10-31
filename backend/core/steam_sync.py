"""Steam-specific import review and incremental sync inside account pull jobs."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from core import catalogue, game_activity, game_tracking
from core.game_access import allowed
from core.game_types import capabilities
from core.steam import SteamError, SteamSource
from core.sync_jobs import SyncCancelled, mark_job_running_unless_cancelled, raise_if_cancelled
from models import SteamConnection, SyncJob, SyncStatus, User
from models.catalogue import CatalogueEntity, CatalogueIdentity
from models.games import GameEntry


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def running_job(db, job_id):
    job = await db.scalar(select(SyncJob).where(SyncJob.id == job_id).with_for_update().execution_options(populate_existing=True))
    if job is None or job.status != SyncStatus.running:
        raise SyncCancelled()
    return job


async def preparation(factory, user_id, connection_id, job_id):
    """Bound fan-out; persist batches so a refreshed review can resume loading."""
    async with factory() as db:
        if not await mark_job_running_unless_cancelled(db, job_id, current_step='Loading Steam library'):
            return
        state = await db.get(SteamConnection, connection_id)
        if not state or state.user_id != user_id:
            return
        generation, native = state.generation, state.steam_id
        library = [dict(row) for row in state.library]
    source = SteamSource(factory)
    try:
        pending = [row for row in library if not row.get('achievement_checked_at')]
        done = len(library) - len(pending)
        for offset in range(0, len(pending), 4):
            batch = pending[offset:offset+4]
            values = await asyncio.gather(*(source.achievements(native, row['app_id']) for row in batch))
            for row, value in zip(batch, values):
                row.update(value, achievement_checked_at=now().isoformat())
            async with factory() as db:
                await raise_if_cancelled(db, job_id)
                state = await db.get(SteamConnection, connection_id, with_for_update=True)
                if not state or state.user_id != user_id or state.generation != generation:
                    return
                # Avoid rewriting a large library JSON for every four games.
                if offset % 64 == 0 or offset + 4 >= len(pending):
                    state.library = library
                job = await running_job(db, job_id)
                job.processed_items = min(done + offset + 4, len(library))
                await db.commit()
        async with factory() as db:
            await raise_if_cancelled(db, job_id)
            state = await db.get(SteamConnection, connection_id, with_for_update=True)
            if not state or state.generation != generation:
                return
            state.fetched_at = now()
            state.library = library
            job = await running_job(db, job_id)
            job.status, job.current_step = SyncStatus.completed, 'Ready to select games'
            unknown = sum(row['achievement_count'] is None for row in library)
            job.warnings = ([f'Achievements are unavailable for {unknown} games. Their counts remain unknown.'] if unknown else [])
            await db.commit()
    except SyncCancelled:
        return
    except Exception as error:
        async with factory() as db:
            await db.execute(update(SyncJob).where(SyncJob.id == job_id, SyncJob.status.in_((SyncStatus.pending, SyncStatus.running)))
                .values(status=SyncStatus.failed, error_message=str(error)[:1000] if isinstance(error, SteamError) else 'Could not load Steam library. Retry the import.'))
            await db.commit()


async def attach_dates(db, library):
    # One compact local projection, never an app-details fan-out for the picker.
    rows = await db.execute(select(CatalogueIdentity.external_id, CatalogueEntity.attributes['release_date'].astext)
        .join(CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id)
        .where(CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id.in_([g['app_id'] for g in library])))
    dates = dict(rows.all())
    return [{**row, 'release_date': dates.get(row['app_id'])} for row in library]


async def apply_games(db, state, library, selected, *, initial=False):
    """Caller holds owner mutex. Apply authoritative app IDs without title matching."""
    by_app = {row['app_id']: row for row in library}
    checkpoints = dict(state.checkpoints)
    changed = 0
    await catalogue.write_lock(db)
    for app in selected:
        data = by_app.get(app)
        if data is None:
            continue  # A hidden/removed game never clears previously imported facts.
        prior = checkpoints.get(app, {})
        identity = await catalogue.resolve_identity(db, 'steam.app', app)
        if not identity and not initial:
            continue
        work = await db.get(CatalogueEntity, identity.entity_id) if identity else await catalogue.upsert_entity(db, {
            'kind': 'game', 'name': data['name'], 'image_url': data['poster'],
            'attributes': {'game_class': 'game'},
            'identities': [{'namespace': 'steam.app', 'external_id': app}],
        }, 'steam')
        if not capabilities(work.attributes).independent:
            continue  # Keep established DLC/edition semantics intact.
        entry = await db.scalar(select(GameEntry).where(GameEntry.user_id == state.user_id, GameEntry.work_id == work.id))
        if entry is None and not initial:
            continue  # A manual list removal is never undone by sync.
        before = await game_activity.snapshot(db, work, entry, achievements=state.sync_achievements)
        created = entry is None
        if created:
            entry = GameEntry(user_id=state.user_id, work_id=work.id,
                status='playing' if data['playtime_minutes'] or data.get('achievement_count') else 'planning', revision=0)
            db.add(entry)
        previous = (entry.total_seconds, entry.achievement_mode, entry.achievement_count, entry.achievement_percentage)
        if state.sync_playtime:
            seconds = data['playtime_minutes'] * 60
            if 'playtime_minutes' not in prior:
                entry.total_seconds = max(entry.total_seconds or 0, seconds)
            else:
                delta = max(0, data['playtime_minutes'] - prior['playtime_minutes']) * 60
                if delta:
                    entry.total_seconds = (entry.total_seconds or 0) + delta
        if state.sync_achievements and data.get('achievement_count') is not None:
            entry.achievement_mode = 'count'
            entry.achievement_count = data['achievement_count']
            total = data.get('achievement_total')
            entry.achievement_percentage = (100 * entry.achievement_count / total) if total else None
        current = (entry.total_seconds, entry.achievement_mode, entry.achievement_count, entry.achievement_percentage)
        if created or previous != current:
            entry.revision = (entry.revision or 0) + 1
            await db.flush()
            await game_activity.record(db, entry, before, await game_activity.snapshot(db, work, entry, achievements=state.sync_achievements))
            changed += 1
        checkpoints[app] = {**prior, 'work_id': work.id,
            **({'observed_playtime_minutes': data['playtime_minutes']} if data.get('achievement_checked_at') or not state.sync_achievements else {}),
            **({'playtime_minutes': data['playtime_minutes']} if state.sync_playtime else {}),
            **({'achievement_checked_at': data['achievement_checked_at']} if data.get('achievement_checked_at') else {})}
    state.checkpoints = checkpoints
    return changed


def achievement_due(row, prior, stamp):
    checked = prior.get('achievement_checked_at')
    return (row['playtime_minutes'] != prior.get('observed_playtime_minutes')
            or not checked or datetime.fromisoformat(checked) <= stamp - timedelta(days=1))


async def run_steam_sync(user_id, job_id, _a=0, _b=0, connection_id=None):
    from db import AsyncSessionLocal
    await sync(AsyncSessionLocal, user_id, job_id, connection_id)


async def sync(factory, user_id, job_id, connection_id):
    async with factory() as db:
        if not await mark_job_running_unless_cancelled(db, job_id, current_step='Syncing Steam'):
            return
        state = await db.get(SteamConnection, connection_id)
        user = await db.get(User, user_id)
        if not state or state.user_id != user_id or not state.imported or not allowed(user):
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed, error_message='Steam connection is not ready or Games access is unavailable.'))
            await db.commit()
            return
        native, generation = state.steam_id, state.generation
        selected, checkpoints = list(state.selection), dict(state.checkpoints)
        active_works = set(await db.scalars(select(GameEntry.work_id).where(GameEntry.user_id == user_id,
            GameEntry.work_id.in_([value['work_id'] for value in checkpoints.values() if value.get('work_id')]))))
        selected = [app for app in selected if checkpoints.get(app, {}).get('work_id') in active_works]
        achievements_enabled = state.sync_achievements
        playtime_enabled = state.sync_playtime
    try:
        if not achievements_enabled and not playtime_enabled:
            async with factory() as db:
                await db.execute(update(SyncJob).where(SyncJob.id == job_id, SyncJob.status == SyncStatus.running)
                    .values(status=SyncStatus.completed, current_step='Steam sync disabled'))
                await db.commit()
            return
        source = SteamSource(factory)
        library = await source.library(native)
        stamp = now()
        selected_set = set(selected)
        pending = [row for row in library if row['app_id'] in selected_set
                   and achievements_enabled and achievement_due(row, checkpoints.get(row['app_id'], {}), stamp)]
        # Refresh changed games first; rotate quiet/unknown games without scanning every achievement on every tick.
        pending.sort(key=lambda row: (row['playtime_minutes'] == checkpoints.get(row['app_id'], {}).get('observed_playtime_minutes'),
                                      checkpoints.get(row['app_id'], {}).get('achievement_checked_at', '')))
        for offset in range(0, min(len(pending), 100), 4):
            batch = pending[offset:min(offset+4, 100)]
            values = await asyncio.gather(*(source.achievements(native, row['app_id']) for row in batch))
            for row, value in zip(batch, values):
                row.update(value, achievement_checked_at=stamp.isoformat())
            async with factory() as db:
                await raise_if_cancelled(db, job_id)
        async with factory() as db:
            await game_tracking.lock_owner(db, user_id)
            await raise_if_cancelled(db, job_id)
            state = await db.get(SteamConnection, connection_id, with_for_update=True)
            if not state or state.generation != generation:
                raise SteamError('Steam connection changed during sync. Retry the sync.')
            changed = await apply_games(db, state, library, selected)
            job = await running_job(db, job_id)
            job.status, job.current_step = SyncStatus.completed, 'Steam up to date'
            job.total_items = job.processed_items = len(selected)
            job.stats = {'updated': changed, 'achievement_requests': min(len(pending), 100), 'library_requests': 1}
            unknown = sum(row.get('achievement_count') is None for row in pending[:100])
            job.warnings = ([f'Achievements unavailable for {unknown} games; existing progress was preserved.'] if unknown else [])
            await db.commit()
    except SyncCancelled:
        return
    except Exception as error:
        async with factory() as db:
            await db.execute(update(SyncJob).where(SyncJob.id == job_id, SyncJob.status.in_((SyncStatus.pending, SyncStatus.running)))
                .values(status=SyncStatus.failed, error_message=str(error)[:1000] if isinstance(error, SteamError) else 'Steam sync failed. Retry later.'))
            await db.commit()
