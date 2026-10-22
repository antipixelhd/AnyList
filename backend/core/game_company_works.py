"""On-demand IGDB company portfolios; personal tracking remains separate."""
import asyncio
import hashlib
import uuid
from datetime import datetime,timedelta,timezone
from time import monotonic

from sqlalchemy import or_,select,update

from core import catalogue
from core.catalogue_additive import prepare_values
from core.catalogue_batch import write_credits
from core.catalogue_providers import ProviderError
from core.game_company_source import GameCompanySource
from core.game_imdb_enrichment import now,snapshot as source_snapshot,request as admit,status_view
from models.catalogue import CatalogueEntity,CatalogueIdentity,CatalogueCredit,MetadataSnapshot

JOB='game_company_job'
_flights={}
_slots=asyncio.Semaphore(1)


def snapshot(native):return source_snapshot(JOB,native,provider='igdb')


def primary_game(values):
    """Secondary store IDs never select or merge an IGDB catalogue root."""
    primary=[row for row in values['identities'] if row['namespace']=='igdb.game']
    if len(primary)!=1:raise catalogue.IdentityConflict('One explicit IGDB game root is required')
    candidates=[row for row in values['identities'] if row not in primary]
    return {**values,'identities':primary,'attributes':{**values.get('attributes',{}),
        **({'source_identity_candidates':candidates} if candidates else {})}}


async def company_root(db,native):
    mapping=await catalogue.resolve_identity(db,'igdb.company',str(native))
    entity=await db.get(CatalogueEntity,mapping.entity_id) if mapping else None
    if entity and entity.canonical_company_id:entity=await db.get(CatalogueEntity,entity.canonical_company_id)
    return entity if entity and entity.kind=='organization' else None


async def status(db,entity_id,*,enqueue=False):
    company=await db.get(CatalogueEntity,entity_id)
    if not company or company.kind!='organization':return None
    if company.canonical_company_id:company=await db.get(CatalogueEntity,company.canonical_company_id)
    if not company or company.kind!='organization':return None
    ids=list(await db.scalars(select(CatalogueIdentity.external_id).join(CatalogueEntity,
        CatalogueEntity.id==CatalogueIdentity.entity_id).where(CatalogueIdentity.namespace=='igdb.company',
        or_(CatalogueEntity.id==company.id,CatalogueEntity.canonical_company_id==company.id))))
    if not ids or len(ids)>32:return None
    if enqueue:
        for native in ids:await admit(db,native,kind=JOB,provider='igdb',entity_id=company.id)
    rows=(await db.execute(select(MetadataSnapshot.external_id,
        MetadataSnapshot.coverage['state'].astext.label('coverage'),MetadataSnapshot.coverage['complete'].as_boolean().label('complete'),
        MetadataSnapshot.coverage['received'].as_integer().label('received'),MetadataSnapshot.coverage['total'].as_integer().label('total'),
        MetadataSnapshot.coverage['generation'].astext.label('generation'),MetadataSnapshot.fetched_at,
        MetadataSnapshot.lease_until,MetadataSnapshot.next_attempt_at,MetadataSnapshot.error_code,MetadataSnapshot.expires_at).where(
        MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id.in_(ids)))).all()
    states=[status_view(row) for row in rows]
    complete=len(rows)==len(ids) and all(value['complete'] for value in states)
    pending=not complete and (len(rows)<len(ids) or any(value['pending'] for value in states))
    fetched=max((row.fetched_at for row in rows if row.fetched_at),default=None)
    revision=hashlib.sha256(str(sorted((row.external_id,row.generation) for row in rows)).encode()).hexdigest()
    return {'id':str(company.id),'source':'igdb-company','complete':complete,'pending':pending,
        'coverage':'complete' if complete and all(row.coverage=='complete' for row in rows) else 'partial' if rows else 'unknown',
        'received':sum(row.received or 0 for row in rows),'retry_after':max((value['retry_after'] for value in states),default=3),
        'error':next((value['error'] for value in states if value['error']),None),
        'fetched_at':fetched.replace(tzinfo=timezone.utc).isoformat() if fetched else None,'revision':revision}


