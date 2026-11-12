"""Hydrate requested canonical game stubs through the existing IGDB snapshot."""
import asyncio
import logging
from dataclasses import dataclass,field
from time import monotonic
from datetime import timedelta

from sqlalchemy import or_,select,update,case
from sqlalchemy.dialects.postgresql import insert

from core import catalogue
from core.enrichment_retry import requested_coverage, demand_expired
from core.catalogue_providers import CatalogueProviders,ProviderHTTP
from core.game_imdb_enrichment import now
from core.game_types import capabilities
from core.game_release_dates import VERSION as RELEASE_VERSION
from core.catalogue_normalize import GAME_ARTWORK_VERSION,game_artwork
from models.catalogue import CatalogueIdentity,MetadataSnapshot

_flights={}
_slots=asyncio.Semaphore(2)
_character_slots=asyncio.Semaphore(2)
IDENTITY_JOB='game_steam_identity'


@dataclass
class Flight:
    primary:asyncio.Event=field(default_factory=asyncio.Event)
    task:asyncio.Task|None=None
    entity_id:int|None=None
    prepare_cast:bool=False


async def native_id(db,work,identities=None):
    if work.kind!='game' or not capabilities(work.attributes).searchable and (work.attributes or {}).get('game_class','unknown')!='unknown':return None
    if identities is None:
        identities=list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id==work.id,
            CatalogueIdentity.namespace=='igdb.game')))
    ids={row.external_id for row in identities if row.namespace=='igdb.game'}
    primary=str((work.attributes or {}).get('igdb_id') or '')
    native=primary if primary in ids else next(iter(ids)) if len(ids)==1 and not primary else None
    return native if native and native.isdecimal() and 0<int(native)<=2147483647 else None


async def header(db,native):
    return (await db.execute(select(MetadataSnapshot.entity_id,MetadataSnapshot.fetched_at,MetadataSnapshot.expires_at,
        MetadataSnapshot.next_attempt_at,MetadataSnapshot.lease_until,MetadataSnapshot.error_code,
        MetadataSnapshot.coverage['requested_at'].astext.label('requested_at'),
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
        'state':'unavailable' if error and not usable and not (active or queued) else
            'retry' if error else 'loading' if active else 'pending' if queued else 'ready' if usable else 'unavailable',
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
            MetadataSnapshot.coverage['artwork_policy_version'].as_integer().in_(range(1,GAME_ARTWORK_VERSION))).with_for_update())).one_or_none()
    if not source or source.native!=native or any(value is not None and not isinstance(value,list)
        for value in (source.screenshots,source.artworks)):return False
    # Refresh artwork without type evidence before declaring the logo policy current.
    if source.artworks and any(not isinstance(row,dict) or
        row.get('image_type') is None and row.get('artwork_type') is None for row in source.artworks):return False
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
    if not row or row.next_attempt_at or not row.expires_at or row.expires_at<=stamp:
        from core.game_work import demand, job_key
        await demand(db,job_key('igdb','game',native))
    if row and row.entity_id not in (None,work.id):
        return None,{'state':'unavailable','primary_ready':False,'pending':False,'complete':False,'error':'identity_conflict','retry_after':0}
    if row and row.fetched_at and row.artwork_policy_version in range(1,GAME_ARTWORK_VERSION):
        await upgrade_cached_artwork(db,work,native)
        row=await header(db,native)
    if (row and row.fetched_at and (row.release_precision_version!=RELEASE_VERSION or row.artwork_policy_version!=GAME_ARTWORK_VERSION) and not row.next_attempt_at
            and not (row.lease_until and row.lease_until>stamp) and row.expires_at and row.expires_at>stamp):
        await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',
            MetadataSnapshot.external_id==native,MetadataSnapshot.next_attempt_at.is_(None),
            or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=stamp)).values(expires_at=stamp))
        row=await header(db,native)
    if row and (row.lease_until and row.lease_until>stamp or row.next_attempt_at or row.expires_at and row.expires_at>stamp):
        if row.next_attempt_at and (not row.requested_at or row.requested_at<(stamp-timedelta(days=1)).isoformat()):
            await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',
                MetadataSnapshot.external_id==native).values(coverage=MetadataSnapshot.coverage.op('||')(requested_coverage(stamp))))
        return native,status(row,work.id)
    statement=insert(MetadataSnapshot).values(provider='igdb',kind='game',external_id=native,entity_id=work.id,next_attempt_at=stamp,coverage=requested_coverage(stamp))
    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',
        set_={'next_attempt_at':stamp,'entity_id':work.id,'coverage':MetadataSnapshot.coverage.op('||')(requested_coverage(stamp))},where=(
            or_(MetadataSnapshot.entity_id.is_(None),MetadataSnapshot.entity_id==work.id)
            & MetadataSnapshot.next_attempt_at.is_(None)
            & or_(MetadataSnapshot.expires_at.is_(None),MetadataSnapshot.expires_at<=stamp)
            & or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=stamp))))
    return native,{**status(row,work.id),'state':'pending','pending':True,'retry_after':2}


