"""Steam review and incremental account sync with durable source checkpoints."""
import asyncio
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


def verified_import_native(evidence):
    if not isinstance(evidence, dict) or evidence.get('version') != 1 or evidence.get('state') != 'bound':
        return None
    try:
        checked = datetime.fromisoformat(evidence.get('checked_at', ''))
        recent = now() - timedelta(days=30) <= checked <= now()
    except (ValueError, TypeError):
        return None
    candidate = str(evidence.get('igdb_id') or '')
    return candidate if recent and candidate.isdecimal() and 0 < int(candidate) <= 2147483647 else None


async def background_details(factory, user_id, connection_id, job_id):
    """Owned by the server, so dismissing or leaving the picker cannot stop it."""
    async with factory() as db:
        if not await mark_job_running_unless_cancelled(db, job_id):
            return
        state = await db.get(SteamConnection, connection_id)
        if state is None or state.user_id != user_id:
            return
        library, native, generation = list(state.library), state.steam_id, state.generation
    try:
        source = SteamSource(factory)
        library.sort(key=lambda game: -game['playtime_minutes'])
        async with factory() as db:
            library = await attach_dates(db, library)
            observed = await steam_cache.read_progress(db, user_id, native, library)
        observed = [game for game in observed if game.get('steam_app_type') != 'software']
        library = [game for game in library if game.get('steam_app_type') != 'software']
        pending = [game for game in observed if game['achievement_refresh_due'] or game['achievement_state'] == 'loading' or game.get('achievement_refreshing')]
        while pending:
            for offset in range(0, len(pending), 4):
                await steam_cache.load_progress(factory, user_id, native, pending[offset:offset+4], source)
                async with factory() as db:
                    await raise_if_cancelled(db, job_id)
                    job = await running_job(db, job_id)
                    job.processed_items = min(len(library)-len(pending)+offset+4, len(library))
                    await db.commit()
            async with factory() as db:
                observed = await steam_cache.read_progress(db, user_id, native, library)
            pending = [game for game in observed if game['achievement_refresh_due'] or game['achievement_state'] == 'loading' or game.get('achievement_refreshing')]
            if pending:
                await asyncio.sleep(1.5)
        async with factory() as db:
            await game_tracking.lock_owner(db, user_id)
            state = await db.get(SteamConnection, connection_id, with_for_update=True)
            if state and state.generation == generation and state.imported:
                games = await steam_cache.read_progress(db, user_id, native, library)
                await apply_games(db, state, games, state.selection)
            job = await running_job(db, job_id)
            job.status = SyncStatus.completed
            job.processed_items = job.total_items = len(library)
            await db.commit()
    except SyncCancelled:
        return
    except Exception:
        async with factory() as db:
            await db.execute(update(SyncJob).where(SyncJob.id == job_id, SyncJob.status == SyncStatus.running)
                .values(status=SyncStatus.failed, error_message='Background Steam details interrupted. Resume the import to retry.'))
            await db.commit()


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
        library=list(state.library)
    from core.steam_targets import classify
    try:
        await classify(factory,library)
    except SteamError as error:
        async with factory() as db:
            await db.execute(update(SyncJob).where(SyncJob.id == job_id, SyncJob.status == SyncStatus.running)
                .values(status=SyncStatus.failed, error_message=str(error)))
            await db.commit()
        return
    async with factory() as db:
        state=await db.get(SteamConnection,connection_id,with_for_update=True)
        if not state or state.user_id!=user_id:
            return
        if state.imported:
            job = await running_job(db, job_id)
            job.status = SyncStatus.completed
            await db.commit()
            return
        state.library=await attach_dates(db,state.library)
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
    return [{**game, **{key:value for key,value in public.get(game['app_id'], {}).items()
        if key in ('poster','release_date','game_class','steam_app_type') and value is not None}} for game in library]