class GameCompanyWorks:
    def __init__(self,sessions,*,source=None,page_budget=4):
        self.sessions=sessions;self.source=source or GameCompanySource(sessions);self.page_budget=page_budget

    async def run(self,native):
        key=(id(self.sessions),str(native));task=_flights.get(key)
        if task is None:
            if len(_flights)>=64:return 'busy'
            task=asyncio.create_task(self.bounded(str(native)));_flights[key]=task
            def done(finished):
                if _flights.get(key) is finished:_flights.pop(key,None)
                if not finished.cancelled():finished.exception()
            task.add_done_callback(done)
        return await asyncio.shield(task)

    async def bounded(self,native):
        async with _slots:return await self._run(native)

    async def _run(self,native):
        token=uuid.uuid4().hex
        async with self.sessions() as db:
            job=await db.scalar(snapshot(native).with_for_update())
            if not job or not job.next_attempt_at or job.next_attempt_at>now() or job.lease_until and job.lease_until>now():return 'cached'
            state=dict(job.payload or {})
            job.lease_token=token;job.lease_until=now()+timedelta(minutes=3);await db.commit()
        try:
            recent=False
            try:recent=now()-datetime.fromisoformat(state['started_at'])<timedelta(days=1)
            except (KeyError,TypeError,ValueError):pass
            if not recent or state.get('complete'):
                state={'generation':uuid.uuid4().hex,'started_at':now().isoformat(),'cursor':0,'received':0,'omitted':0,'complete':False}
            deadline=monotonic()+55
            for _ in range(self.page_budget):
                async with asyncio.timeout(max(1,deadline-monotonic())):page=await self.source.page(native,state['cursor'])
                if not await self.publish(native,token,state,page):return 'superseded'
                if state['complete']:return 'ready'
                if monotonic()>=deadline:break
            await self.release(native,token,delay=2);return 'partial'
        except asyncio.CancelledError:
            await self.release(native,token);raise
        except Exception as cause:
            async with self.sessions() as db:
                job=await db.scalar(snapshot(native).with_for_update())
                if job and job.lease_token==token:
                    job.attempts+=1;job.error_code=(cause.code if isinstance(cause,ProviderError) else
                        'identity_conflict' if isinstance(cause,catalogue.IdentityConflict) else 'timeout' if isinstance(cause,TimeoutError) else 'ingestion_failed')
                    delay=getattr(cause,'retry_after',None) or min(3600,60*2**min(job.attempts-1,6))
                    job.next_attempt_at=now()+timedelta(seconds=min(3600,max(1,delay)));job.lease_token=job.lease_until=None
                    await db.commit()
            return 'retry'

    async def publish(self,native,token,state,page):
        async with self.sessions() as db:
            await catalogue.write_lock(db)
            job=await db.scalar(snapshot(native).with_for_update())
            if not job or job.lease_token!=token:return False
            company=await company_root(db,native)
            if not company:raise catalogue.IdentityConflict('Company identity changed')
            credits=[];known={};omitted=page['omitted']
            for item in page['documents']:
                doc=item['doc'];native_game=doc['work']['identities'][0]['external_id']
                if native_game not in known:
                    try:
                        async with db.begin_nested():
                            values=await prepare_values(db,primary_game(doc['work']),'igdb')
                            projected={**doc,'work':values,'relationships':[],'releases':[],'editions':[]}
                            for edge in doc['relationships']:
                                if edge['relation']=='expansion' and not edge.get('reverse'):
                                    projected['relationships'].append({**edge,'target':await prepare_values(db,primary_game(edge['target']),'igdb')})
                            work=await catalogue.ingest_document(db,projected,'igdb')
                            for candidate in doc['work']['identities'][1:]:
                                existing=await catalogue.resolve_identity(db,candidate['namespace'],candidate['external_id'])
                                if not existing or existing.entity_id==work.id:
                                    await catalogue.bind_identity(db,work.id,candidate['namespace'],candidate['external_id'],'igdb',
                                        {'cross_reference':doc['work']['identities'][0]})
                            known[native_game]=work.id
                    except catalogue.IdentityConflict:
                        omitted+=1;known[native_game]=None
                work_id=known[native_game]
                if not work_id:continue
                record=item['record']
                for role in item['roles']:
                    credits.append({'work_id':work_id,'contributor_id':company.id,'provider':'igdb',
                        'source_key':f"company:{native}:{record['id']}:{role}",'scope':'title','role':role,
                        'attributes':{'source_active':True,'company_connection':native,'company_generation':state['generation'],
                            'source_game_id':str(record['game']),'source_record':record,'source_fetched_at':now().isoformat()}})
            await write_credits(db,credits)
            state.update(cursor=page['cursor'],received=state['received']+page['received'],
                omitted=state['omitted']+omitted,complete=page['complete'])
            if state['complete']:
                if not state['omitted']:
                    await db.execute(update(CatalogueCredit).where(CatalogueCredit.provider=='igdb',
                        CatalogueCredit.attributes['company_connection'].astext==native,
                        CatalogueCredit.attributes['company_generation'].astext!=state['generation']).values(
                            attributes=CatalogueCredit.attributes.op('||')({'source_active':False})))
                job.next_attempt_at=job.lease_token=job.lease_until=None;job.expires_at=now()+timedelta(days=1 if state['omitted'] else 7)
            else:job.lease_until=now()+timedelta(minutes=3)
            job.entity_id=company.id;job.payload=dict(state);job.error_code=None;job.attempts=0
            job.coverage={'state':'partial' if state['omitted'] else 'complete' if state['complete'] else 'partial',
                'complete':state['complete'],'received':state['received'],'total':state['received'] if state['complete'] else None,
                'generation':state['generation']}
            job.fetched_at=now();await db.commit();return True

    async def release(self,native,token,delay=0):
        async with self.sessions() as db:
            await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind==JOB,
                MetadataSnapshot.external_id==native,MetadataSnapshot.lease_token==token).values(
                    lease_token=None,lease_until=None,next_attempt_at=now()+timedelta(seconds=delay)))
            await db.commit()

    async def tick(self):
        async with self.sessions() as db:
            native=await db.scalar(select(MetadataSnapshot.external_id).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind==JOB,
                MetadataSnapshot.next_attempt_at<=now(),or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(1))
        if native:await self.run(native)


async def scheduler():
    from db import AsyncSessionLocal
    service=GameCompanyWorks(AsyncSessionLocal)
    try:
        while True:
            delay=2
            try:await service.tick()
            except asyncio.CancelledError:raise
            except Exception:delay=30
            await asyncio.sleep(delay)
    finally:
        tasks=[task for (scope,_),task in list(_flights.items()) if scope==id(AsyncSessionLocal)]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
