"""IGDB completion estimates, separate from recorded personal playing time."""
import asyncio
import uuid
from datetime import timedelta,timezone

from sqlalchemy import or_,select,update

from core import catalogue
from core.catalogue_providers import ProviderError
from core.game_browse_sources import GameBrowseSources,positive
from core.game_imdb_enrichment import now,snapshot as metadata,request as admit,status as job_status
from core.game_types import capabilities
from models.catalogue import CatalogueEntity,CatalogueIdentity,MetadataSnapshot

JOB='game_estimate_job'
COMPONENT='game_time_to_beat'
FIELDS={'main_story_seconds':'hastily','main_sides_seconds':'normally','completionist_seconds':'completely'}
_flights={}
_slots=asyncio.Semaphore(2)


def snapshot(kind,work_id):return metadata(kind,work_id,provider='igdb')


async def primary_id(db,work):
    if work.kind!='game' or not capabilities(work.attributes).searchable:return None
    ids=set(await db.scalars(select(CatalogueIdentity.external_id).where(
        CatalogueIdentity.entity_id==work.id,CatalogueIdentity.namespace=='igdb.game')))
    primary=str((work.attributes or {}).get('igdb_id') or '')
    native=primary if primary in ids else next(iter(ids)) if len(ids)==1 and not primary else None
    return native if native and native.isdecimal() and positive(int(native)) else None


def normalized(rows,native):
    if not isinstance(rows,list) or len(rows)>1:raise ProviderError('bad_response')
    values={key:None for key in FIELDS}
    result={'source':'igdb','source_game_id':str(native),'source_record_id':None,'samples':None,**values}
    if not rows:return result
    row=rows[0]
    if not isinstance(row,dict) or type(row.get('game_id')) is not int or row['game_id']!=int(native) or not positive(row.get('id')):
        raise ProviderError('bad_response')
    result['source_record_id']=str(row['id'])
    for target,field in FIELDS.items():
        value=row.get(field)
        if value is not None and (type(value) is not int or value<0):raise ProviderError('bad_response')
        result[target]=value or None  # Source zeros are unavailable estimates.
    samples=row.get('count')
    if samples is not None and (type(samples) is not int or samples<0):raise ProviderError('bad_response')
    result['samples']=samples
    return result


def projection(work,native):
    value=(work.attributes or {}).get('completion_estimates')
    if not isinstance(value,dict) or value.get('source_game_id')!=native:return None
    return {**{key:value.get(key) for key in ('source','source_game_id','source_record_id','samples','fetched_at')},
        **{key:value.get(key) if type(value.get(key)) is int and value[key]>0 else None for key in FIELDS}}


async def view(db,work,*,enqueue=False):
    native=await primary_id(db,work)
    if not native:return {'estimates':None,'status':{'state':'unavailable','pending':False,'complete':False}}
    previous=(work.attributes or {}).get('completion_estimates') or {}
    if enqueue and isinstance(previous,dict) and previous.get('source_game_id') and previous['source_game_id']!=native:
        await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind==JOB,
            MetadataSnapshot.external_id==str(work.id),MetadataSnapshot.coverage['source_game_id'].astext!=native,
            MetadataSnapshot.next_attempt_at.is_(None),
            or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now())).values(expires_at=None))
    state=await (admit(db,work.id,kind=JOB,provider='igdb',entity_id=work.id) if enqueue else job_status(db,work.id,kind=JOB,provider='igdb'))
    return {'estimates':projection(work,native),'status':state}


