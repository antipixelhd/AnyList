"""Demand-driven game credits with durable page progress and shared leases."""
import asyncio
import uuid
import logging
from weakref import WeakKeyDictionary
from datetime import datetime, timedelta, timezone
from time import monotonic

from sqlalchemy import Integer, cast, delete, event, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from core import catalogue
from core.catalogue_providers import ProviderError
from core.enrichment_retry import (CONTENT_FAILURES, content_recheck, schedule_failure,
    requested_coverage, pause_unused, retain_demand, release_context)
from core.game_imdb_bindings import GameIMDbBindings, context
from core.game_imdb_projection import credit_document
from models.catalogue import CatalogueCredit, CharacterPerformance, MetadataSnapshot

JOB='game_credit_job'
COMPONENT='game_credits'
PAGE='game_credit_page'
_flights={}
_slots=asyncio.Semaphore(2)
_wakeups=WeakKeyDictionary()
log=logging.getLogger(__name__)


def _wake_committed(_session):
    wake=_wakeups.get(asyncio.get_running_loop())
    if wake is not None:wake.set()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def snapshot(kind,key,*,provider='imdb'):
    return select(MetadataSnapshot).where(MetadataSnapshot.provider==provider,
        MetadataSnapshot.kind==kind,MetadataSnapshot.external_id==str(key))


async def request(db,work_id,*,kind=JOB,entity_id=None,provider='imdb'):
    """Cheap admission only; provider work stays off the request path."""
    stamp=now()
    row=await header(db,work_id,kind=kind,provider=provider)
    view=status_view(row)
    from core.game_work import demand, job_key
    if not row or view['pending'] or not row.expires_at or row.expires_at <= stamp:
        await demand(db, job_key(provider, kind, work_id),reset=not row or not view['pending'])
    if kind==JOB and provider=='imdb' and row and row.coverage=='unmapped' and not view['pending']:
        # A later automatic/person discovery may have supplied the missing
        # identity since this negative job was cached. Recover existing jobs
        # without querying providers or disturbing active/usable collections.
        from core.game_imdb_bindings import verified_bindings
        bindings=verified_bindings().subquery()
        if await db.scalar(select(bindings.c.work_id).where(bindings.c.work_id==work_id)):
            await retry_unmapped(db,work_id)
            return await status(db,work_id)
    if row and (row.next_attempt_at or row.lease_until and row.lease_until>stamp
                or row.expires_at and row.expires_at>stamp):
        if row.next_attempt_at and (not row.requested_at or row.requested_at< (stamp-timedelta(days=1)).isoformat()):
            await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider==provider,
                MetadataSnapshot.kind==kind,MetadataSnapshot.external_id==str(work_id)).values(
                    coverage=MetadataSnapshot.coverage.op('||')(requested_coverage(stamp))))
        return view
    statement=insert(MetadataSnapshot).values(provider=provider,kind=kind,external_id=str(work_id),
        entity_id=entity_id if entity_id is not None else work_id,next_attempt_at=stamp,coverage=requested_coverage(stamp))
    await db.execute(statement.on_conflict_do_update(
        constraint='uq_catalogue_snapshot',set_={'next_attempt_at':stamp,
            'coverage':MetadataSnapshot.coverage.op('||')(requested_coverage(stamp))},where=(
            MetadataSnapshot.next_attempt_at.is_(None)
            & or_(MetadataSnapshot.expires_at.is_(None),MetadataSnapshot.expires_at<=stamp)
            & or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=stamp))))
    # Wake only after publication: a scan before commit can miss the new job
    # and otherwise put the idle worker straight back into its two-second sleep.
    if kind==JOB and provider=='imdb':
        event.listen(db.sync_session,'after_commit',_wake_committed,once=True)
    return {**view,'state':'pending','pending':True,'complete':False,'error':None,'retry_after':2}


async def retry_unmapped(db,work_id):
    await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',
        MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id==str(work_id),
        or_(MetadataSnapshot.coverage['state'].astext.in_(('unmapped','unresolved')),
            MetadataSnapshot.coverage['retry_mode'].astext=='on_demand'),MetadataSnapshot.next_attempt_at.is_(None),
        or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
        .values(payload={},coverage={},expires_at=None,next_attempt_at=now(),error_code=None,attempts=0,
            lease_token=None,lease_until=None)
        .execution_options(synchronize_session=False))


