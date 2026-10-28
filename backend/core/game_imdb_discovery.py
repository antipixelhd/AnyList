"""Discover a requested person's games from cached full IMDb source connections."""
import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select,or_
from sqlalchemy.dialects.postgresql import insert

from core import catalogue
from core.catalogue_additive import prepare_values
from core.catalogue_providers import ProviderError
from core.game_company_works import primary_game
from core.game_imdb_bindings import GameIMDbBindings,verified_bindings
from core.game_imdb_discovery_source import GameIMDbDiscoverySource,SIZE
from core.game_imdb_enrichment import now,snapshot,status_view
from core.game_types import CREDIT_CHILDREN
from models.catalogue import CatalogueIdentity,MetadataSnapshot

JOB='game_person_discovery'
MAP='game_person_title_map'
VERSION=13
MAX_ACQUIRE=4
_slots=asyncio.Semaphore(1)


def status(row):
    return status_view(SimpleNamespace(complete=(row.coverage or {}).get('complete'),
        coverage=(row.coverage or {}).get('state','unknown'),received=(row.coverage or {}).get('received',0),
        total=None,lease_until=row.lease_until,next_attempt_at=row.next_attempt_at,error_code=row.error_code))


async def header(db,native):
    return (await db.execute(snapshot(JOB,native).with_only_columns(
        MetadataSnapshot.payload['generation'].astext.label('generation'),MetadataSnapshot.coverage,
        MetadataSnapshot.lease_until,MetadataSnapshot.next_attempt_at,MetadataSnapshot.error_code,
        MetadataSnapshot.expires_at))).one_or_none()


async def request(db,native,generation,entity_id):
    row=await header(db,native)
    stamp=now()
    if row and (row.lease_until and row.lease_until>stamp or row.next_attempt_at):
        return status(row)
    if row and row.generation==generation and row.coverage.get('matcher_version')==VERSION and row.expires_at and row.expires_at>stamp:
        return status(row)
    statement=insert(MetadataSnapshot).values(provider='imdb',kind=JOB,external_id=native,entity_id=entity_id,
        payload={'generation':generation,'page':0,'offset':0,'processed':0},coverage={'complete':False},next_attempt_at=stamp)
    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',set_={
        'payload':statement.excluded.payload,'coverage':statement.excluded.coverage,'next_attempt_at':stamp,
        'error_code':None,'attempts':0},where=or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=stamp)))
    return {'state':'pending','pending':True,'complete':False,'retry_after':2,'error':None}


