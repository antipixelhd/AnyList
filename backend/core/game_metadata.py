"""Hydrate requested canonical game stubs through the existing IGDB snapshot."""
import asyncio
import uuid
from dataclasses import dataclass,field
from time import monotonic
from datetime import datetime,timedelta

from sqlalchemy import or_,select,update,exists
from sqlalchemy.dialects.postgresql import insert

from core import catalogue
from core.catalogue_providers import CatalogueProviders,ProviderHTTP
from core.game_imdb_enrichment import now,request as admit,status as job_status,snapshot
from core.game_types import capabilities
from core.game_release_dates import VERSION as RELEASE_VERSION
from core.catalogue_normalize import GAME_ARTWORK_VERSION,game_artwork
from models.catalogue import CatalogueEntity,CatalogueIdentity,MetadataSnapshot

_flights={}
_slots=asyncio.Semaphore(2)
_character_slots=asyncio.Semaphore(2)
IDENTITY_JOB='game_steam_identity'


@dataclass
class Flight:
    primary:asyncio.Event=field(default_factory=asyncio.Event)
    task:asyncio.Task|None=None
    entity_id:int|None=None


async def native_id(db,work,identities=None):
    if work.kind!='game' or not capabilities(work.attributes).searchable and (work.attributes or {}).get('game_class','unknown')!='unknown':return None
    if identities is None:
        identities=list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id==work.id,
            CatalogueIdentity.namespace=='igdb.game')))
    ids={row.external_id for row in identities if row.namespace=='igdb.game'}
    primary=str((work.attributes or {}).get('igdb_id') or '')
    native=primary if primary in ids else next(iter(ids)) if len(ids)==1 and not primary else None
    return native if native and native.isdecimal() and 0<int(native)<=2147483647 else None


async def unmapped_steam_app(db,work,identities=None):
    from core.game_ratings import app_id
    if not work:return None
    if identities is None:
        identities=list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id==work.id)))
    if (work.attributes or {}).get('igdb_id') or any(row.namespace=='igdb.game' for row in identities):return None
    return await app_id(db,work,identities)


async def queue_imports(db,work_ids):
    """Durable, public mapping work; never wait for providers in an import."""
    if not work_ids:return
    mapped=exists().where(CatalogueIdentity.entity_id==CatalogueEntity.id,CatalogueIdentity.namespace=='igdb.game')
    ids=list(await db.scalars(select(CatalogueEntity.id).where(CatalogueEntity.id.in_(work_ids),
        ~mapped,CatalogueEntity.attributes['igdb_id'].astext.is_(None))))
    for offset in range(0,len(ids),250):
        statement=insert(MetadataSnapshot).values([{'provider':'igdb','kind':IDENTITY_JOB,'external_id':str(work_id),
            'entity_id':work_id,'next_attempt_at':now(),'coverage':{'priority':'background'}} for work_id in ids[offset:offset+250]])
        await db.execute(statement.on_conflict_do_nothing(constraint='uq_catalogue_snapshot'))