class GameTimeToBeat:
    def __init__(self,sessions,*,source=None):
        self.sessions=sessions;self.source=source or GameBrowseSources(sessions)

    async def run(self,work_id):
        key=(id(self.sessions),work_id);task=_flights.get(key)
        if task is None:
            if len(_flights)>=64:return 'busy'
            task=asyncio.create_task(self.bounded(work_id));_flights[key]=task
            def done(finished):
                if _flights.get(key) is finished:_flights.pop(key,None)
                if not finished.cancelled():finished.exception()
            task.add_done_callback(done)
        return await asyncio.shield(task)

    async def bounded(self,work_id):
        async with _slots:return await self._run(work_id)

    async def _run(self,work_id):
        token=uuid.uuid4().hex
        async with self.sessions() as db:
            job=await db.scalar(snapshot(JOB,work_id).with_for_update())
            if not job or not job.next_attempt_at or job.next_attempt_at>now() or job.lease_until and job.lease_until>now():return 'cached'
            work=await db.get(CatalogueEntity,work_id)
            native=await primary_id(db,work) if work else None
            if not native:
                job.error_code='identity_conflict';job.next_attempt_at=None;job.expires_at=now()+timedelta(days=1)
                job.coverage={'state':'unavailable','complete':False};await db.commit();return 'unavailable'
            job.lease_token=token;job.lease_until=now()+timedelta(minutes=2);await db.commit()
        try:
            async with asyncio.timeout(60):
                rows=await self.source.igdb('game_time_to_beats',
                    f'fields id,game_id,hastily,normally,completely,count; where game_id={int(native)}; limit 2;')
            values=normalized(rows,native);values['fetched_at']=now().replace(tzinfo=timezone.utc).isoformat()
            async with self.sessions() as db:
                await catalogue.write_lock(db)
                job=await db.scalar(snapshot(JOB,work_id).with_for_update())
                if not job or job.lease_token!=token:return 'superseded'
                work=await db.get(CatalogueEntity,work_id)
                if not work or await primary_id(db,work)!=native:raise catalogue.IdentityConflict('Game identity changed')
                component=await db.scalar(snapshot(COMPONENT,work_id))
                if not component:
                    component=MetadataSnapshot(provider='igdb',kind=COMPONENT,external_id=str(work_id),entity_id=work_id);db.add(component)
                catalogue.merge_fields(work,{'attributes':{'completion_estimates':values}},'igdb')
                complete=any(values[key] is not None for key in FIELDS)
                expiry=now()+timedelta(days=30 if complete else 7)
                component.payload=values;component.coverage={'state':'available' if complete else 'empty','complete':True}
                component.fetched_at=now();component.expires_at=expiry
                job.coverage={'state':'complete','complete':True,'received':len(rows),'total':len(rows),'source_game_id':native}
                job.fetched_at=now();job.expires_at=expiry;job.attempts=0
                job.error_code=job.next_attempt_at=job.lease_token=job.lease_until=None
                await db.commit();return 'ready'
        except asyncio.CancelledError:
            await self.release(work_id,token);raise
        except Exception as error:
            async with self.sessions() as db:
                job=await db.scalar(snapshot(JOB,work_id).with_for_update())
                if job and job.lease_token==token:
                    job.attempts+=1;job.error_code=getattr(error,'code','identity_conflict' if isinstance(error,catalogue.IdentityConflict) else 'timeout' if isinstance(error,TimeoutError) else 'ingestion_failed')
                    delay=getattr(error,'retry_after',None) or min(3600,60*2**min(job.attempts-1,6))
                    job.next_attempt_at=now()+timedelta(seconds=min(3600,max(1,delay)))
                    job.lease_token=job.lease_until=None;await db.commit()
            return 'retry'

    async def release(self,work_id,token):
        async with self.sessions() as db:
            job=await db.scalar(snapshot(JOB,work_id).with_for_update())
            if job and job.lease_token==token:
                job.lease_token=job.lease_until=None;job.next_attempt_at=now();await db.commit()

    async def tick(self):
        async with self.sessions() as db:
            ids=list(await db.scalars(select(MetadataSnapshot.entity_id).where(MetadataSnapshot.provider=='igdb',
                MetadataSnapshot.kind==JOB,MetadataSnapshot.next_attempt_at<=now(),
                or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(2)))
        if ids:await asyncio.gather(*(self.run(work_id) for work_id in ids))


async def scheduler():
    from db import AsyncSessionLocal
    service=GameTimeToBeat(AsyncSessionLocal)
    try:
        while True:
            delay=3
            try:await service.tick()
            except asyncio.CancelledError:raise
            except Exception:delay=30
            await asyncio.sleep(delay)
    finally:
        tasks=[task for (scope,_),task in list(_flights.items()) if scope==id(AsyncSessionLocal)]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
