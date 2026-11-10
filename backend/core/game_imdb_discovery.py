"""Discover a requested person's games from cached full IMDb source connections."""
import asyncio
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select,or_,func
from sqlalchemy.dialects.postgresql import insert

from core import catalogue
from core.catalogue_providers import ProviderError
from core.game_company_works import prepare_primary_game
from core.game_imdb_bindings import GameIMDbBindings
from core.game_imdb_discovery_source import GameIMDbDiscoverySource,SIZE
from core.game_imdb_enrichment import now,snapshot,status_view
from core.game_types import CREDIT_CHILDREN
from core.game_mapping_policy import prepare,lifetime,source_title
from models.catalogue import MetadataSnapshot

JOB='game_person_discovery'
MAP='game_person_title_map'
VERSION=16
MAX_ACQUIRE=4
WORKERS=2
_slots=asyncio.Semaphore(WORKERS)


async def acquisition_budget(sessions,processed):
    from core.game_work import eligible_jobs
    # Keep the initial turn small. Later, fill a transport batch only
    # while alone; leased jobs still count so new arrivals get bounded turns.
    if not processed:return MAX_ACQUIRE
    async with sessions() as db:
        contenders=list(await db.scalars(select(MetadataSnapshot.id).where(
            MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==JOB,
            MetadataSnapshot.next_attempt_at<=now(),eligible_jobs()).limit(2)))
    return 10 if len(contenders)==1 else MAX_ACQUIRE


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
    from core.game_work import demand, job_key
    row=await header(db,native)
    stamp=now()
    if row and (row.lease_until and row.lease_until>stamp or row.next_attempt_at):
        await demand(db,job_key('imdb',JOB,native))
        return status(row)
    if row and row.generation==generation and row.coverage.get('matcher_version')==VERSION and row.expires_at and row.expires_at>stamp:
        return status(row)
    await demand(db,job_key('imdb',JOB,native),reset=True)
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
        self._owns_source = source is None

    async def run(self,native):
        async with _slots:
            return await self._run(native)

    async def claim(self,native=None):
        from core.game_work import eligible_jobs, job_order
        async with self.sessions() as db:
            query=select(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==JOB,
                MetadataSnapshot.next_attempt_at<=now(),or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
            if native is not None:query=query.where(MetadataSnapshot.external_id==native)
            else:query=query.where(eligible_jobs())
            query=query.order_by(job_order(),MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(1).with_for_update(skip_locked=True)
            job=await db.scalar(query)
            if not job:return None
            token=uuid.uuid4().hex;job.lease_token=token;job.lease_until=now()+timedelta(minutes=3)
            state=dict(job.payload or {})
            # A cursor is certified only for the policy that processed its prefix.
            if (job.coverage or {}).get('matcher_version')!=VERSION:
                state.update(page=0,offset=0,processed=0,retry_after=0)
            await db.commit()
            return job.external_id,token,state

    async def _run(self,native,*,claimed=None):
        from core.game_work import job_context
        async with job_context(self.sessions,'imdb',JOB,native):
            return await self._execute(native,claimed=claimed)

    async def _execute(self,native,*,claimed=None):
        from core.game_imdb_person import COMPONENT
        from core.game_imdb_review import INDEX,PRIORITY_INDEX
        claimed=claimed or await self.claim(native)
        if not claimed:return 'cached'
        native,token,state=claimed
        try:
            async with self.sessions() as db:
                component=(await db.execute(snapshot(COMPONENT,native).with_only_columns(
                    MetadataSnapshot.payload['generation'].astext.label('generation'),
                    MetadataSnapshot.payload['index_pages'].as_integer().label('pages'),MetadataSnapshot.payload['index_priority'].as_boolean().label('priority'),MetadataSnapshot.coverage))).one_or_none()
                if not component or not component.coverage.get('source_complete'):raise ProviderError('incomplete_cache')
                if component.generation!=state.get('generation'):
                    await self.release(native,token,restart=component.generation);return 'changed'
                page=state.get('page',0);offset=state.get('offset',0)
                records=await db.scalar(snapshot(PRIORITY_INDEX if component.priority else INDEX,f'{native}:{component.generation}:priority:{page}' if component.priority else f'{native}:{component.generation}:{page}').with_only_columns(
                    MetadataSnapshot.payload['titles'])) if page<(component.pages or 0) else []
                if records is None:raise ProviderError('incomplete_cache')
                batch=records[offset:offset+SIZE]
                if not batch and page<(component.pages or 0):raise ProviderError('incomplete_cache')
                ids=[row['id'] for row in batch if any(role!='thanks' for role in row['roles'])]
                from core.game_credit_parents import person_bindings
                bound=person_bindings(ids)
                known=dict((await db.execute(select(bound.c.imdb_id,bound.c.work_id))).all()) if ids else {}
                # Reuse only the scheduling header, never transfer the stored
                # candidate/credit evidence just to decide whether to retry.
                payload=MetadataSnapshot.payload
                fields=('status','matcher_version','source_title','retry_after','retry_attempts')
                retained=list((await db.execute(select(MetadataSnapshot.external_id,
                    func.jsonb_build_object(*[part for key in fields for part in (key,payload[key])]),MetadataSnapshot.expires_at).where(
                    MetadataSnapshot.provider=='wikidata',MetadataSnapshot.kind==MAP,MetadataSnapshot.external_id.in_(ids)))).all()) if ids else []
                retained=[(key,{field:item for field,item in value.items() if item is not None},expiry) for key,value,expiry in retained]
                titles={row['id']:row for row in batch}
                previous={key:value for key,value,expiry in retained if value.get('matcher_version')==VERSION}
                cached={key:value for key,value,expiry in retained if expiry and expiry>now()
                    and value.get('status')!='verified' and value.get('matcher_version')==VERSION
                    and ('source_title' not in value or value['source_title']==source_title(titles[key]))}
            missing=[value for value in ids if value not in known and value not in cached]
            # A sole career can fill one provider batch without delaying another
            # career's worker turn. Contended jobs keep the small quantum.
            candidate_budget=MAX_ACQUIRE
            if isinstance(self.source,GameIMDbDiscoverySource):
                candidate_budget=await acquisition_budget(self.sessions,state.get('processed',0))
            # Legacy sources retain the small acquisition contract. The real
            # source batches identity lookups and applies this budget only to
            # fuzzy matching; unattempted titles never become negative evidence.
            if not isinstance(self.source,GameIMDbDiscoverySource) and len(missing)>MAX_ACQUIRE:
                stop=next(index for index,row in enumerate(batch) if row['id']==missing[MAX_ACQUIRE])
                batch=batch[:stop]
                missing=missing[:MAX_ACQUIRE]
                batch_ids={row['id'] for row in batch}
                cached={key:value for key,value in cached.items() if key in batch_ids}
            published={}
            async def prepare_outcome(outcome):
                if outcome['id'] in published:return dict(published[outcome['id']])
                value=dict(outcome)
                if value['status']=='verified':
                    try:
                        work_id=await self.ingest(value['document'])
                        title=titles[value['id']]
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
                published[outcome['id']]=dict(value)
                return value

            async def progress(rows):
                ready=[row for row in rows if row['status']=='verified' and row['id'] not in published]
                if not ready:return
                async with self.sessions() as db:
                    owner=await db.scalar(snapshot(JOB,native).with_only_columns(MetadataSnapshot.lease_token))
                    generation=await db.scalar(snapshot(COMPONENT,native).with_only_columns(MetadataSnapshot.payload['generation'].astext))
                    if owner!=token or generation!=component.generation:raise ProviderError('stale_mapping')
                for outcome in ready:await prepare_outcome(outcome)
                async with self.sessions() as db:
                    from core.game_imdb_person import queue_projection
                    await queue_projection(db,native);await db.commit()

            try:
                async with asyncio.timeout(55):
                    if missing and isinstance(self.source,GameIMDbDiscoverySource):
                        result=await self.source.page(missing,titles={row['id']:row for row in batch if row['id'] in missing},on_progress=progress,candidate_budget=candidate_budget)
                    else:
                        result=await self.source.page(missing) if missing else {'outcomes':[],'stale':False}
                if result.get('stale'):raise ProviderError('stale_mapping')
            except (ProviderError,TimeoutError) as error:
                # Acquisition failures use the same per-title retry accounting;
                # no stale result may publish new verified participation.
                result={'outcomes':[{'id':native,'status':'unresolved',
                    'reason':getattr(error,'code','timeout'),'retry_after':60} for native in missing],'stale':False}
            supplied=result.get('outcomes')
            deferred=result.get('deferred',[])
            if (not isinstance(deferred,list) or any(not isinstance(value,str) for value in deferred)
                or len(set(deferred))!=len(deferred) or not set(deferred)<=set(missing)):
                raise ProviderError('bad_response')
            if (not isinstance(supplied,list) or len(supplied)!=len(missing)-len(deferred) or any(not isinstance(row,dict) for row in supplied)
                or any(not isinstance(row.get('id'),str) for row in supplied)
                or {row.get('id') for row in supplied}!=set(missing)-set(deferred)
                or any(row.get('status') not in ('verified','excluded','unresolved','unmapped','conflict') for row in supplied)):
                raise ProviderError('bad_response')
            outcomes=[]
            for outcome in supplied:
                value=await prepare_outcome(outcome)
                value.pop('document',None);value['matcher_version']=VERSION
                value=prepare(value,titles[value['id']],previous.get(value['id']))
                value['dependencies']=list(dict.fromkeys([*value.get('dependencies',[]),f'imdb:{value["id"]}',f'wikidata-imdb:{value["id"]}']))
                outcomes.append(value)
            async with self.sessions() as db:
                job=await db.scalar(snapshot(JOB,native).with_for_update())
                if not job or job.lease_token!=token:return 'superseded'
                generation=await db.scalar(snapshot(COMPONENT,native).with_only_columns(MetadataSnapshot.payload['generation'].astext))
                if generation!=component.generation:
                    await db.rollback();await self.release(native,token,restart=generation);return 'changed'
                from core.game_credit_parents import reject_automatic_parents
                await reject_automatic_parents(db,[row['id'] for row in outcomes if row['status'] in ('conflict','excluded')])
                stamp = now()
                deadlines = [expiry for key, value, expiry in retained
                    if key in cached and value.get('retry_after')]
                if retry_at := state.pop('retry_at', None):
                    deadlines.append(datetime.fromisoformat(retry_at))
                # Jobs already in progress may carry a legacy relative delay.
                if legacy_delay := state.pop('retry_after', 0):
                    deadlines.append(stamp + timedelta(seconds=legacy_delay))
                deadlines.extend(stamp + lifetime(outcome) for outcome in outcomes if outcome.get('retry_after'))
                retry_at = max(deadlines, default=None)
                # Different careers retain different source tie orders. Lock
                # shared mapping rows consistently without reordering traversal.
                if outcomes:
                    rows = [dict(provider='wikidata',kind=MAP,external_id=outcome['id'],
                        entity_id=outcome.get('work_id'),payload=outcome,coverage={'verified':outcome['status'] in ('verified','excluded')},
                        fetched_at=stamp,expires_at=stamp+lifetime(outcome))
                        for outcome in sorted(outcomes,key=lambda value:value['id'])]
                    statement=insert(MetadataSnapshot).values(rows)
                    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',set_={
                        'payload':statement.excluded.payload,'coverage':statement.excluded.coverage,
                        'entity_id':statement.excluded.entity_id,'fetched_at':statement.excluded.fetched_at,'expires_at':statement.excluded.expires_at}))
                from core.game_bulk import retire
                settled={key for key,value in cached.items() if value.get('status') in ('excluded','conflict')}|{row['id'] for row in outcomes if row['status'] in ('excluded','conflict')}
                if settled and job.entity_id:
                    for role_kind in ('actor','staff'):await retire(db,job.entity_id,role_kind,settled)
                # Sparse verified siblings may already be published. Keep the
                # durable cursor behind unattempted work, without saving a
                # false negative or spending its transient retry allowance.
                advanced=next((index for index,row in enumerate(batch) if row['id'] in deferred),len(batch))
                offset+=advanced
                if offset>=len(records):page+=1;offset=0
                complete=page>=(component.pages or 0)
                state.update(page=page,offset=offset,processed=state.get('processed',0)+advanced)
                repeat=complete and retry_at is not None
                if retry_at and not repeat:
                    state['retry_at'] = retry_at.isoformat()
                job.coverage={'complete':complete and not repeat,'state':'complete' if complete and not repeat else 'partial','received':state['processed'],'matcher_version':VERSION}
                if repeat:state.update(page=0,offset=0,processed=0)
                job.payload=state
                job.error_code=None;job.attempts=0;job.lease_token=job.lease_until=None
                # Keep each title's cooldown measured from the failed attempt.
                # A long first pass must not restart an already elapsed wait.
                job.next_attempt_at=max(stamp, retry_at) if repeat else None if complete else stamp
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
            projected={**doc,'work':await prepare_primary_game(db,doc['work']),
                'relationships':[],'releases':[],'editions':[]}
            for edge in doc['relationships']:
                if edge['relation'] in CREDIT_CHILDREN and not edge.get('reverse'):
                    projected['relationships'].append({**edge,'target':await prepare_primary_game(db,edge['target'])})
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
        async with _slots:
            claimed=await self.claim()
            if not claimed:return False
            await self._run(claimed[0],claimed=claimed)
            return True

    async def worker(self):
        while True:
            try:
                if await self.tick():
                    await asyncio.sleep(0)
                    continue
            except asyncio.CancelledError:raise
            except Exception:
                await asyncio.sleep(30)
            await asyncio.sleep(2)

    async def serve(self):
        if not self._owns_source:
            await self._serve_workers()
            return
        # Keep provider connections across turns in the long-lived workers.
        http = self.source.sources.api.http
        async with http.open_client() as client:
            http.client = client
            try:
                await self._serve_workers()
            finally:
                http.client = None

    async def _serve_workers(self):
        tasks=[asyncio.create_task(self.worker()) for _ in range(WORKERS)]
        try:await asyncio.gather(*tasks)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            if isinstance(self.source,GameIMDbDiscoverySource):await self.source.close()


async def scheduler():
    from db import AsyncSessionLocal
    await GamePersonDiscovery(AsyncSessionLocal).serve()
