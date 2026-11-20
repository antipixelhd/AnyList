"""Connection-owned planning entries; wishlist absence is never play evidence."""
from types import SimpleNamespace

from sqlalchemy import select, or_

from core import steam_cache, steam_targets, game_tracking
from models.catalogue import CatalogueIdentity
from models.games import GameEntry, GameChildState, GameFavorite


async def present(db, games, *, settled=False):
    if not games:
        return []
    metadata = await steam_cache.public_metadata(db, [game['app_id'] for game in games])
    states = await steam_targets.import_states(db, games, settled=settled)
    return [{**game, **{key: value for key, value in metadata.get(game['app_id'], {}).items()
        if key in ('name', 'poster', 'release_date') and value is not None}, 'mapping_state': states[game['app_id']]}
        for game in games if states[game['app_id']] != 'software']


async def work_map(db, apps):
    if not apps:
        return {}
    result = dict((await db.execute(select(CatalogueIdentity.external_id, CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id.in_(apps)))).all())
    result.update(await steam_targets.targets(db, apps))
    # A newly purchased edition may not yet have a published Steam identity.
    from core.steam_sync import verified_import_native
    public = await steam_cache.public_metadata(db, list(apps))
    candidates = {app: verified_import_native(row.get('igdb_classification')) for app, row in public.items() if app not in result}
    natives = dict((await db.execute(select(CatalogueIdentity.external_id, CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace == 'igdb.game', CatalogueIdentity.external_id.in_([v for v in candidates.values() if v])))).all())
    result.update({app: natives[native] for app, native in candidates.items() if native in natives})
    return result


async def handoff_purchases(db, state, library):
    """Let the normal library worker handle playtime and auto-playing after purchase."""
    managed = (state.wishlist_state or {}).get('entries', {})
    if not managed:
        return []
    owned = await work_map(db, {row['app_id'] for row in library})
    works = {row['work_id'] for row in managed.values()}
    references = {(row['work_id'], row['entry_id']) for row in managed.values()}
    entries = {row.work_id: row for row in await db.scalars(select(GameEntry).where(
        GameEntry.user_id == state.user_id, GameEntry.work_id.in_(works)))}
    checkpoints, selected = dict(state.checkpoints), set()
    for app, work in owned.items():
        entry = entries.get(work)
        if not entry or app in checkpoints or (work, entry.id) not in references:
            continue
        # No state/date write here. Existing auto-playing logic owns transitions.
        checkpoints[app] = {'work_id': work, 'start_pending': bool(state.auto_add_games and entry.status == 'planning')}
        selected.add(app)
    state.checkpoints = checkpoints
    return sorted(selected)


async def apply(db, state, games, library, *, selected=None):
    """Caller holds the owner lock. selected is explicit only during onboarding."""
    from core.steam_sync import apply_games
    previous = dict(state.wishlist_state or {})
    prior_apps = set(previous.get('apps', []))
    managed = dict(previous.get('entries', {}))
    current = {game['app_id'] for game in games}
    if not current and not prior_apps and not managed:
        state.wishlist_state = {'apps': [], 'entries': {}, 'pending': []}
        return {'wishlist_added': 0, 'wishlist_removed': 0}
    additions = set(selected) if selected is not None else (current - prior_apps) | (set(previous.get('pending', [])) & current)
    owned_apps = {row['app_id'] for row in library}
    owned = await work_map(db, owned_apps)
    mapping = await work_map(db, current)
    retained_works = {mapping[app] for app in current if app in mapping}
    retained_works.update(row['work_id'] for app, row in managed.items() if app in current)
    owned_works = set(owned.values())
    removed = 0
    for app in set(managed) - current:
        record = managed.pop(app)
        if app in owned_apps or record['work_id'] in owned_works or record['work_id'] in retained_works:
            continue
        entry = await db.scalar(select(GameEntry).where(GameEntry.user_id == state.user_id,
            GameEntry.work_id == record['work_id']))
        # Manual edits, status changes, deletion/re-addition all relinquish ownership.
        if entry and entry.id == record['entry_id'] and entry.revision == record['revision'] and entry.status == 'planning':
            personal = await db.scalar(select(or_(
                select(GameFavorite.user_id).where(GameFavorite.user_id == state.user_id, GameFavorite.work_id == entry.work_id).exists(),
                select(GameChildState.id).where(GameChildState.user_id == state.user_id, GameChildState.base_entry_id == entry.id).exists())))
            if personal:
                continue
            await game_tracking.remove_entry(db, state.user_id, entry.work_id, entry.revision, commit=False)
            removed += 1
    # Wishlist additions never reset an existing personal entry or owned game.
    additions -= owned_apps
    additions = {app for app in additions if mapping.get(app) not in owned_works}
    existing = set(await db.scalars(select(GameEntry.work_id).where(GameEntry.user_id == state.user_id,
        GameEntry.work_id.in_(set(mapping.values())))))
    scratch = SimpleNamespace(user_id=state.user_id, checkpoints={}, timezone=state.timezone,
        auto_add_games=False, sync_playtime=False, sync_achievements=False)
    if additions:
        await apply_games(db, scratch, games, sorted(additions), add_apps=additions)
    added = set()
    for app, record in scratch.checkpoints.items():
        if record['work_id'] in existing:
            continue
        entry = await db.scalar(select(GameEntry).where(GameEntry.user_id == state.user_id, GameEntry.work_id == record['work_id']))
        managed[app] = {'work_id': entry.work_id, 'entry_id': entry.id, 'revision': entry.revision}
        added.add(entry.work_id)
    state.wishlist_state = {'apps': sorted(current), 'entries': managed,
        'pending': sorted(additions - set(scratch.checkpoints))}
    return {'wishlist_added': len(added), 'wishlist_removed': removed}
