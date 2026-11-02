"""Steam review and incremental account sync with durable source checkpoints."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from core import catalogue, game_activity, game_tracking, steam_cache
from core.game_access import allowed
from core.game_types import capabilities
from core.steam import SteamError, SteamSource
from core.sync_jobs import SyncCancelled, mark_job_running_unless_cancelled, raise_if_cancelled
from models import SteamConnection, SyncJob, SyncStatus, User
from models.catalogue import CatalogueEntity
from models.games import GameEntry


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def running_job(db, job_id):
    job = await db.scalar(select(SyncJob).where(SyncJob.id == job_id).with_for_update().execution_options(populate_existing=True))
    if job is None or job.status != SyncStatus.running:
        raise SyncCancelled()
    return job


async def preparation(factory, user_id, connection_id, job_id):
    """Make the picker usable immediately; achievements belong to step two."""
    async with factory() as db:
        if not await mark_job_running_unless_cancelled(db, job_id, current_step='Preparing Steam library'):
            return
        state = await db.get(SteamConnection, connection_id, with_for_update=True)
        if not state or state.user_id != user_id:
            return
        state.library = await steam_cache.read_progress(db, user_id, state.steam_id, state.library)
        state.fetched_at = now()
        library = list(state.library)
        job = await running_job(db, job_id)
        job.status, job.current_step = SyncStatus.completed, 'Ready to select games'
        job.processed_items = job.total_items = len(library)
        await db.commit()
    # Publish only public evidence, including games the viewer does not import.
    await steam_cache.save_public(factory, library)


async def attach_dates(db, library):
    public = await steam_cache.public_metadata(db, [game['app_id'] for game in library])
    return [{**game, 'release_date': public.get(game['app_id'], {}).get('release_date')} for game in library]


async def apply_games(db, state, library, selected, *, initial=False, add_apps=None):
    """Caller holds the owner mutex. Source totals never replace manual deltas."""
    by_app = {row['app_id']: row for row in library}
    checkpoints, additions = dict(state.checkpoints), set(add_apps or ())
    changed = 0
    today = datetime.now(ZoneInfo(state.timezone or 'UTC')).date()
    await catalogue.write_lock(db)
    for app in selected:
        data = by_app.get(app)
        if data is None:
            continue
        prior = dict(checkpoints.get(app, {}))
        identity = await catalogue.resolve_identity(db, 'steam.app', app)
        if not identity and not initial and app not in additions:
            continue
        work = await db.get(CatalogueEntity, identity.entity_id) if identity else await catalogue.upsert_entity(db, {
            'kind': 'game', 'name': data['name'], 'image_url': data['poster'], 'attributes': {'game_class': 'game',
                **({'release_date': data['release_date'], 'release_date_precision': 'day' if len(data['release_date']) == 10 else 'month' if len(data['release_date']) == 7 else 'year'} if data.get('release_date') else {})},
            'identities': [{'namespace': 'steam.app', 'external_id': app}],
        }, 'steam')
        if not capabilities(work.attributes).independent:
            continue
        if identity is None and data.get('achievement_total') is not None:
            steam_cache.merge_total(work, app, data['achievement_total'], now())
        entry = await db.scalar(select(GameEntry).where(GameEntry.user_id == state.user_id, GameEntry.work_id == work.id))
        if entry is None and not initial and app not in additions:
            continue  # Manual deletion is never undone by an ordinary refresh.
        before = await game_activity.snapshot(db, work, entry, achievements=state.sync_achievements)
        created = entry is None
        if created:
            automatic = app in additions
            entry = GameEntry(user_id=state.user_id, work_id=work.id, revision=0,
                status='planning' if automatic else 'playing' if data['playtime_minutes'] or data.get('achievement_count') else 'planning')
            db.add(entry)
            if automatic:
                prior['start_pending'] = True
        previous = (entry.status, entry.start_date, entry.total_seconds, entry.achievement_mode,
                    entry.achievement_count, entry.achievement_percentage)
        if state.auto_add_games and prior.get('start_pending'):
            if entry.status != 'planning' or entry.start_date is not None:
                prior['start_pending'] = False
            elif data['playtime_minutes'] > 30:
                entry.status, entry.start_date = 'playing', today
                prior['start_pending'] = False
        if state.sync_playtime:
            seconds = data['playtime_minutes'] * 60
            if 'playtime_minutes' not in prior:
                entry.total_seconds = max(entry.total_seconds or 0, seconds)
            else:
                delta = max(0, data['playtime_minutes'] - prior['playtime_minutes']) * 60
                if delta:
                    entry.total_seconds = (entry.total_seconds or 0) + delta
        if state.sync_achievements and data.get('achievement_count') is not None:
            entry.achievement_mode, entry.achievement_count = 'count', data['achievement_count']
            total = data.get('achievement_total')
            entry.achievement_percentage = 100 * entry.achievement_count / total if total and entry.achievement_count <= total else None
        current = (entry.status, entry.start_date, entry.total_seconds, entry.achievement_mode,
                   entry.achievement_count, entry.achievement_percentage)
        if created or previous != current:
            entry.revision = (entry.revision or 0) + 1
            await db.flush()
            await game_activity.record(db, entry, before, await game_activity.snapshot(db, work, entry, achievements=state.sync_achievements))
            changed += 1
        checkpoints[app] = {**prior, 'work_id': work.id,
            'observed_playtime_minutes': data['playtime_minutes'],
            **({'last_played': data['last_played']} if data.get('last_played') is not None else {}),
            **({'playtime_minutes': max(prior.get('playtime_minutes', 0), data['playtime_minutes'])} if state.sync_playtime else {}),
            **({'achievement_checked_at': data['achievement_checked_at']} if data.get('achievement_checked_at') else {})}
    state.checkpoints = checkpoints
    return changed


def achievement_due(row, prior, stamp):
    checked = prior.get('achievement_checked_at')
    return (row['playtime_minutes'] != prior.get('observed_playtime_minutes')
            or row.get('last_played') is not None and row['last_played'] != prior.get('last_played')
            or not checked or datetime.fromisoformat(checked) <= stamp - timedelta(days=1))


async def run_steam_sync(user_id, job_id, _a=0, _b=0, connection_id=None):
    from db import AsyncSessionLocal
    await sync(AsyncSessionLocal, user_id, job_id, connection_id)


async def sync(factory, user_id, job_id, connection_id):
    async with factory() as db:
        if not await mark_job_running_unless_cancelled(db, job_id, current_step='Syncing Steam'):
            return
        state = await db.get(SteamConnection, connection_id)
        if not state or state.user_id != user_id or not state.imported or not allowed(await db.get(User, user_id)):
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed,
                error_message='Steam connection is not ready or Games access is unavailable.'))
            await db.commit()
            return
        native, generation = state.steam_id, state.generation
        selected, checkpoints, known = list(state.selection), dict(state.checkpoints), set(state.known_apps)
        active_works = set(await db.scalars(select(GameEntry.work_id).where(GameEntry.user_id == user_id,
            GameEntry.work_id.in_([value['work_id'] for value in checkpoints.values() if value.get('work_id')]))))
        selected = [app for app in selected if checkpoints.get(app, {}).get('work_id') in active_works]
        achievements_enabled, playtime_enabled, auto_add = state.sync_achievements, state.sync_playtime, state.auto_add_games
    try:
        if not achievements_enabled and not playtime_enabled and not auto_add:
            async with factory() as db:
                job = await running_job(db, job_id)
                job.status, job.current_step = SyncStatus.completed, 'Steam sync disabled'
                await db.commit()
            return
        source = SteamSource(factory)
        library = await source.library(native)
        additions = {row['app_id'] for row in library} - known if auto_add else set()
        target_ids = set(selected) | additions
        async with factory() as db:
            if additions:
                dates = await steam_cache.public_metadata(db, additions)
                library = [{**row, 'release_date': dates.get(row['app_id'], {}).get('release_date')} for row in library]
            library = await steam_cache.read_progress(db, user_id, native, library)
        pending = [row for row in library if row['app_id'] in target_ids and achievements_enabled and row['achievement_refresh_due']]
        pending.sort(key=lambda row: (not achievement_due(row, checkpoints.get(row['app_id'], {}), now()),
                                     checkpoints.get(row['app_id'], {}).get('achievement_checked_at', '')))
        requests, patches = 0, {}
        for offset in range(0, min(len(pending), 100), 4):
            values, count = await steam_cache.load_progress(factory, user_id, native, pending[offset:min(offset+4, 100)], source)
            requests += count
            patches.update({row['app_id']: row for row in values})
            async with factory() as db:
                await raise_if_cancelled(db, job_id)
        library = [patches.get(row['app_id'], row) for row in library]
        async with factory() as db:
            await game_tracking.lock_owner(db, user_id)
            await raise_if_cancelled(db, job_id)
            state = await db.get(SteamConnection, connection_id, with_for_update=True)
            if not state or state.generation != generation:
                raise SteamError('Steam connection changed during sync. Retry the sync.')
            changed = await apply_games(db, state, library, [*selected, *sorted(additions)], add_apps=additions)
            state.known_apps = sorted(known | {row['app_id'] for row in library})
            state.selection = [*selected, *sorted(app for app in additions if app in state.checkpoints)]
            job = await running_job(db, job_id)
            job.status, job.current_step = SyncStatus.completed, 'Steam up to date'
            job.total_items = job.processed_items = len(target_ids)
            job.stats = {'updated': changed, 'added': len(state.selection) - len(selected),
                         'achievement_requests': requests, 'library_requests': 1}
            unknown = sum(row.get('achievement_count') is None for row in patches.values())
            job.warnings = [f'Achievements unavailable for {unknown} games; existing progress was preserved.'] if unknown else []
            await db.commit()
        await steam_cache.save_public(factory, library)
    except SyncCancelled:
        return
    except Exception as error:
        async with factory() as db:
            await db.execute(update(SyncJob).where(SyncJob.id == job_id, SyncJob.status.in_((SyncStatus.pending, SyncStatus.running)))
                .values(status=SyncStatus.failed, error_message=str(error)[:1000] if isinstance(error, SteamError) else 'Steam sync failed. Retry later.'))
            await db.commit()
