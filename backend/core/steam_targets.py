"""A Steam application can contribute progress to another catalogue game.

These are tracking relationships, not equivalent provider identities. Never
infer them from a shared title prefix, missing artwork or a failed lookup.
"""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core import catalogue, steam_cache
from core.catalogue_providers import ProviderError
from core.config import settings
from core.game_types import capabilities, canonical_game, igdb_game_class
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot

KIND='steam_progress_target'
# Steam exposes this mode as an advertising app; IGDB represents the main
# game (1122), without a separate Zombies work. Reviewed app IDs, never names.
COMPONENTS={'212910':'202970'}


async def targets(db, apps):
    return dict((await db.execute(select(MetadataSnapshot.external_id,MetadataSnapshot.entity_id).where(
        MetadataSnapshot.provider=='steam',MetadataSnapshot.kind==KIND,
        MetadataSnapshot.external_id.in_(apps),MetadataSnapshot.entity_id.is_not(None)))).all())


async def import_states(db, games, *, settled=False):
    """Batch indexed identities first; public evidence never contains viewer state."""
    from core.steam_sync import verified_import_native
    apps = [game['app_id'] for game in games]
    native = CatalogueIdentity.__table__.alias('native')
    known = dict((await db.execute(select(CatalogueIdentity.external_id, native.c.external_id).join(
        native, native.c.entity_id == CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id.in_(apps),
        native.c.namespace == 'igdb.game'))).all())
    combined = await targets(db, apps)
    public = await steam_cache.public_metadata(db, apps)
    result = {}
    for app in apps:
        evidence = public.get(app, {}).get('igdb_classification')
        if public.get(app, {}).get('steam_app_type') == 'software':
            result[app] = 'software'
        elif app in known or app in combined:
            result[app] = 'ready'
        elif app in COMPONENTS:
            # Components remain individually selectable; their target resolves
            # independently and selection never implies selecting its siblings.
            result[app] = 'pending' if not settled else 'ready'
        elif verified_import_native(evidence):
            result[app] = 'ready' if capabilities({'game_class':evidence.get('game_class','unknown')}).independent else 'unavailable'
        else:
            result[app] = 'unavailable' if settled or isinstance(evidence, dict) and evidence.get('state') in ('unmatched','ambiguous') else 'pending'
    return result


async def assign(db, app, target_app, *, evidence):
    """Caller holds catalogue write lock; operator/provider evidence is required."""
    from core.game_achievement_source import native_app
    native_app(app);native_app(target_app)
    if not evidence or app==target_app:
        raise catalogue.IdentityConflict('A component requires a different verified main game')
    identity=await catalogue.resolve_identity(db,'steam.app',target_app)
    work=await db.get(CatalogueEntity,identity.entity_id) if identity else None
    if not work or not capabilities(work.attributes).independent:
        raise catalogue.IdentityConflict('Main game identity is unavailable')
    source=await catalogue.resolve_identity(db,'steam.app',app)
    if source and source.entity_id==work.id:
        return work
    statement=insert(MetadataSnapshot).values(provider='steam',kind=KIND,external_id=app,
        entity_id=work.id,payload={'target_app':target_app,'evidence':evidence},fetched_at=steam_cache.now())
    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',set_={
        'entity_id':work.id,'payload':statement.excluded.payload,'fetched_at':statement.excluded.fetched_at}))
    apps=set((work.attributes or {}).get('steam_progress_apps') or [])
    apps.update(await db.scalars(select(CatalogueIdentity.external_id).where(
        CatalogueIdentity.entity_id==work.id,CatalogueIdentity.namespace=='steam.app')))
    apps.add(app)
    catalogue.merge_fields(work,{'attributes':{'steam_primary_app_id':target_app,'steam_progress_apps':sorted(apps)}},'steam')
    public=await steam_cache.public_metadata(db,list(apps))
    for native, item in public.items():
        if type(item.get('achievement_total')) is int:
            steam_cache.merge_total(work,native,item['achievement_total'],steam_cache.now())
    return work


