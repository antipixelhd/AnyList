"""On-demand game ratings with independent component freshness and shared jobs."""
import asyncio
import uuid
from datetime import timedelta,timezone

from sqlalchemy import or_,select,update

from core import catalogue
from core.game_rating_source import GameRatingSource,PARTS
from core.game_offers import steam_identity
from core.game_types import capabilities
from core.game_imdb_enrichment import now,snapshot as metadata,request as admit,status as job_status
from models.catalogue import CatalogueEntity,CatalogueIdentity,MetadataSnapshot

JOB='game_ratings_job'
_flights={}
_slots=asyncio.Semaphore(2)


def snapshot(kind,key):return metadata(kind,key,provider='steam')


async def app_id(db,work,identities=None):
    if not work or work.kind!='game' or not capabilities(work.attributes).searchable:return None
    if identities is None:
        identities=list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id==work.id,
            CatalogueIdentity.namespace=='steam.app').limit(2)))
    app=steam_identity(identities)
    return app if app and int(app)<=4294967295 else None


def projection(work,app):
    value=(work.attributes or {}).get('game_ratings')
    return {**value} if app and isinstance(value,dict) and value.get('steam_app_id')==app else None


async def critic_fallback(db,work,identities=None):
    """Reuse verified existing RAWG metadata without new matching or fan-out."""
    value=(work.attributes or {}).get('metacritic')
    if type(value) is not int or not 0<=value<=100 or (work.field_sources or {}).get('attributes.metacritic')!='rawg':return None
    verified=any(row.namespace=='rawg.game' and row.external_id.isdecimal() and int(row.external_id)>0 for row in identities) if identities is not None else await db.scalar(
        select(CatalogueIdentity.id).where(CatalogueIdentity.entity_id==work.id,CatalogueIdentity.namespace=='rawg.game').limit(1))
    if not verified:return None
    return {'score':value,'url':None,'source':'rawg','platform':None}


async def view(db,work,*,enqueue=False,identities=None):
    app=await app_id(db,work,identities)
    values=projection(work,app)
    if not values or (values.get('critic') or {}).get('score') is None:
        fallback=await critic_fallback(db,work,identities) if work.kind=='game' and capabilities(work.attributes).searchable else None
        if fallback:values={**(values or {'steam_app_id':app}),'critic':fallback}
    if not app:return {'ratings':values,'status':{'state':'ready' if values else 'unavailable','pending':False,'complete':bool(values)}}
    previous=(work.attributes or {}).get('game_ratings') or {}
    if enqueue and isinstance(previous,dict) and previous.get('steam_app_id') and previous['steam_app_id']!=app:
        await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='steam',MetadataSnapshot.kind==JOB,
            MetadataSnapshot.external_id==str(work.id),MetadataSnapshot.coverage['steam_app_id'].astext.is_distinct_from(app),
            MetadataSnapshot.next_attempt_at.is_(None),or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
            .values(expires_at=None).execution_options(synchronize_session=False))
    state=await (admit(db,work.id,kind=JOB,provider='steam',entity_id=work.id) if enqueue else job_status(db,work.id,kind=JOB,provider='steam'))
    return {'ratings':values,'status':state}