async def apply_games(db, state, library, selected, *, initial=False, add_apps=None, initial_status='completed', completion_dates=None,start_dates=None):
    """One list write per canonical game; source app counters remain independent."""
    from core import steam_targets
    from core.game_metadata import native_id
    by_app = {row['app_id']: row for row in library}
    additions = set(add_apps or ())
    today = datetime.now(ZoneInfo(state.timezone or 'UTC')).date()
    await catalogue.write_lock(db)
    # Resolve before creating a Steam-only root. Preparation's exact public
    # evidence is authoritative only while recent, complete and unambiguous.
    public = await steam_cache.public_metadata(db, list(selected))
    selected = [app for app in selected if public.get(app, {}).get('steam_app_type') != 'software']
    mapping = await steam_targets.targets(db, selected)
    canonical_admission = {}
    for target in set(mapping.values()):
        work = await db.get(CatalogueEntity, target)
        canonical_admission[target] = bool(work and await native_id(db, work))
    # Retargeting itself moves private membership and checkpoints. Reject an
    # orphan destination before that mutation, including ordinary progress sync.
    await steam_targets.retarget(db, state, {app: target for app, target in mapping.items()
        if canonical_admission[target]})
    checkpoints, groups = dict(state.checkpoints), {}
    for app in dict.fromkeys(selected):
        if app in mapping and not canonical_admission[mapping[app]]:
            continue
        data = by_app.get(app)
        if data is None:
            continue
        identity = await catalogue.resolve_identity(db, 'steam.app', app)
        if app in mapping:
            work = await db.get(CatalogueEntity, mapping[app])
        elif identity:
            work = await db.get(CatalogueEntity, identity.entity_id)
        elif initial or app in additions:
            evidence = public.get(app, {}).get('igdb_classification')
            native = verified_import_native(evidence)
            if not native or not capabilities({'game_class':evidence.get('game_class','unknown')}).independent:
                continue
            existing = await catalogue.resolve_identity(db, 'igdb.game', native) if native else None
            if existing:
                work = await db.get(CatalogueEntity, existing.entity_id)
                await catalogue.bind_identity(db, work.id, 'steam.app', app, 'igdb',
                    {'igdb_classification': evidence})
            else:
                identities = [{'namespace': 'steam.app', 'external_id': app}]
                if native:
                    identities.append({'namespace': 'igdb.game', 'external_id': native})
                work = await catalogue.upsert_entity(db, {
                    'kind': 'game', 'name': data['name'], 'image_url': data['poster'],
                    'attributes': {'game_class': evidence['game_class'],
                        **({'igdb_id': native} if native else {}),
                        **({'release_date': data['release_date'], 'release_date_precision': 'day' if len(data['release_date']) == 10 else 'month' if len(data['release_date']) == 7 else 'year'} if data.get('release_date') else {})},
                    'identities': identities,
                }, 'steam')
        else:
            continue
        # Every admission path, including combined-app progress targets, must
        # already resolve to a canonical IGDB game. Legacy progress can still
        # sync, but an import/auto-add cannot create another orphan membership.
        if work and (initial or app in additions):
            if work.id not in canonical_admission:
                canonical_admission[work.id] = bool(await native_id(db, work))
            if not canonical_admission[work.id]:
                continue
        if work and app not in mapping and data.get('game_class') and data['game_class']!='unknown' and (work.field_sources or {}).get('attributes.game_class') in (None,'steam'):
            catalogue.merge_fields(work,{'attributes':{'game_class':data['game_class']}},'igdb')
        if not work or not capabilities(work.attributes).independent:
            continue
        if data.get('achievement_total') is not None and (identity is None or app in mapping or (work.attributes or {}).get('steam_progress_apps')):
            steam_cache.merge_total(work, app, data['achievement_total'], now())
        groups.setdefault(work.id, (work, []))[1].append(data)
    changed = 0
    for work, games in groups.values():
        apps={game['app_id'] for game in games}
        if len(apps)>1 and not (work.attributes or {}).get('steam_progress_apps'):
            catalogue.merge_fields(work,{'attributes':{'steam_progress_apps':sorted(apps),
                'steam_primary_app_id':(work.attributes or {}).get('steam_primary_app_id') or
                    ((work.attributes or {}).get('achievement_catalogue') or {}).get('steam_app_id') or sorted(apps)[0]}},'steam')
            for game in games:
                if game.get('achievement_total') is not None:
                    steam_cache.merge_total(work,game['app_id'],game['achievement_total'],now())
        entry = await db.scalar(select(GameEntry).where(GameEntry.user_id == state.user_id, GameEntry.work_id == work.id))
        automatic = any(game['app_id'] in additions for game in games)
        if entry is None and not initial and not automatic:
            continue
        before = await game_activity.snapshot(db, work, entry, achievements=state.sync_achievements)
        created = entry is None
        if created:
            entry = GameEntry(user_id=state.user_id, work_id=work.id, revision=0, status='planning')
            db.add(entry)
        priors = {game['app_id']: dict(checkpoints.get(game['app_id'], {})) for game in games}
        previous = (entry.status, entry.start_date, entry.finish_date, entry.total_seconds,
                    entry.achievement_mode, entry.achievement_count, entry.achievement_percentage)
        if initial:
            entry.status = initial_status
            if initial_status=='playing':
                dates=[value for game in games if (value:=(start_dates or {}).get(game['app_id'])) is not None]
                entry.start_date=min(dates) if dates else None
            if initial_status == 'completed':
                dates = []
                for game in games:
                    stamp = game.get('last_played')
                    inferred = datetime.fromtimestamp(stamp, ZoneInfo(state.timezone or 'UTC')).date() if stamp else None
                    date = (completion_dates or {}).get(game['app_id'], inferred)
                    if date is not None:
                        dates.append(date)
                entry.finish_date = max(dates) if dates else None
                if entry.completion_seconds is None:
                    entry.completion_source = 'unknown'
        if created and automatic:
            for prior in priors.values():
                prior['start_pending'] = True
        if state.auto_add_games and any(prior.get('start_pending') for prior in priors.values()):
            if entry.status != 'planning' or entry.start_date is not None:
                for prior in priors.values():
                    prior['start_pending'] = False
            elif sum(game['playtime_minutes'] for game in games) > 30:
                entry.status, entry.start_date = 'playing', today
                for prior in priors.values():
                    prior['start_pending'] = False
        if state.sync_playtime:
            if initial or all('playtime_minutes' not in prior for prior in priors.values()):
                entry.total_seconds = max(entry.total_seconds or 0, sum(game['playtime_minutes'] for game in games) * 60)
            else:
                delta = sum(max(0, game['playtime_minutes'] - priors[game['app_id']].get('playtime_minutes', 0)) for game in games)
                entry.total_seconds = (entry.total_seconds or 0) + delta * 60
        if state.sync_achievements:
            counts = [game.get('achievement_count') if game.get('achievement_count') is not None
                      else priors[game['app_id']].get('achievement_count') for game in games]
            counts.extend(prior.get('achievement_count') for native,prior in checkpoints.items()
                if native in selected and native not in apps and prior.get('work_id')==work.id)
            if all(count is not None for count in counts):
                entry.achievement_mode, entry.achievement_count = 'count', sum(counts)
                _, total, _ = game_tracking.achievement_metadata(work)
                entry.achievement_percentage = 100 * entry.achievement_count / total if total and entry.achievement_count <= total else None
        current = (entry.status, entry.start_date, entry.finish_date, entry.total_seconds,
                   entry.achievement_mode, entry.achievement_count, entry.achievement_percentage)
        if created or previous != current:
            entry.revision = (entry.revision or 0) + 1
            await db.flush()
            if initial:await game_activity.record_import(db,entry,timezone_name=state.timezone or 'UTC')
            else:await game_activity.record(db, entry, before, await game_activity.snapshot(db, work, entry, achievements=state.sync_achievements))
            changed += 1
        for game in games:
            app = game['app_id']
            checkpoints[app] = {**priors[app], 'work_id': work.id,
                'observed_playtime_minutes': game['playtime_minutes'],
                **({'last_played': game['last_played']} if game.get('last_played') is not None else {}),
                **({'playtime_minutes': max(priors[app].get('playtime_minutes', 0), game['playtime_minutes'])} if state.sync_playtime else {}),
                **({'achievement_count': game['achievement_count']} if state.sync_achievements and game.get('achievement_count') is not None else {}),
                **({'achievement_checked_at': game['achievement_checked_at']} if game.get('achievement_checked_at') else {})}
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
        from core.steam_targets import classify
        await classify(factory,library)
        additions = {row['app_id'] for row in library} - known if auto_add else set()
        target_ids = set(selected) | additions
        async with factory() as db:
            library = await attach_dates(db, library)
            library = await steam_cache.read_progress(db, user_id, native, library)
        pending = [row for row in library if row['app_id'] in target_ids and row.get('steam_app_type') != 'software' and achievements_enabled and row['achievement_refresh_due']]
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
        from core.steam_artwork import refresh as refresh_artwork
        await refresh_artwork(factory, library)
    except SyncCancelled:
        return
    except Exception as error:
        async with factory() as db:
            await db.execute(update(SyncJob).where(SyncJob.id == job_id, SyncJob.status.in_((SyncStatus.pending, SyncStatus.running)))
                .values(status=SyncStatus.failed, error_message=str(error)[:1000] if isinstance(error, SteamError) else 'Steam sync failed. Retry later.'))
            await db.commit()