async def classify(factory, games, *, source=None):
    """Batch exact IGDB app references. No title matching or per-game fan-out."""
    from core.game_browse_sources import GameBrowseSources
    from core import steam_software
    await steam_software.classify(factory, games)
    async with factory() as db:
        cached = await steam_cache.public_metadata(db, [game['app_id'] for game in games])
    games = [game for game in games if cached.get(game['app_id'], {}).get('steam_app_type') != 'software']
    if not games:
        return
    apps=list(dict.fromkeys(game['app_id'] for game in games))
    missing={}
    async with factory() as db:
        for app in apps:
            if app in COMPONENTS:
                if await catalogue.resolve_identity(db,'steam.app',COMPONENTS[app]):
                    await catalogue.write_lock(db)
                    await assign(db,app,COMPONENTS[app],evidence={'source':'reviewed_steam_component','steam_type':'advertising','igdb_game':'1122'})
                else:
                    missing[app]=COMPONENTS[app]
        await db.commit()
        native = CatalogueIdentity.__table__.alias('native')
        known = set(await db.scalars(select(CatalogueIdentity.external_id).join(native,
            native.c.entity_id == CatalogueIdentity.entity_id).where(CatalogueIdentity.namespace=='steam.app',
            CatalogueIdentity.external_id.in_(apps),native.c.namespace=='igdb.game')))
    if source is None and not (settings.igdb_client_id and settings.igdb_client_secret):
        return
    stamp=steam_cache.now()
    due=[app for app in apps if app not in known and app not in COMPONENTS and (
        not cached.get(app,{}).get('classification_checked_at') or
        datetime.fromisoformat(cached[app]['classification_checked_at'])<=stamp-timedelta(days=30))]
    source=source or GameBrowseSources(factory)
    if missing:
        from core.catalogue_normalize import image
        try:
            parents=await source.steam_games([int(app) for app in set(missing.values())])
            if len(parents) >= 500:
                parents=[]  # A capped component lookup cannot certify uniqueness.
        except ProviderError:
            parents=[]
        candidates={app:{} for app in missing.values()}
        for row in parents:
            native=str(row.get('uid',''));raw=canonical_game(row.get('game'))
            if native in candidates and raw and capabilities({'game_class':igdb_game_class(raw)}).independent:
                candidates[native][raw['id']]=raw
        async with factory() as db:
            await catalogue.write_lock(db)
            for app,parent in missing.items():
                if len(candidates[parent])!=1:
                    continue
                raw=next(iter(candidates[parent].values()))
                await catalogue.upsert_entity(db,{'kind':'game','name':raw['name'],'image_url':image(raw.get('cover')),
                    'attributes':{'game_class':igdb_game_class(raw),'igdb_id':str(raw['id'])},
                    'identities':[{'namespace':'igdb.game','external_id':str(raw['id'])},
                        {'namespace':'steam.app','external_id':parent}]},'igdb')
                await assign(db,app,parent,evidence={'source':'reviewed_steam_component','igdb_game':str(raw['id'])})
            await db.commit()
    for offset in range(0,len(due),100):
        batch=due[offset:offset+100]
        try:
            rows=await source.steam_games([int(app) for app in batch])
        except ProviderError:
            continue
        found={app:{} for app in batch}
        for row in rows:
            app=str(row.get('uid',''));raw=canonical_game(row.get('game'))
            if app in found and raw:
                found[app][raw['id']]=raw
        values=[]
        for app,matches in found.items():
            if len(rows)>=500 and not matches:
                continue  # A capped provider response cannot certify absence.
            item={'app_id':app,'classification_checked_at':stamp.isoformat()}
            evidence={'version':1,'state':'unmatched' if not matches else 'ambiguous','checked_at':stamp.isoformat()}
            if len(matches)==1:
                raw=next(iter(matches.values()))
                item.update(game_class=igdb_game_class(raw),igdb_id=str(raw['id']))
                evidence.update(state='bound' if len(rows)<500 else 'partial',game_class=item['game_class'],igdb_id=item['igdb_id'])
            item['igdb_classification']=evidence
            values.append(item)
        await steam_cache.save_public(factory,values)


async def retarget(db, state, mapping):
    """Move existing imported membership once, leaving source checkpoints intact."""
    from core import game_tracking
    from core.steam import SteamError
    from models.games import GameEntry, GameAchievementUnlock, GameChildState, GameFavorite
    checkpoints=dict(state.checkpoints)
    moved=set()
    for app,target in mapping.items():
        old=checkpoints.get(app,{}).get('work_id')
        if not old or old==target or (old,target) in moved:
            continue
        moved.add((old,target))
        source=await db.scalar(select(GameEntry).where(GameEntry.user_id==state.user_id,GameEntry.work_id==old))
        if source:
            unlock=await db.scalar(select(GameAchievementUnlock.achievement_id).where(
                GameAchievementUnlock.user_id==state.user_id,GameAchievementUnlock.work_id==old).limit(1))
            child=await db.scalar(select(GameChildState.id).where(GameChildState.base_entry_id==source.id).limit(1))
            if unlock or child:
                raise SteamError('This component has individual progress that needs review before combining games.')
            entry=await db.scalar(select(GameEntry).where(GameEntry.user_id==state.user_id,GameEntry.work_id==target))
            if entry is None:
                source.work_id=target
                source.revision+=1
            else:
                entry.total_seconds=(entry.total_seconds or 0)+(source.total_seconds or 0)
                for field in ('score','start_date','finish_date','completion_seconds','completion_source'):
                    if getattr(entry,field) is None:
                        setattr(entry,field,getattr(source,field))
                if source.notes and source.notes!=entry.notes:
                    entry.notes='\n\n'.join(filter(None,(entry.notes,source.notes)))
                entry.revision+=1
                await db.delete(source)
            favorite=await db.get(GameFavorite,(state.user_id,old))
            if favorite:
                await game_tracking.set_favorite(db,state.user_id,target,True)
                await db.delete(favorite)
            await db.flush()
        # Missing membership stays deleted: update its destination without
        # recreating it, and preserve every per-app high-water mark.
        for native, prior in list(checkpoints.items()):
            if prior.get('work_id')==old:
                checkpoints[native]={**prior,'work_id':target}
    state.checkpoints=checkpoints