class GameRatings:
    def __init__(self,factory,source=None):self.factory=factory;self.source=source or GameRatingSource(factory)

    async def run(self,work_id):
        key=(id(self.factory),work_id);task=_flights.get(key)
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
        async with self.factory() as db:
            job=await db.scalar(snapshot(JOB,work_id).with_for_update())
            if not job or not job.next_attempt_at or job.next_attempt_at>now() or job.lease_until and job.lease_until>now():return 'cached'
            work=await db.get(CatalogueEntity,work_id);app=await app_id(db,work)
            if not app:
                job.error_code='identity_conflict';job.next_attempt_at=None;job.expires_at=now()+timedelta(days=1)
                job.coverage={'state':'unavailable','complete':False};await db.commit();return 'unavailable'
            components={row.kind.removeprefix('game_rating_'):row for row in await db.scalars(select(MetadataSnapshot).where(
                MetadataSnapshot.provider=='steam',MetadataSnapshot.entity_id==work_id,
                MetadataSnapshot.kind.in_([f'game_rating_{part}' for part in PARTS])))}
            # A failed component can retry without fetching successful siblings.
            due=[part for part in PARTS if part not in components or not components[part].expires_at or
                components[part].expires_at<=now() or (components[part].coverage or {}).get('steam_app_id')!=app]
            job.lease_token=token;job.lease_until=now()+timedelta(minutes=3);await db.commit()
        failures=[]
        try:
            for part in due:
                try:
                    stamp=now().replace(tzinfo=timezone.utc)
                    async with asyncio.timeout(45):value=await self.source.fetch(app,part,stamp)
                    value={**value,'fetched_at':stamp.isoformat()}
                    async with self.factory() as db:
                        await catalogue.write_lock(db)
                        job=await db.scalar(snapshot(JOB,work_id).with_for_update())
                        if not job or job.lease_token!=token or job.lease_until<=now():return 'superseded'
                        work=await db.get(CatalogueEntity,work_id)
                        if await app_id(db,work)!=app:raise catalogue.IdentityConflict('Game Steam identity changed')
                        record=await db.scalar(snapshot(f'game_rating_{part}',work_id))
                        if not record:
                            record=MetadataSnapshot(provider='steam',kind=f'game_rating_{part}',external_id=str(work_id),entity_id=work_id);db.add(record)
                        record.payload=value;record.coverage={'state':'complete','complete':True,'steam_app_id':app}
                        record.fetched_at=now();record.expires_at=now()+timedelta(days=30) if part=='critic' else now()+timedelta(hours=6)
                        previous=projection(work,app) or {'steam_app_id':app}
                        catalogue.merge_fields(work,{'attributes':{'game_ratings':{**previous,part:value}}},'steam')
                        await db.commit()
                except catalogue.IdentityConflict:raise
                except Exception as error:failures.append(error)
            async with self.factory() as db:
                job=await db.scalar(snapshot(JOB,work_id).with_for_update())
                if not job or job.lease_token!=token:return 'superseded'
                if failures:self.fail(job,failures[0])
                else:
                    expiry=await db.scalar(select(MetadataSnapshot.expires_at).where(MetadataSnapshot.provider=='steam',
                        MetadataSnapshot.entity_id==work_id,MetadataSnapshot.kind.in_([f'game_rating_{part}' for part in PARTS]))
                        .order_by(MetadataSnapshot.expires_at).limit(1))
                    job.coverage={'state':'complete','complete':True,'steam_app_id':app}
                    job.fetched_at=now();job.expires_at=expiry or now()+timedelta(hours=6)
                    job.next_attempt_at=job.error_code=None;job.attempts=0
                job.lease_token=job.lease_until=None;await db.commit()
            return 'partial' if failures else 'ready'
        except asyncio.CancelledError:
            await self.release(work_id,token);raise
        except Exception as error:
            async with self.factory() as db:
                job=await db.scalar(snapshot(JOB,work_id).with_for_update())
                if job and job.lease_token==token:
                    self.fail(job,error);job.lease_token=job.lease_until=None;await db.commit()
            return 'retry'

    @staticmethod
    def fail(job,error):
        job.attempts+=1;job.error_code=getattr(error,'code','identity_conflict' if isinstance(error,catalogue.IdentityConflict) else 'timeout' if isinstance(error,TimeoutError) else 'ingestion_failed')
        delay=max(getattr(error,'retry_after',0) or 0,min(3600,60*2**min(job.attempts-1,6)))
        job.next_attempt_at=now()+timedelta(seconds=min(86400,max(1,delay)))
        job.coverage={**(job.coverage or {}),'state':'partial','complete':False}

    async def release(self,work_id,token):
        async with self.factory() as db:
            job=await db.scalar(snapshot(JOB,work_id).with_for_update())
            if job and job.lease_token==token:
                job.lease_token=job.lease_until=None;job.next_attempt_at=now();await db.commit()

    async def tick(self):
        async with self.factory() as db:
            ids=list(await db.scalars(select(MetadataSnapshot.entity_id).where(MetadataSnapshot.provider=='steam',
                MetadataSnapshot.kind==JOB,MetadataSnapshot.next_attempt_at<=now(),
                or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(2)))
        if ids:await asyncio.gather(*(self.run(work_id) for work_id in ids))


async def scheduler():
    from db import AsyncSessionLocal
    service=GameRatings(AsyncSessionLocal)
    try:
        while True:
            delay=5
            try:await service.tick()
            except asyncio.CancelledError:raise
            except Exception:delay=30
            await asyncio.sleep(delay)
    finally:
        tasks=[task for (scope,_),task in list(_flights.items()) if scope==id(AsyncSessionLocal)]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
