"""Hydrate requested canonical game stubs through the existing IGDB snapshot."""
import asyncio
from dataclasses import dataclass,field
from time import monotonic
from datetime import timedelta

from sqlalchemy import or_,select,update
from sqlalchemy.dialects.postgresql import insert

from core import catalogue
from core.catalogue_providers import CatalogueProviders,ProviderHTTP
from core.game_imdb_enrichment import now
from core.game_types import capabilities
from core.game_release_dates import VERSION as RELEASE_VERSION
from models.catalogue import CatalogueIdentity,MetadataSnapshot

_flights={}
_slots=asyncio.Semaphore(2)
_character_slots=asyncio.Semaphore(2)


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


async def header(db,native):
    return (await db.execute(select(MetadataSnapshot.entity_id,MetadataSnapshot.fetched_at,MetadataSnapshot.expires_at,
        MetadataSnapshot.next_attempt_at,MetadataSnapshot.lease_until,MetadataSnapshot.error_code,
        MetadataSnapshot.coverage['characters_complete'].as_boolean().label('characters_complete'),
        MetadataSnapshot.coverage['release_precision_version'].as_integer().label('release_precision_version')).where(
            MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',MetadataSnapshot.external_id==native))).one_or_none()


def status(row,expected=None):
    usable=bool(row and row.fetched_at and row.entity_id and (expected is None or row.entity_id==expected))
    active=bool(row and row.lease_until and row.lease_until>now())
    queued=bool(row and row.next_attempt_at)
    retry=max(1,int((row.next_attempt_at-now()).total_seconds())+1) if queued else 2
    error=row.error_code if row else None
    return {'primary_ready':usable,'entity_id':row.entity_id if usable else None,
        'complete':usable and row.characters_complete is True and not active and not queued and not error,
        'state':'retry' if error else 'loading' if active else 'pending' if queued else 'ready' if usable else 'unavailable',
        'pending':active or queued,'error':error,'retry_after':min(3600,retry)}


async def request(db,work,*,identities=None):
    native=await native_id(db,work,identities)
    if not native:return None,{'state':'unavailable','primary_ready':False,'pending':False,'complete':False,
        'error':'identity_unavailable','retry_after':0}
    row=await header(db,native);stamp=now()
    if row and row.entity_id not in (None,work.id):
        return None,{'state':'unavailable','primary_ready':False,'pending':False,'complete':False,'error':'identity_conflict','retry_after':0}
    if (row and row.fetched_at and row.release_precision_version!=RELEASE_VERSION and not row.next_attempt_at
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

    def start(self,native,expected):
        key=(id(self.scope),native)
        flight=_flights.get(key)
        if flight:return flight
        if len(_flights)>=64:return None
        flight=Flight();_flights[key]=flight
        flight.task=asyncio.create_task(self.refresh(flight,native,expected))
        def done(task):
            if _flights.get(key) is flight:_flights.pop(key,None)
            if not task.cancelled():task.exception()
        flight.task.add_done_callback(done)
        return flight

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
        native,state=await request(db,work,identities=identities)
        await db.commit()  # Waiting never reserves the request's SQL connection.
        initial=state['primary_ready']
        if not native or not state['pending'] or state.get('error'):return state,False
        flight=self.start(native,work.id)
        if initial or not flight:return state,False
        deadline=monotonic()+wait_seconds
        while monotonic()<deadline:
            try:
                if flight.task.done():await asyncio.sleep(min(1,max(0,deadline-monotonic())))
                else:await asyncio.wait_for(flight.primary.wait(),max(.001,deadline-monotonic()))
            except TimeoutError:break
            state=status(await header(db,native),work.id);await db.commit()
            if state['primary_ready'] or state['error'] or not state['pending']:break
        state=status(await header(db,native),work.id);await db.commit()
        return state,state['primary_ready'] and not initial

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
            jobs=(await db.execute(select(MetadataSnapshot.external_id,MetadataSnapshot.entity_id).where(
                MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',MetadataSnapshot.entity_id.is_not(None),
                MetadataSnapshot.next_attempt_at<=now(),or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(2))).all()
        tasks=[]
        for native,expected in jobs:
            async with self.factory() as db:
                from models.catalogue import CatalogueEntity
                work=await db.get(CatalogueEntity,expected)
                if not work or await native_id(db,work)!=native:
                    await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',
                        MetadataSnapshot.external_id==native,or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                        .values(next_attempt_at=None,error_code='identity_conflict',expires_at=now()+timedelta(days=1)))
                    await db.commit();continue
            flight=self.start(native,expected)
            if flight:tasks.append(flight.task)
        if tasks:await asyncio.gather(*(asyncio.shield(task) for task in tasks))


async def scheduler():
    from db import AsyncSessionLocal,engine
    service=GameMetadata(AsyncSessionLocal,scope=engine)
    try:
        while True:
            delay=5
            try:await service.tick()
            except asyncio.CancelledError:raise
            except Exception:delay=30
            await asyncio.sleep(delay)
    finally:
        tasks=[flight.task for (scope,_),flight in list(_flights.items()) if scope==id(service.scope)]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