async def header(db,native):
    return (await db.execute(select(MetadataSnapshot.entity_id,MetadataSnapshot.fetched_at,MetadataSnapshot.expires_at,
        MetadataSnapshot.next_attempt_at,MetadataSnapshot.lease_until,MetadataSnapshot.error_code,
        MetadataSnapshot.coverage['characters_complete'].as_boolean().label('characters_complete'),
        MetadataSnapshot.coverage['release_precision_version'].as_integer().label('release_precision_version'),
        MetadataSnapshot.coverage['artwork_policy_version'].as_integer().label('artwork_policy_version')).where(
            MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',MetadataSnapshot.external_id==native))).one_or_none()


def status(row,expected=None):
    usable=bool(row and row.fetched_at and row.entity_id and (expected is None or row.entity_id==expected))
    active=bool(row and row.lease_until and row.lease_until>now())
    queued=bool(row and row.next_attempt_at)
    retry=max(1,int((row.next_attempt_at-now()).total_seconds())+1) if queued else 2
    error=row.error_code if row else None
    return {'primary_ready':usable,'entity_id':row.entity_id if usable else None,
        'primary_revision':f'{row.fetched_at.isoformat()}:{row.artwork_policy_version}' if usable else None,
        'complete':usable and row.characters_complete is True and not active and not queued and not error,
        'state':'retry' if error else 'loading' if active else 'pending' if queued else 'ready' if usable else 'unavailable',
        'pending':active or queued,'error':error,'retry_after':min(3600,retry)}


async def upgrade_cached_artwork(db,work,native):
    """Re-rank already retrieved geometry without fetching credits/characters again."""
    await catalogue.write_lock(db)
    await db.refresh(work,with_for_update=True)
    if await native_id(db,work)!=native:return False
    source=(await db.execute(select(MetadataSnapshot.id,
        MetadataSnapshot.payload['game']['id'].astext.label('native'),
        MetadataSnapshot.payload['game']['screenshots'].label('screenshots'),
        MetadataSnapshot.payload['game']['artworks'].label('artworks')).where(
            MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',MetadataSnapshot.external_id==native,
            MetadataSnapshot.entity_id==work.id,MetadataSnapshot.fetched_at.is_not(None),
            MetadataSnapshot.coverage['artwork_policy_version'].as_integer().in_((1,2,3,4))).with_for_update())).one_or_none()
    if not source or source.native!=native or any(value is not None and not isinstance(value,list)
        for value in (source.screenshots,source.artworks)):return False
    # Legacy artwork lacks image types: refresh it before declaring logo policy current.
    if source.artworks and any(not isinstance(row,dict) or 'image_type' not in row for row in source.artworks):return False
    raw={'screenshots':source.screenshots or [],'artworks':source.artworks or []}
    images=[value for rows in raw.values() for value in rows]
    if images and not any(isinstance(value,dict) and all(type(value.get(key)) is int and value[key]>0
        for key in ('width','height')) for value in images):return False
    ranked=game_artwork(raw)
    attrs=dict(work.attributes or {});fields=dict(work.field_sources or {})
    if 'attributes.scene_artwork' not in (work.protected_fields or []) and fields.get('attributes.scene_artwork') in (None,'igdb'):
        attrs['scene_artwork']=ranked['scene_artwork'];fields['attributes.scene_artwork']='igdb'
    if 'attributes.artwork' not in (work.protected_fields or []) and fields.get('attributes.artwork') in (None,'igdb'):
        from core.catalogue_normalize import image
        excluded={image(row) for row in images}-set(ranked['artwork'])
        attrs['artwork']=list(dict.fromkeys([*ranked['artwork'],
            *(value for value in attrs.get('artwork') or [] if image(value) not in excluded)]))
        fields['attributes.artwork']='igdb'
    # Preserve the complete gallery, selected backdrop, source timestamps,
    # retry/lease state and all private tracking. Only presentation policy moves.
    attrs['artwork_policy_version']=GAME_ARTWORK_VERSION
    work.attributes=attrs;work.field_sources=fields
    await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.id==source.id).values(
        coverage=MetadataSnapshot.coverage.op('||')({'artwork_policy_version':GAME_ARTWORK_VERSION})))
    return True


async def request(db,work,*,identities=None):
    native=await native_id(db,work,identities)
    if not native:return None,{'state':'unavailable','primary_ready':False,'pending':False,'complete':False,
        'error':'identity_unavailable','retry_after':0}
    row=await header(db,native);stamp=now()
    if row and row.entity_id not in (None,work.id):
        return None,{'state':'unavailable','primary_ready':False,'pending':False,'complete':False,'error':'identity_conflict','retry_after':0}
    if row and row.fetched_at and row.artwork_policy_version in (1,2,3,4):
        await upgrade_cached_artwork(db,work,native)
        row=await header(db,native)
    if (row and row.fetched_at and (row.release_precision_version!=RELEASE_VERSION or row.artwork_policy_version!=GAME_ARTWORK_VERSION) and not row.next_attempt_at
            and not (row.lease_until and row.lease_until>stamp) and row.expires_at and row.expires_at>stamp):
        await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',
            MetadataSnapshot.external_id==native,MetadataSnapshot.next_attempt_at.is_(None),
            or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=stamp)).values(expires_at=stamp))
        row=await header(db,native)
    if row and (row.lease_until and row.lease_until>stamp or row.next_attempt_at or row.expires_at and row.expires_at>stamp):
        return native,status(row,work.id)
    statement=insert(MetadataSnapshot).values(provider='igdb',kind='game',external_id=native,entity_id=work.id,next_attempt_at=stamp)
    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',
        set_={'next_attempt_at':stamp,'entity_id':work.id},where=(
            or_(MetadataSnapshot.entity_id.is_(None),MetadataSnapshot.entity_id==work.id)
            & MetadataSnapshot.next_attempt_at.is_(None)
            & or_(MetadataSnapshot.expires_at.is_(None),MetadataSnapshot.expires_at<=stamp)
            & or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=stamp))))
    return native,{**status(row,work.id),'state':'pending','pending':True,'retry_after':2}