class GameMetadata:
    def __init__(self,factory,*,scope=None,providers=None):
        self.factory=factory;self.scope=scope if scope is not None else factory
        self.providers=providers or CatalogueProviders(ProviderHTTP(session_factory=factory))

    async def warm(self,db,native):
        """Admit public intent work; clicks join/promote this same canonical job."""
        identity=await catalogue.resolve_identity(db,'igdb.game',native)
        if identity:
            from models.catalogue import CatalogueEntity
            work=await db.get(CatalogueEntity,identity.entity_id)
            from core.game_imdb_enrichment import request as request_credits
            await request_credits(db,work.id)
            await self.ensure(db,work,wait_seconds=0)
            return
        from core.game_work import demand,job_key
        await demand(db,job_key('igdb','game',native))
        await db.commit()
        self.start(native,None,prepare_cast=True)

    def start(self,native,expected,*,prepare_cast=False):
        key=(id(self.scope),native)
        flight=_flights.get(key)
        if flight:
            flight.prepare_cast |= prepare_cast
            return flight
        if len(_flights)>=64:return None
        flight=Flight(prepare_cast=prepare_cast);_flights[key]=flight
        flight.task=asyncio.create_task(self.refresh(flight,native,expected))
        def done(task):
            if _flights.get(key) is flight:_flights.pop(key,None)
            if not task.cancelled():task.exception()
        flight.task.add_done_callback(done)
        return flight


    async def refresh(self,flight,native,expected):
        from core.game_work import job_context
        async with job_context(self.factory,'igdb','game',native):
            return await self._refresh(flight,native,expected)

    async def _refresh(self,flight,native,expected):
        primary_held=False;characters_held=False
        async def primary(entity_id):
            nonlocal primary_held,characters_held
            flight.entity_id=entity_id;flight.primary.set()
            from core.game_work import current, Work, Priority
            current.set(Work(Priority.FINISH,current.get().owner))
            if primary_held:_slots.release();primary_held=False
            if flight.prepare_cast:
                # The canonical root has committed. Queue its public cast now,
                # independently of optional IGDB characters or browser arrival.
                # This subscriber must not poison already-published primary data.
                try:
                    from core.game_imdb_enrichment import request as request_credits
                    async with self.factory() as cast_db:
                        await request_credits(cast_db,entity_id)
                        await cast_db.commit()
                except Exception:
                    logging.getLogger(__name__).warning('Game cast admission deferred to details')
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
            return state, False  # Legacy orphan repair is an explicit admin operation.
        if native and state['pending']:
            await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',
                MetadataSnapshot.kind=='game',MetadataSnapshot.external_id==native,
                MetadataSnapshot.coverage['priority'].astext=='background').values(
                    coverage=MetadataSnapshot.coverage.op('||')({'priority':'interactive'})))
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
            from core.game_imdb_enrichment import request as request_credits
            await request_credits(db,work.id)
            state,changed=await self.ensure(db,work,wait_seconds=wait_seconds)
            if changed:await db.refresh(work)
            return work.id if state['primary_ready'] else None
        from core.game_work import demand, job_key
        await demand(db,job_key('igdb','game',native))
        await db.commit()
        flight=self.start(native,None,prepare_cast=True)
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
        from core.game_work import eligible_jobs, job_order
        async with self.factory() as db:
            jobs=(await db.execute(select(MetadataSnapshot.external_id,MetadataSnapshot.entity_id,MetadataSnapshot.coverage['priority'].astext,
                MetadataSnapshot.coverage['requested_at'].astext,MetadataSnapshot.error_code).where(
                MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',MetadataSnapshot.entity_id.is_not(None),eligible_jobs(),
                MetadataSnapshot.next_attempt_at<=now(),or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                .order_by(job_order(),case((MetadataSnapshot.coverage['priority'].astext=='background',1),else_=0),MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(2))).all()
        # One background primary at a time reserves capacity for a page click.
        background_started=False
        for native,expected,background_job,requested,error in jobs:
            async with self.factory() as db:
                if error and demand_expired(requested,now()):
                    await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',
                        MetadataSnapshot.external_id==native,MetadataSnapshot.coverage['requested_at'].astext==requested,
                        or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                        .values(next_attempt_at=None,expires_at=now(),coverage=MetadataSnapshot.coverage.op('||')({'retry_mode':'on_demand'})))
                    await db.commit();continue
                from models.catalogue import CatalogueEntity
                work=await db.get(CatalogueEntity,expected)
                canonical=await native_id(db,work) if work else None
                if not work or canonical!=native:
                    alias=bool(canonical and await db.scalar(select(CatalogueIdentity.id).where(
                        CatalogueIdentity.entity_id==expected,CatalogueIdentity.namespace=='igdb.game',
                        CatalogueIdentity.external_id==native)))
                    await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',
                        MetadataSnapshot.external_id==native,or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                        .values(next_attempt_at=None,error_code=None if alias else 'identity_conflict',expires_at=now()+timedelta(days=1),
                                coverage=MetadataSnapshot.coverage.op('||')({'state':'superseded','canonical_igdb_id':canonical} if alias else {})))
                    await db.commit();continue
            if background_job=='background':
                if background_started or any(scope==id(self.scope) and not flight.primary.is_set() for (scope,_),flight in _flights.items()):continue
                background_started=True
            # Flights own their leases, exceptions and lifecycle. Waiting for
            # optional characters here would block the next scheduler tick and
            # starve unrelated primary jobs despite free primary slots.
            self.start(native,expected)
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