async def header(db,work_id,*,kind=JOB,provider='imdb'):
    return (await db.execute(snapshot(kind,work_id,provider=provider).with_only_columns(
        MetadataSnapshot.coverage['state'].astext.label('coverage'),
        MetadataSnapshot.coverage['complete'].as_boolean().label('complete'),
        MetadataSnapshot.coverage['received'].as_integer().label('received'),
        MetadataSnapshot.coverage['total'].as_integer().label('total'),
        MetadataSnapshot.coverage['requested_at'].astext.label('requested_at'),
        MetadataSnapshot.lease_until,MetadataSnapshot.next_attempt_at,MetadataSnapshot.error_code,
        MetadataSnapshot.expires_at))).one_or_none()


def status_view(row):
    if not row:return {'state':'pending','pending':True,'complete':False,'retry_after':2,
        'coverage':'unknown','received':0,'total':None,'error':None}
    active=bool(row.lease_until and row.lease_until>now())
    pending=active or bool(row.next_attempt_at)
    delay=max(1,int((row.next_attempt_at-now()).total_seconds())+1) if row.next_attempt_at else 2
    state='loading' if active else 'retry' if row.error_code and pending else 'pending' if pending else 'ready'
    return {'state':state,'pending':pending,'complete':bool(row.complete) and not pending,
        'coverage':row.coverage or 'unknown','received':row.received or 0,'total':row.total,
        'error':row.error_code,'retry_after':min(3600,delay)}


async def status(db,work_id,*,kind=JOB,provider='imdb'):
    return status_view(await header(db,work_id,kind=kind,provider=provider))


