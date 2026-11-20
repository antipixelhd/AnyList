"""Admin-triggered orphan scans and confirmed catalogue reconciliation."""
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select, text

from core import catalogue
from core.catalogue_providers import ProviderError
from core.game_types import canonical_game, capabilities, igdb_game_class, mobile_only
from core.statistics_snapshots import utcnow
from models import SyncJob, SyncStatus
from models.catalogue import CatalogueEntity, CatalogueIdentity

SCAN_LIMIT = 100
SCAN_LIFETIME = timedelta(hours=1)
ORPHANS = """SELECT e.id,e.name,
 coalesce(array_agg(s.external_id ORDER BY s.external_id) FILTER (WHERE s.id IS NOT NULL),'{}') AS apps
 FROM catalogue_entities e LEFT JOIN catalogue_identities s ON s.entity_id=e.id AND s.namespace='steam.app'
 WHERE e.kind='game' AND NOT EXISTS (SELECT 1 FROM catalogue_identities i WHERE i.entity_id=e.id AND i.namespace='igdb.game')
 AND (s.id IS NOT NULL OR EXISTS (SELECT 1 FROM game_entries g WHERE g.work_id=e.id)
 OR EXISTS (SELECT 1 FROM game_child_states g WHERE g.work_id=e.id)
 OR EXISTS (SELECT 1 FROM game_favorites g WHERE g.work_id=e.id)
 OR EXISTS (SELECT 1 FROM game_activity g WHERE g.work_id=e.id)) GROUP BY e.id"""


def usable_scan(job):
    if not job or job.job_type != 'data_health' or job.status != SyncStatus.completed or (job.stats or {}).get('action') != 'game_scan':
        return None
    scan = (job.stats or {}).get('game_scan')
    try:
        age = utcnow() - datetime.fromisoformat(scan['checked_at'])
        if not timedelta(0) <= age <= SCAN_LIFETIME:
            return None
    except (TypeError, KeyError, ValueError):
        return None
    return scan


async def require_scan(db, job_id):
    scan = usable_scan(await db.get(SyncJob, job_id))
    if scan is None:
        raise HTTPException(409, 'Run a new game scan before reconciling.')
    return scan


async def scan_games(factory, *, after_id=0, source=None):
    from core.game_browse_sources import GameBrowseSources
    async with factory() as db:
        rows = (await db.execute(text(f'SELECT * FROM ({ORPHANS}) g WHERE id>:after ORDER BY id LIMIT :limit'),
            {'after': after_id, 'limit': SCAN_LIMIT + 1})).mappings().all()
    more = len(rows) > SCAN_LIMIT
    rows = rows[:SCAN_LIMIT]
    candidates = []
    by_app = {}
    for row in rows:
        apps = row['apps']
        candidate = {'id': row['id'], 'name': row['name'], 'steam_app_id': apps[0] if len(apps) == 1 else None,
            'igdb_id': None, 'duplicate_id': None, 'game_class': None,
            'status': 'unknown', 'detail': 'No Steam identity; manual mapping review is required.'}
        candidates.append(candidate)
        if len(apps) > 1:
            candidate.update(status='ambiguous', detail='Multiple Steam identities require review.')
        elif len(apps) == 1 and apps[0].isdecimal() and int(apps[0]) > 0:
            by_app[apps[0]] = candidate
    if by_app:
        source = source or GameBrowseSources(factory)
        try:
            references = await source.steam_games([int(app) for app in by_app])
            if len(references) >= 500:
                for row in by_app.values():
                    row['detail'] = 'Provider response was incomplete; scan again later.'
            else:
                found = {app: {} for app in by_app}
                incomplete = set()
                for reference in references:
                    app = str(reference.get('uid', ''))
                    if app not in by_app:
                        continue
                    raw = reference.get('game')
                    game = canonical_game(raw)
                    if game:
                        found[app][str(game['id'])] = game
                    elif isinstance(raw, dict) and mobile_only(raw):
                        by_app[app].update(status='excluded', detail='Mobile-only provider record.')
                    else:
                        incomplete.add(app)
                for app, matches in found.items():
                    row = by_app[app]
                    if app in incomplete:
                        row['detail'] = 'Incomplete provider identity evidence; scan again later.'
                    elif len(matches) > 1:
                        row.update(status='ambiguous', detail='Steam references identify more than one IGDB game.')
                    elif len(matches) == 1:
                        native, game = next(iter(matches.items()))
                        kind = igdb_game_class(game)
                        if not native.isdecimal() or not 0 < int(native) <= 2147483647:
                            row['detail'] = 'Provider game identity is invalid.'
                        elif kind == 'unknown':
                            row['detail'] = 'Provider game type is unknown.'
                        elif not capabilities({'game_class': kind}).independent:
                            row.update(status='excluded', detail='Provider record is not an independent game.')
                        else:
                            row.update(status='match', igdb_id=native, game_class=kind, detail=game.get('name') or row['name'])
                    elif row['status'] != 'excluded':
                        row.update(status='unmatched', detail='No exact IGDB reference found.')
        except ProviderError:
            for row in by_app.values():
                row.update(status='failed', detail='Provider lookup failed; scan again later.')
    matches = [row for row in candidates if row['status'] == 'match']
    if matches:
        async with factory() as db:
            native_roots = dict((await db.execute(select(CatalogueIdentity.external_id, CatalogueIdentity.entity_id).where(
                CatalogueIdentity.namespace == 'igdb.game', CatalogueIdentity.external_id.in_([row['igdb_id'] for row in matches])))).all())
        for row in matches:
            target = native_roots.get(row['igdb_id'])
            row['duplicate_id'] = target if target and target != row['id'] else None
    return {'checked_at': utcnow().isoformat(), 'next_after_id': rows[-1]['id'] if more else None, 'candidates': candidates}