class GamePersonDiscovery:
    def __init__(self,sessions,*,source=None,bindings=None):
        self.sessions=sessions;self.source=source or GameIMDbDiscoverySource(sessions)
        self.bindings=bindings or GameIMDbBindings(sessions)

    async def run(self,native):
        async with _slots:
            return await self._run(native)

    async def _run(self,native):
        from core.game_imdb_person import COMPONENT
        from core.game_imdb_review import INDEX
        async with self.sessions() as db:
            job=await db.scalar(snapshot(JOB,native).with_for_update())
            if not job or not job.next_attempt_at or job.next_attempt_at>now() or job.lease_until and job.lease_until>now():return 'cached'
            token=uuid.uuid4().hex;job.lease_token=token;job.lease_until=now()+timedelta(minutes=3)
            state=dict(job.payload or {})
            await db.commit()
        try:
            async with self.sessions() as db:
                component=(await db.execute(snapshot(COMPONENT,native).with_only_columns(
                    MetadataSnapshot.payload['generation'].astext.label('generation'),
                    MetadataSnapshot.payload['index_pages'].as_integer().label('pages'),MetadataSnapshot.coverage))).one_or_none()
                if not component or not component.coverage.get('source_complete'):raise ProviderError('incomplete_cache')
                if component.generation!=state.get('generation'):
                    await self.release(native,token,restart=component.generation);return 'changed'
                page=state.get('page',0);offset=state.get('offset',0)
                records=await db.scalar(snapshot(INDEX,f'{native}:{component.generation}:{page}').with_only_columns(
                    MetadataSnapshot.payload['titles'])) if page<(component.pages or 0) else []
                if records is None:raise ProviderError('incomplete_cache')
                batch=records[offset:offset+SIZE]
                if not batch and page<(component.pages or 0):raise ProviderError('incomplete_cache')
                ids=[row['id'] for row in batch if any(role!='thanks' for role in row['roles'])]
                from core.game_credit_parents import person_bindings
                bound=person_bindings(ids)
                known=dict((await db.execute(select(bound.c.imdb_id,bound.c.work_id))).all()) if ids else {}
                retained=list((await db.execute(select(MetadataSnapshot.external_id,MetadataSnapshot.payload).where(
                    MetadataSnapshot.provider=='wikidata',MetadataSnapshot.kind==MAP,MetadataSnapshot.external_id.in_(ids),
                    MetadataSnapshot.expires_at>now()))).all()) if ids else []
                cached={key:value for key,value in retained if value.get('status')!='verified' and value.get('matcher_version')==VERSION}
            missing=[value for value in ids if value not in known and value not in cached]
            # One title can require many alias queries. Publish small fresh
            # batches within the shared deadline while advancing cached rows
            # at the normal page size; never truncate the saved connection.
            if len(missing)>MAX_ACQUIRE:
                stop=next(index for index,row in enumerate(batch) if row['id']==missing[MAX_ACQUIRE])
                batch=batch[:stop]
                missing=missing[:MAX_ACQUIRE]
                batch_ids={row['id'] for row in batch}
                cached={key:value for key,value in cached.items() if key in batch_ids}
            async with asyncio.timeout(55):
                if missing and isinstance(self.source,GameIMDbDiscoverySource):
                    result=await self.source.page(missing,titles={row['id']:row for row in batch if row['id'] in missing})
                else:
                    result=await self.source.page(missing) if missing else {'outcomes':[],'stale':False}
            if result.get('stale'):
                raise ProviderError('stale_mapping')  # Existing catalogue content remains available.
            supplied=result.get('outcomes')
            if (not isinstance(supplied,list) or len(supplied)!=len(missing) or any(not isinstance(row,dict) for row in supplied)
                or any(not isinstance(row.get('id'),str) for row in supplied)
                or {row.get('id') for row in supplied}!=set(missing)
                or any(row.get('status') not in ('verified','excluded','unresolved','unmapped','conflict') for row in supplied)):
                raise ProviderError('bad_response')
            outcomes=[]
            retry_after=max([state.get('retry_after',0),*[value.get('retry_after',0) for value in cached.values()]],default=0)
            for outcome in supplied:
                value=dict(outcome)
                if value['status']=='verified':
                    try:
                        work_id=await self.ingest(value['document'])
                        title=next(row for row in batch if row['id']==value['id'])
                        matched=value.get('binding_source')=='matched'
                        if value.get('credit_parent'):
                            from core.game_credit_parents import save_automatic_parent
                            async with self.sessions() as db:
                                await save_automatic_parent(db,value['id'],work_id,value['evidence']);await db.commit()
                        else:await self.bindings.save(work_id,value['id'],'matched' if matched else 'wikidata',{
                            'title_type':'videoGame','igdb_id':str(value['igdb_id']),
                            'matching' if matched else 'wikidata':value['evidence'],
                            'dependencies':value['evidence'].get('dependencies',[]),'mapping_state':'active',
                            'person_connection':{'id':native,'generation':component.generation},
                            'imdb_identity':{'id':value['id'],'titleType':{'id':'videoGame'},
                                'titleText':{'text':title['title']},'releaseYear':{'year':title['year']}}},
                            expected_igdb=str(value['igdb_id']),participation=True)
                        value['work_id']=work_id
                    except catalogue.IdentityConflict:
                        value={'id':value['id'],'status':'conflict'}
                value.pop('document',None);value['matcher_version']=VERSION;outcomes.append(value)
                retry_after=max(retry_after,value.get('retry_after',0))
            async with self.sessions() as db:
                job=await db.scalar(snapshot(JOB,native).with_for_update())
                if not job or job.lease_token!=token:return 'superseded'
                generation=await db.scalar(snapshot(COMPONENT,native).with_only_columns(MetadataSnapshot.payload['generation'].astext))
                if generation!=component.generation:
                    await db.rollback();await self.release(native,token,restart=generation);return 'changed'
                from core.game_credit_parents import reject_automatic_parents
                await reject_automatic_parents(db,[row['id'] for row in outcomes if row['status'] in ('conflict','excluded')])
                for outcome in outcomes:
                    statement=insert(MetadataSnapshot).values(provider='wikidata',kind=MAP,external_id=outcome['id'],
                        entity_id=outcome.get('work_id'),payload=outcome,coverage={'verified':outcome['status'] in ('verified','excluded')},
                        fetched_at=now(),expires_at=now()+(timedelta(seconds=outcome['retry_after']) if outcome.get('retry_after')
                            else timedelta(days=7 if outcome['status'] in ('verified','excluded') else 1)))
                    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',set_={
                        'payload':statement.excluded.payload,'coverage':statement.excluded.coverage,
                        'entity_id':statement.excluded.entity_id,'fetched_at':statement.excluded.fetched_at,'expires_at':statement.excluded.expires_at}))
                offset+=len(batch)
                if offset>=len(records):page+=1;offset=0
                complete=page>=(component.pages or 0)
                state.update(page=page,offset=offset,processed=state.get('processed',0)+len(batch),retry_after=retry_after)
                repeat=complete and retry_after>0
                job.coverage={'complete':complete and not repeat,'state':'complete' if complete and not repeat else 'partial','received':state['processed'],'matcher_version':VERSION}
                if repeat:state.update(page=0,offset=0,processed=0,retry_after=0)
                job.payload=state
                job.error_code=None;job.attempts=0;job.lease_token=job.lease_until=None
                job.next_attempt_at=now()+timedelta(seconds=retry_after) if repeat else None if complete else now()+timedelta(seconds=2)
                job.expires_at=now()+timedelta(days=1) if complete and not repeat else None
                # Publish each verified batch. A later provider failure must
                # not hold already discovered participation behind completion.
                from core.game_imdb_person import queue_projection
                await queue_projection(db,native)
                await db.commit()
            return 'ready' if complete and not repeat else 'partial'
        except asyncio.CancelledError:
            await self.release(native,token);raise
        except Exception as error:
            await self.release(native,token,error=getattr(error,'code','identity_conflict' if isinstance(error,catalogue.IdentityConflict)
                else 'timeout' if isinstance(error,TimeoutError) else 'ingestion_failed'),
                retry_after=getattr(error,'retry_after',None))
            return 'retry'

    async def ingest(self,doc):
        async with self.sessions() as db:
            await catalogue.write_lock(db)
            projected={**doc,'work':await prepare_values(db,primary_game(doc['work']),'igdb'),
                'relationships':[],'releases':[],'editions':[]}
            for edge in doc['relationships']:
                if edge['relation'] in CREDIT_CHILDREN and not edge.get('reverse'):
                    projected['relationships'].append({**edge,'target':await prepare_values(db,primary_game(edge['target']),'igdb')})
            work=await catalogue.ingest_document(db,projected,'igdb');await db.commit()
            return work.id

    async def release(self,native,token,*,error=None,restart=None,retry_after=None):
        async with self.sessions() as db:
            row=await db.scalar(snapshot(JOB,native).with_for_update())
            if not row or row.lease_token!=token:return
            row.lease_token=row.lease_until=None
            if error:row.attempts+=1;row.error_code=error
            if restart:
                row.payload={'generation':restart,'page':0,'offset':0,'processed':0};row.coverage={'complete':False}
            delay=retry_after if isinstance(retry_after,(int,float)) and 0<retry_after<=3600 else 60*2**min(row.attempts-1,6)
            row.next_attempt_at=now()+timedelta(seconds=min(3600,delay)) if error else now()
            await db.commit()

    async def tick(self):
        async with self.sessions() as db:
            native=await db.scalar(select(MetadataSnapshot.external_id).where(MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==JOB,
                MetadataSnapshot.next_attempt_at<=now(),or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(1))
        if native:await self.run(native)


async def scheduler():
    from db import AsyncSessionLocal
    service=GamePersonDiscovery(AsyncSessionLocal)
    while True:
        try:await service.tick()
        except asyncio.CancelledError:raise
        except Exception:await asyncio.sleep(30)
        await asyncio.sleep(2)