class GameIMDbEnrichment:
    def __init__(self,sessions,*,bindings=None,page_budget=12):
        self.sessions=sessions
        self.bindings=bindings or GameIMDbBindings(sessions)
        self.source=self.bindings.source
        self.page_budget=page_budget

    async def page_size(self,work_id,state):
        # Origin priority is not enough: a click can promote an already-running
        # mapper job, and that foreground demand can expire between turns.
        from core.game_work import Priority,job_key
        from models.game_runner import GameWorkDemand
        if state.get('page_size_fallback'):return 100
        async with self.sessions() as db:
            demand=(await db.execute(select(GameWorkDemand.priority,GameWorkDemand.active_until).where(
                GameWorkDemand.key==job_key('imdb',JOB,work_id)))).one_or_none()
            foreground=bool(demand and demand.priority==Priority.DIRECT and demand.active_until>now())
            if not foreground:return 100
            if state.get('cursor') or state.get('staging'):return 250
            previous=(await db.execute(snapshot(COMPONENT,state['title_id']).with_only_columns(
                MetadataSnapshot.entity_id,MetadataSnapshot.payload['complete'].as_boolean()))).one_or_none()
            # A sizing hint only. Publication still rechecks staging under the
            # write lock so a concurrent complete projection stays protected.
            return 250 if previous and previous[0]==work_id and previous[1] else 100

    async def run(self,work_id):
        key=(id(self.sessions),work_id)
        task=_flights.get(key)
        if task is None:
            if len(_flights)>=64:return 'busy'
            task=asyncio.create_task(self._bounded(work_id))
            _flights[key]=task
            def done(finished):
                if _flights.get(key) is finished:_flights.pop(key,None)
                if not finished.cancelled():finished.exception()
            task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _bounded(self,work_id):
        async with _slots:
            return await self._run(work_id)

    async def claim(self,work_id):
        async with self.sessions() as db:
            job=await db.scalar(snapshot(JOB,work_id).with_for_update())
            if not job or not job.next_attempt_at or job.next_attempt_at>now() or job.lease_until and job.lease_until>now():
                return None
            if pause_unused(job,now()):
                await db.commit();return None
            token=uuid.uuid4().hex
            job.lease_token=token
            job.lease_until=now()+timedelta(minutes=3)
            state=dict(job.payload or {})
            state['_mapping_request']=(job.coverage or {}).get('mapping_revalidation')
            await db.commit()
            return token,state

    async def _run(self,work_id):
        from core.game_work import job_context
        async with job_context(self.sessions, 'imdb', JOB, work_id):
            return await self._execute(work_id)

    async def _execute(self,work_id):
        claimed=await self.claim(work_id)
        if not claimed:return 'cached'
        token,state=claimed
        expected_request=state.get('_mapping_request')
        try:
            binding=await self.bindings.resolve(work_id)
            if binding['status']!='bound':
                await self.finish_unmatched(work_id,token,binding['status'],expected_request=expected_request)
                return binding['status']
            title_id=binding['binding']['id']
            binding_revision=(binding['binding'].get('evidence') or {}).get('evidence_revision')
            started=state.get('started_at')
            recent=False
            if isinstance(started,str):
                try:recent=now()-datetime.fromisoformat(started)<timedelta(days=1)
                except ValueError:pass
            if not recent or state.get('title_id')!=title_id or 'cursor' not in state or state.get('binding_revision')!=binding_revision:
                state={'state':'partial','title_id':title_id,'generation':uuid.uuid4().hex,
                    'started_at':now().isoformat(),'cursor':None,'seen_cursors':[],
                    'received':0,'total':None,'pages':0,'occurrences':{},'omitted':0,'complete':False}
            state.update(binding_revision=binding_revision,_mapping_request=expected_request)
            deadline=monotonic()+55
            for _ in range(self.page_budget):
                async with asyncio.timeout(max(1,deadline-monotonic())):
                    size=await self.page_size(work_id,state)
                    page=await self.source.credits(title_id,state['cursor'],**({'page_size':size} if size!=100 else {}))
                    if page.get('page_size_fallback'):state['page_size_fallback']=True
                if state['total'] is not None and page['total']!=state['total']:
                    # A changing connection cannot be certified from mixed pages.
                    await self.fail(work_id,token,'changed_connection',restart=True)
                    return 'partial'
                if page['has_next'] and page['cursor'] in state['seen_cursors']:
                    raise ProviderError('bad_response')
                received=state['received']+len(page['rows'])
                if received>page['total'] or not page['has_next'] and received!=page['total']:
                    raise ProviderError('bad_response')
                if not await self.publish(work_id,token,state,page):return 'superseded'
                if not page['has_next']:return 'ready' if state['complete'] else 'partial'
                if monotonic()>=deadline:break
            await self.continue_later(work_id,token,state)
            return 'partial'
        except (ProviderError,catalogue.IdentityConflict,TimeoutError) as error:
            code=error.code if isinstance(error,ProviderError) else 'identity_conflict' if isinstance(error,catalogue.IdentityConflict) else 'timeout'
            await self.fail(work_id,token,code,retry_after=getattr(error,'retry_after',None))
            return 'retry'
        except asyncio.CancelledError:
            # Page publication is atomic. Another worker can resume the durable
            # cursor after this lease expires; shutdown is not negative evidence.
            await self.release(work_id,token)
            raise
        except Exception:
            await self.fail(work_id,token,'ingestion_failed')
            return 'retry'

    async def publish(self,work_id,token,state,page):
        async with self.sessions() as db:
            await catalogue.write_lock(db)
            job=await db.scalar(snapshot(JOB,work_id).with_for_update())
            if not job or job.lease_token!=token:return False
            work,native,binding,_=await context(db,work_id,participation=True)
            from core.game_title_match import SUPPORTED_VERSIONS
            evidence=(binding.evidence or {}) if binding else {}
            if ((job.coverage or {}).get('mapping_revalidation')!=state.get('_mapping_request')
                or evidence.get('evidence_revision')!=state.get('binding_revision')
                or evidence.get('mapping_state','active')!='active'
                or binding and binding.source=='matched' and evidence.get('matcher_version') not in SUPPORTED_VERSIONS):
                job.lease_token=job.lease_until=None
                job.error_code='mapping_changed'
                job.next_attempt_at=now();job.expires_at=None
                await db.commit()
                return False
            if not binding or binding.external_id!=state['title_id']:
                job.lease_token=job.lease_until=None
                job.error_code='identity_conflict'
                job.next_attempt_at=None
                await db.commit()
                return False
            doc,occurrences=await asyncio.to_thread(credit_document,native,state['title_id'],page['rows'],
                generation=state['generation'],offset=state['received'],occurrences=state['occurrences'],
                page_number=state['pages'])
            # Imported below to keep provider transport independent of identity
            # reconciliation and to avoid any per-credit provider requests.
            from core.game_imdb_people import bridge_people
            if 'staging' not in state:
                previous=await db.scalar(snapshot(COMPONENT,state['title_id']))
                state['staging']=bool(previous and previous.entity_id==work_id and (previous.payload or {}).get('complete'))
            if not state['staging']:
                await bridge_people(db,doc['credits'])
                await catalogue.ingest_document(db,doc,'imdb',project_screen_roles=False)
            raw_key=f"{state['title_id']}:{state['generation']}:{state['pages']}"
            await db.execute(insert(MetadataSnapshot).values(provider='imdb',kind=PAGE,external_id=raw_key,
                entity_id=work_id,payload=page,coverage={'page':state['pages']},fetched_at=now()).on_conflict_do_nothing())
            state.update(header=page['header'],cursor=page['cursor'] if page['has_next'] else None,
                received=state['received']+len(page['rows']),total=page['total'],pages=state['pages']+1,
                occurrences=occurrences,omitted=state['omitted']+doc['coverage']['omitted_rows'])
            if page['has_next']:state['seen_cursors']=[*state['seen_cursors'],page['cursor']]
            if page['has_next']:job.lease_until=now()+timedelta(minutes=3)
            state['complete']=not page['has_next'] and state['omitted']==0
            if not page['has_next']:
                if state['complete']:
                    if state['staging']:
                        # Retain the previous complete projection until every
                        # new page is valid. A partial refresh never mixes into
                        # or empties an already usable cast/crew collection.
                        pages=list(await db.scalars(select(MetadataSnapshot.payload).where(
                            MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==PAGE,
                            MetadataSnapshot.entity_id==work_id,MetadataSnapshot.external_id.like(
                                f"{state['title_id']}:{state['generation']}:%"))
                            .order_by(cast(MetadataSnapshot.coverage['page'].astext,Integer))))
                        doc,_=await asyncio.to_thread(credit_document,native,state['title_id'],
                            [row for stored in pages for row in stored['rows']],generation=state['generation'])
                        await bridge_people(db,doc['credits'])
                        await catalogue.ingest_document(db,doc,'imdb',project_screen_roles=False)
                    await db.execute(update(CatalogueCredit).where(CatalogueCredit.work_id==work_id,
                        CatalogueCredit.provider=='imdb',func.coalesce(
                            CatalogueCredit.attributes['imdb_generation'].astext,'')!=state['generation'],
                        CatalogueCredit.scope!='person',
                        ~select(CharacterPerformance.id).where(CharacterPerformance.credit_id==CatalogueCredit.id,
                            CharacterPerformance.source=='reviewed').exists())
                        .values(attributes=CatalogueCredit.attributes.op('||')({'source_active':False}))
                        .execution_options(synchronize_session=False))
                    await db.execute(insert(MetadataSnapshot).values(provider='imdb',kind=COMPONENT,
                        external_id=state['title_id']).on_conflict_do_nothing())
                    component=await db.scalar(snapshot(COMPONENT,state['title_id']).with_for_update())
                    component.entity_id=work_id
                    component.payload={key:state[key] for key in ('title_id','generation','header','received','total','pages','complete')}
                    component.coverage={'source_complete':True,'projection_complete':True,'source':'imdb',
                        'scope':'retrieved_connection','credits_complete':False}
                    component.fetched_at=now()
                    component.expires_at=now()+timedelta(days=30)
                    await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',
                        MetadataSnapshot.kind==PAGE,MetadataSnapshot.entity_id==work_id,
                        ~MetadataSnapshot.external_id.like(f"{state['title_id']}:{state['generation']}:%")))
                state['state']='complete' if state['complete'] else 'partial'
                # Closed connections have no continuation cursor. Their complete
                # source pages remain cached; omitted identities stay partial.
                state.pop('cursor',None)
                state.pop('occurrences',None)
                state.pop('seen_cursors',None)
                job.lease_token=job.lease_until=None
                job.next_attempt_at=None
                release=(work.attributes or {}).get('release_date') or ''
                established=bool(release and release[:4].isdigit() and int(release[:4])<now().year)
                job.expires_at=now()+timedelta(days=(30 if established else 7) if state['complete'] else 1)
            job.payload=dict(state)
            job.coverage=retain_demand(job,{**{key:state.get(key) for key in ('state','complete','received','total')},
                'mapping_revalidation':state.get('_mapping_request')})
            job.fetched_at=now()
            job.error_code=None
            job.attempts=0
            await db.commit()
            from core.game_credit_updates import published
            published(db.bind,work_id)
            return True

    async def continue_later(self,work_id,token,state):
        async with self.sessions() as db:
            job=await db.scalar(snapshot(JOB,work_id).with_for_update())
            if job and job.lease_token==token:
                if (job.coverage or {}).get('mapping_revalidation')!=state.get('_mapping_request'):
                    job.lease_token=job.lease_until=None;job.next_attempt_at=now();job.expires_at=None
                    await db.commit();return
                job.payload=dict(state)
                job.coverage=retain_demand(job,{**{key:state.get(key) for key in ('state','complete','received','total')},
                    'mapping_revalidation':state.get('_mapping_request')})
                job.lease_token=job.lease_until=None
                job.next_attempt_at=now()
                await db.commit()

    async def finish_unmatched(self,work_id,token,state,*,expected_request=None):
        async with self.sessions() as db:
            job=await db.scalar(snapshot(JOB,work_id).with_for_update())
            if job and job.lease_token==token:
                marker=(job.coverage or {}).get('mapping_revalidation')
                if marker!=expected_request:
                    job.lease_token=job.lease_until=None
                    job.next_attempt_at=now()
                    job.expires_at=None
                    await db.commit();return
                job.payload={'state':state,'complete':False}
                job.coverage=retain_demand(job,{'state':state,'complete':False,'retry_mode':'on_demand'})
                job.lease_token=job.lease_until=job.next_attempt_at=None
                job.expires_at=content_recheck(await release_context(db,work_id),now())
                job.error_code=None;job.attempts=0
                await db.commit()

    async def fail(self,work_id,token,code,*,restart=False,retry_after=None):
        async with self.sessions() as db:
            job=await db.scalar(snapshot(JOB,work_id).with_for_update())
            if job and job.lease_token==token:
                if restart:
                    job.payload={'state':'partial','complete':False}
                    job.coverage={**(job.coverage or {}),'state':'partial','complete':False}
                job.lease_token=job.lease_until=None
                release=await release_context(db,work_id) if code in CONTENT_FAILURES else None
                schedule_failure(job,code,now(),work=release,retry_after=retry_after)
                await db.commit()

    async def release(self,work_id,token):
        async with self.sessions() as db:
            await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',
                MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id==str(work_id),MetadataSnapshot.lease_token==token)
                .values(lease_token=None,lease_until=None,next_attempt_at=now()).execution_options(synchronize_session=False))
            await db.commit()

    async def tick(self):
        from core.game_work import eligible_jobs, job_order
        async with self.sessions() as db:
            ids=list(await db.scalars(select(MetadataSnapshot.entity_id).where(MetadataSnapshot.provider=='imdb',
                MetadataSnapshot.kind==JOB,MetadataSnapshot.next_attempt_at<=now(),eligible_jobs(),
                or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                .order_by(job_order(),MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(2)))
        if ids:await asyncio.gather(*(self.run(work_id) for work_id in ids))
        return bool(ids)


async def scheduler():
    from db import AsyncSessionLocal
    service=GameIMDbEnrichment(AsyncSessionLocal,page_budget=1)
    loop=asyncio.get_running_loop();wake=asyncio.Event();_wakeups[loop]=wake
    try:
        while True:
            wake.clear()
            delay=2
            try:
                if await service.tick():delay=0
            except asyncio.CancelledError:raise
            except Exception:
                log.warning('Game credit queue is temporarily unavailable')
                delay=30
            if delay==30:
                await asyncio.sleep(delay)
            elif delay:
                try:await asyncio.wait_for(wake.wait(),delay)
                except TimeoutError:pass
    finally:
        if _wakeups.get(loop) is wake:_wakeups.pop(loop,None)
        tasks=[task for (scope,_),task in list(_flights.items()) if scope==id(AsyncSessionLocal)]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