async def reconcile_scan(factory, scan):
    """Apply only server-stored, fresh matches after rechecking their identities."""
    from core.game_identity_reconciliation import reconcile
    result = {**scan, 'candidates': [dict(row) for row in scan['candidates']]}
    for row in result['candidates']:
        if row['status'] != 'match':
            continue
        async with factory() as db:
            try:
                await catalogue.write_lock(db)
                identity = await catalogue.resolve_identity(db, 'steam.app', row['steam_app_id'])
                native = await catalogue.resolve_identity(db, 'igdb.game', row['igdb_id'])
                if not identity or identity.entity_id != row['id']:
                    raise catalogue.IdentityConflict('Steam identity changed; run a new scan.')
                if native and native.entity_id == row['id']:
                    row.update(status='already_linked', detail='Already linked to the verified IGDB game.')
                    continue
                if (native.entity_id if native else None) != row['duplicate_id']:
                    raise catalogue.IdentityConflict('IGDB identity changed; run a new scan.')
                root = await db.get(CatalogueEntity, row['id'])
                existing = await db.scalar(select(CatalogueIdentity.id).where(
                    CatalogueIdentity.entity_id == row['id'], CatalogueIdentity.namespace == 'igdb.game'))
                if not root or root.kind != 'game' or existing:
                    raise catalogue.IdentityConflict('Game mapping changed; run a new scan.')
                ids = [{'namespace': 'steam.app', 'external_id': row['steam_app_id']},
                       {'namespace': 'igdb.game', 'external_id': row['igdb_id']}]
                attrs = {'igdb_id': row['igdb_id'], 'game_class': row['game_class']}
                await reconcile(db, ids, 'igdb', attrs)
                work = await catalogue.upsert_entity(db, {'kind': 'game', 'name': root.name,
                    'identities': ids, 'attributes': attrs}, 'igdb')
                if work.id != row['id']:
                    raise catalogue.IdentityConflict('Imported game root must be retained.')
                await db.commit()
                row.update(status='reconciled', detail='Reconciled; private tracking was preserved.')
            except catalogue.IdentityConflict as error:
                await db.rollback()
                row.update(status='conflict', detail=str(error))
    return result


def scan_counts(scan):
    rows = scan['candidates']
    return {'examined': len(rows), **{status: sum(row['status'] == status for row in rows)
        for status in ('match', 'reconciled', 'already_linked', 'unmatched', 'ambiguous', 'excluded', 'conflict', 'unknown', 'failed')
        if any(row['status'] == status for row in rows)}}


async def run_game_health(factory, job_id, action, *, scan_job_id=None, after_id=0):
    if action == 'game_scan':
        scan = await scan_games(factory, after_id=after_id)
    else:
        async with factory() as db:
            scan = await require_scan(db, scan_job_id)
        scan = await reconcile_scan(factory, scan)
    async with factory() as db:
        job = await db.get(SyncJob, job_id)
        job.stats = {**job.stats, 'game_scan': scan}
        await db.commit()
    return scan_counts(scan)