class GameMetadata:
    def __init__(self,factory,*,scope=None,providers=None):
        self.factory=factory;self.scope=scope if scope is not None else factory
        self.providers=providers or CatalogueProviders(ProviderHTTP(session_factory=factory))

    def start(self,native,expected,*,identity=False):
        key=(id(self.scope),native)
        flight=_flights.get(key)
        if flight:return flight
        if len(_flights)>=64:return None
        flight=Flight();_flights[key]=flight
        flight.task=asyncio.create_task(self.refresh_identity(flight,expected) if identity else self.refresh(flight,native,expected))
        def done(task):
            if _flights.get(key) is flight:_flights.pop(key,None)
            if not task.cancelled():task.exception()
        flight.task.add_done_callback(done)
        return flight

    async def refresh_identity(self,flight,expected):
        """Resolve imported Steam stubs by exact provider references, never names."""
        from core.game_browse_sources import GameBrowseSources
        from core.game_ratings import app_id,GameRatings
        from core.game_types import canonical_game,igdb_game_class,IGDB_TYPES
        from core.steam_cache import PUBLIC_KIND
        from models.catalogue import CatalogueEntity
        token=uuid.uuid4().hex
        query=snapshot(IDENTITY_JOB,expected,provider='igdb')
        async with _slots:
            async with self.factory() as db:
                job=await db.scalar(query.with_for_update())
                if not job or not job.next_attempt_at or job.next_attempt_at>now() or job.lease_until and job.lease_until>now():return
                work=await db.get(CatalogueEntity,expected)
                app=await unmapped_steam_app(db,work)
                cached=await db.scalar(select(MetadataSnapshot.payload).where(MetadataSnapshot.provider=='steam',
                    MetadataSnapshot.kind==PUBLIC_KIND,MetadataSnapshot.external_id==app)) if app else None
                job.lease_token=token;job.lease_until=now()+timedelta(minutes=3)
                await db.commit()
            try:
                if not app:raise catalogue.IdentityConflict('Steam identity is unavailable')
                rows=None
                # Preparation already batch-resolves exact app references. Reuse
                # a recent positive classification rather than fetch it again.
                evidence=cached.get('igdb_classification') if isinstance(cached,dict) else None
                if isinstance(evidence,dict) and evidence.get('version')==1 and evidence.get('state')=='bound':
                    candidate=str(evidence.get('igdb_id') or '')
                    kind=next((key for key,value in IGDB_TYPES.items() if value==evidence.get('game_class')),None)
                    try:recent=now()-timedelta(days=30)<=datetime.fromisoformat(evidence.get('checked_at',''))<=now()
                    except (ValueError,TypeError):recent=False
                    if recent and kind is not None and candidate.isdecimal() and 0<int(candidate)<=2147483647:
                        rows=[{'uid':app,'game':{'id':int(candidate),'game_type':kind}}]
                if rows is None:
                    async with asyncio.timeout(45):
                        rows=await GameBrowseSources(self.factory).steam_games([int(app)])
                matches={}
                for row in rows:
                    if str(row.get('uid',''))!=app:continue
                    raw=canonical_game(row.get('game'))
                    if not raw:raise catalogue.IdentityConflict('Incomplete Steam reference')
                    matches[str(raw['id'])]=raw
                # Capped responses and competing references cannot certify a match.
                if len(rows)>=500 or len(matches)>1:raise catalogue.IdentityConflict('Ambiguous Steam reference')
                if matches and not capabilities({'game_class':igdb_game_class(next(iter(matches.values())))}).searchable:
                    matches={}
                async with self.factory() as db:
                    await catalogue.write_lock(db)
                    job=await db.scalar(query.with_for_update())
                    if not job or job.lease_token!=token:return
                    work=await db.get(CatalogueEntity,expected)
                    native=await native_id(db,work)
                    if await app_id(db,work)!=app or not native and await unmapped_steam_app(db,work)!=app:
                        raise catalogue.IdentityConflict('Steam or IGDB identity changed')
                    if matches:
                        matched=next(iter(matches))
                        if native and native!=matched or (work.attributes or {}).get('igdb_id') not in (None,'',matched):
                            raise catalogue.IdentityConflict('IGDB identity changed')
                        raw=matches[matched]
                        # upsert rejects references already owned by another work;
                        # it cannot merge memberships or move private tracking.
                        await catalogue.upsert_entity(db,{'kind':'game','name':work.name,
                            'attributes':{'igdb_id':matched,'game_class':igdb_game_class(raw)},
                            'identities':[{'namespace':'steam.app','external_id':app},
                                {'namespace':'igdb.game','external_id':matched}]},'igdb')
                        foreground=(job.coverage or {}).get('priority')!='background'
                        if foreground:
                            native,state=await request(db,work)
                            if not native:raise catalogue.IdentityConflict('IGDB identity is unavailable')
                    job.coverage={**(job.coverage or {}),'state':'complete' if matches else 'unavailable','complete':True}
                    job.fetched_at=now();job.expires_at=now()+timedelta(days=30)
                    job.error_code=None;job.next_attempt_at=None;job.lease_token=job.lease_until=None
                    await db.commit()
                if matches and foreground:self.start(native,expected)
            except BaseException as error:
                async with self.factory() as db:
                    # Roll back the entire publication before recording a failure.
                    job=await db.scalar(query.with_for_update())
                    if job and job.lease_token==token:
                        if isinstance(error,asyncio.CancelledError):job.next_attempt_at=now()
                        else:GameRatings.fail(job,error)
                        job.lease_token=job.lease_until=None;await db.commit()
                if isinstance(error,asyncio.CancelledError):raise
            finally:flight.primary.set()

    async def refresh(self,flight,native,expected):
        primary_held=False;characters_held=False
        async def primary(entity_id):
            nonlocal primary_held,characters_held
            flight.entity_id=entity_id;flight.primary.set()
            if primary_held:_slots.release();primary_held=False
            await _character_slots.acquire();characters_held=True
        try:
            await _slots.acquire();primary_held=True
            async with self.factory() as db:
                result=await catalogue.refresh_metadata(db,self.providers,'igdb','game',native,
                    expected_entity_id=expected,on_primary=primary)
                flight.entity_id=result.get('entity_id')
                return result
        finally:
            if primary_held:_slots.release()
            if characters_held:_character_slots.release()
            flight.primary.set()

    async def ensure(self,db,work,*,identities=None,wait_seconds=15):
        deadline=monotonic()+wait_seconds
        identity_changed=False
        native,state=await request(db,work,identities=identities)
        if not native:
            if await unmapped_steam_app(db,work,identities):
                state=await admit(db,work.id,kind=IDENTITY_JOB,provider='igdb')
                await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',
                    MetadataSnapshot.kind==IDENTITY_JOB,MetadataSnapshot.external_id==str(work.id),
                    MetadataSnapshot.coverage['priority'].astext.is_distinct_from('interactive')).values(
                        coverage=MetadataSnapshot.coverage.op('||')({'priority':'interactive'})))
                await db.commit()
                if state['pending'] and not state.get('error'):
                    flight=self.start(f'steam:{work.id}',work.id,identity=True)
                    if flight:
                        try:await asyncio.wait_for(flight.primary.wait(),max(.001,deadline-monotonic()))
                        except TimeoutError:pass
                await db.refresh(work);identities=None
                if not await native_id(db,work):
                    state=await job_status(db,work.id,kind=IDENTITY_JOB,provider='igdb');await db.commit()
                    return {**state,'state':state['state'] if state['pending'] else 'unavailable','primary_ready':False},False
                identity_changed=True
                native,state=await request(db,work)
        await db.commit()  # Waiting never reserves the request's SQL connection.
        initial=state['primary_ready']
        if not native or not state['pending'] or state.get('error'):return state,identity_changed
        flight=self.start(native,work.id)
        if initial or not flight:return state,identity_changed
        while monotonic()<deadline:
            try:
                if flight.task.done():await asyncio.sleep(min(1,max(0,deadline-monotonic())))
                else:await asyncio.wait_for(flight.primary.wait(),max(.001,deadline-monotonic()))
            except TimeoutError:break
            state=status(await header(db,native),work.id);await db.commit()
            if state['primary_ready'] or state['error'] or not state['pending']:break
        state=status(await header(db,native),work.id);await db.commit()
        return state,identity_changed or state['primary_ready'] and not initial

    async def resolve(self,db,native,*,wait_seconds=15):
        identity=await catalogue.resolve_identity(db,'igdb.game',native)
        if identity:
            from models.catalogue import CatalogueEntity
            work=await db.get(CatalogueEntity,identity.entity_id)
            state,changed=await self.ensure(db,work)
            if changed:await db.refresh(work)
            return work.id if state['primary_ready'] else None
        await db.commit()
        flight=self.start(native,None)
        if not flight:return None
        deadline=monotonic()+wait_seconds
        while monotonic()<deadline:
            try:
                if flight.task.done():await asyncio.sleep(min(1,max(0,deadline-monotonic())))
                else:await asyncio.wait_for(flight.primary.wait(),max(.001,deadline-monotonic()))
            except TimeoutError:return None
            identity=await catalogue.resolve_identity(db,'igdb.game',native)
            state=status(await header(db,native),identity.entity_id if identity else None);await db.commit()
            if identity and state['primary_ready']:return identity.entity_id
            if state['error'] or not state['pending']:return None
        return None

    async def tick(self):
        async with self.factory() as db:
            identity_query=select(MetadataSnapshot.entity_id).where(
                MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind==IDENTITY_JOB,
                MetadataSnapshot.next_attempt_at<=now(),or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
            imports=list(await db.scalars(identity_query.where(MetadataSnapshot.coverage['priority'].astext.is_distinct_from('background'))
                .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(2)))
            jobs=(await db.execute(select(MetadataSnapshot.external_id,MetadataSnapshot.entity_id).where(
                MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',MetadataSnapshot.entity_id.is_not(None),
                MetadataSnapshot.next_attempt_at<=now(),or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(2))).all()
            background=None
            if not jobs and not imports and not any(scope==id(self.scope) and not flight.primary.is_set() for (scope,_),flight in _flights.items()):
                background=await db.scalar(identity_query.where(MetadataSnapshot.coverage['priority'].astext=='background')
                    .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(1))
        for work_id in imports:self.start(f'steam:{work_id}',work_id,identity=True)
        for native,expected in jobs:
            async with self.factory() as db:
                from models.catalogue import CatalogueEntity
                work=await db.get(CatalogueEntity,expected)
                if not work or await native_id(db,work)!=native:
                    await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',
                        MetadataSnapshot.external_id==native,or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                        .values(next_attempt_at=None,error_code='identity_conflict',expires_at=now()+timedelta(days=1)))
                    await db.commit();continue
            # Flights own their leases, exceptions and lifecycle. Waiting for
            # optional characters here would block the next scheduler tick and
            # starve unrelated primary jobs despite free primary slots.
            self.start(native,expected)
        if background is not None:
            flight=self.start(f'steam:{background}',background,identity=True)
            if flight:await asyncio.shield(flight.task)
            return True
        return False


async def scheduler():
    from db import AsyncSessionLocal,engine
    service=GameMetadata(AsyncSessionLocal,scope=engine)
    try:
        while True:
            delay=5
            try:
                if await service.tick():delay=0
            except asyncio.CancelledError:raise
            except Exception:delay=30
            await asyncio.sleep(delay)
    finally:
        tasks=[flight.task for (scope,_),flight in list(_flights.items()) if scope==id(service.scope)]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
