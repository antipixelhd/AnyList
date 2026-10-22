"""On-demand IMDb person components, isolated from public movie/series profiles.

Complete source connections are cached independently of game mapping coverage.
Only existing explicit IMDb-to-IGDB bindings become catalogue participation.
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from time import monotonic

from sqlalchemy import Integer, cast, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert,JSONB

from core import catalogue
from core.catalogue_providers import ProviderError, ProviderHTTP
from core.game_imdb_enrichment import now, snapshot, request as admit, status as job_status
from core.game_imdb_source import IMDbGameSource, identifier, TITLE_ID
from core.game_imdb_projection import ROLE
from core.game_imdb_bindings import revision as binding_revision, verified_bindings
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, MetadataSnapshot, CharacterPerformance

JOB='game_person_job'
PAGE='game_person_page'
COMPONENT='game_person_credits'
_flights={}
_slots=asyncio.Semaphore(1)


async def person_identity(db,entity_id):
    entity=await db.get(CatalogueEntity,entity_id)
    if not entity or entity.kind!='person':return None
    root=entity.canonical_person_id or entity.id
    natives=list(await db.scalars(select(CatalogueIdentity.external_id).where(
        CatalogueIdentity.entity_id==root,CatalogueIdentity.namespace=='imdb.person')))
    return (root,natives[0]) if len(natives)==1 else None


async def request(db,entity_id):
    identity=await person_identity(db,entity_id)
    if not identity:return None
    root,native=identity
    return {'id':native,**await admit(db,native,kind=JOB,entity_id=root)}


async def view(db,native,*,state=None,include_profile=True,check_mapping=False):
    """Small component/header read; never transfer cached filmography pages."""
    identifier(native,person=True)
    row=(await db.execute(snapshot(COMPONENT,native).with_only_columns(
        *([MetadataSnapshot.payload['profile'].label('profile')] if include_profile else []),MetadataSnapshot.coverage,
        MetadataSnapshot.fetched_at))).one_or_none()
    if state is None:state=await job_status(db,native,kind=JOB)
    if check_mapping and row and row.coverage.get('source_complete') and not state['pending']:
        current=await binding_revision(db)
        if row.coverage.get('binding_revision')!=current:
            # A reviewed/new game mapping can unlock cached person works.
            # Replay the saved connection instead of refetching IMDb for it.
            if await queue_projection(db,native):state={**state,'state':'pending','pending':True,'complete':False,'error':None,'retry_after':2}
    return {'id':native,**state,**({'profile':row.profile if row else None} if include_profile else {}),
        'fetched_at':row.fetched_at.replace(tzinfo=timezone.utc).isoformat() if row and row.fetched_at else None,
        'revision':row.coverage.get('projection_revision') if row else None,
        'mapping':row.coverage if row else {'state':'unknown'}}


async def queue_projection(db,native):
    queued=await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',
        MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id==native,MetadataSnapshot.next_attempt_at.is_(None),
        or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
        .values(next_attempt_at=now(),payload=func.coalesce(MetadataSnapshot.payload,cast({},JSONB)).op('||')({'projection_only':True}))
        .returning(MetadataSnapshot.id))
    return queued.scalar_one_or_none() is not None


def profile(header):
    denied=set(header.get('unavailable_fields') or [])
    def permitted(field):return not any(value==f'Name.{field}' or value.startswith(f'Name.{field}.') for value in denied)
    result={}
    biography=((header.get('bio') or {}).get('text') or {}).get('plainText')
    if permitted('bio') and isinstance(biography,str) and biography.strip():result['biography']=biography.strip()
    for source,target in [('birthDate','birthday'),('deathDate','deathday')]:
        values=(header.get(source) or {}).get('dateComponents') or {}
        parts=[values.get(key) for key in ('year','month','day')]
        if permitted(source) and all(type(value) is int for value in parts):
            try:result[target]=date(*parts).isoformat()
            except ValueError:pass
    professions=[(row.get('category') or {}).get('text') for row in header.get('primaryProfessions') or [] if isinstance(row,dict)]
    if permitted('primaryProfessions'):
        result['known_for_department']=' / '.join(dict.fromkeys(value for value in professions if isinstance(value,str) and value.strip())) or None
    return {key:value for key,value in result.items() if catalogue.has_value(value)}


def game_rows(rows):
    games={};omitted=0
    for index,row in enumerate(rows):
        title=row.get('title') or {}
        if not isinstance(title,dict) or not isinstance(title.get('titleType'),dict) or not title['titleType'].get('id'):
            omitted+=1;continue
        if title['titleType']['id']!='videoGame':continue
        native=title.get('id');role=(row.get('category') or {}).get('id')
        if not isinstance(native,str) or not TITLE_ID.fullmatch(native) or not isinstance(role,str) or not ROLE.fullmatch(role):
            omitted+=1;continue
        key=(native,role)
        games.setdefault(key,{'title_id':native,'category':role,'position':index})
    return list(games.values()),omitted


async def project_mapped(db,native,rows,generation,*,source_generation=None):
    """One identity read and batched writes; no provider calls or name matching."""
    person=await catalogue.resolve_identity(db,'imdb.person',native)
    if not person:raise catalogue.IdentityConflict('Person identity is no longer available')
    entity=await db.get(CatalogueEntity,person.entity_id)
    root=entity.canonical_person_id or entity.id
    games,omitted=await asyncio.to_thread(game_rows,rows)
    if omitted:raise ProviderError('bad_response')
    ids={row['title_id'] for row in games}
    bindings=dict((await db.execute(verified_bindings(ids).with_only_columns(
        CatalogueIdentity.external_id,CatalogueIdentity.entity_id))).all()) if ids else {}
    credits=[]
    for row in games:
        work=bindings.get(row['title_id'])
        if not work:continue
        role='actor' if row['category'] in ('actor','actress','cast') else row['category']
        credits.append({'work_id':work,'contributor_id':root,'role':role,'provider':'imdb','scope':'person',
            'role_label':'Actor' if role=='actor' else role.replace('_',' ').title(),
            'source_key':f"person:{native}:{row['title_id']}:{row['category']}",
            'position':None,'attributes':{'work_domain':'game','source_active':True,
                'imdb_person_id':native,'imdb_person_evidence':True,'imdb_person_generation':generation,
                'imdb_person_source_generation':source_generation or generation,
                'imdb_title_id':row['title_id'],'imdb_category':row['category'],
                'person_credit_order':row['position'],'voice':None,'languages':[],
                'source_url':f'https://www.imdb.com/name/{native}/'}})
    for offset in range(0,len(credits),100):
        statement=insert(CatalogueCredit).values(credits[offset:offset+100])
        await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_credit_source',set_={
            'contributor_id':statement.excluded.contributor_id,'role':statement.excluded.role,
            'scope':statement.excluded.scope,
            'role_label':statement.excluded.role_label,
            'attributes':CatalogueCredit.attributes.op('||')(statement.excluded.attributes)}))
    await db.execute(update(CatalogueCredit).where(CatalogueCredit.provider=='imdb',
        CatalogueCredit.scope=='person',
        CatalogueCredit.attributes['imdb_person_id'].astext==native,
        CatalogueCredit.attributes['imdb_person_evidence'].astext=='true',
        CatalogueCredit.attributes['imdb_person_generation'].astext.is_distinct_from(generation),
        ~select(CharacterPerformance.id).where(CharacterPerformance.credit_id==CatalogueCredit.id,
            CharacterPerformance.source=='reviewed').exists()).values(
                attributes=CatalogueCredit.attributes.op('||')({'source_active':False})).execution_options(synchronize_session=False))
    acting={'actor','actress','cast','voice_actor','narrator'}
    role_coverage={}
    for kind in ('actor','staff'):
        titles={row['title_id'] for row in games if (row['category'] in acting)==(kind=='actor') and row['category']!='thanks'}
        role_coverage[kind]={'source':len(titles),'unmapped':len(titles-set(bindings))}
    return {'role_coverage':role_coverage,'state':'partial' if omitted or ids-set(bindings) else 'mapped','source_games':len(ids),
        'mapped_games':len(set(bindings)&ids),'unmapped_games':len(ids-set(bindings)),'omitted_rows':omitted,
        'scope':'retrieved_person_connection','official_filmography_complete':False}


class GameIMDbPersonEnrichment:
    def __init__(self,sessions,*,source=None,page_budget=12):
        self.sessions=sessions
        self.source=source or IMDbGameSource(ProviderHTTP(session_factory=sessions))
        self.page_budget=page_budget

    async def run(self,native):
        identifier(native,person=True)
        key=(id(self.sessions),native)
        task=_flights.get(key)
        if task is None:
            if len(_flights)>=64:return 'busy'
            task=asyncio.create_task(self._bounded(native));_flights[key]=task
            def done(finished):
                if _flights.get(key) is finished:_flights.pop(key,None)
                if not finished.cancelled():finished.exception()
            task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _bounded(self,native):
        async with _slots:return await self._run(native)

    async def claim(self,native):
        async with self.sessions() as db:
            job=await db.scalar(snapshot(JOB,native).with_for_update())
            if not job or not job.next_attempt_at or job.next_attempt_at>now() or job.lease_until and job.lease_until>now():return None
            token=uuid.uuid4().hex;job.lease_token=token;job.lease_until=now()+timedelta(minutes=3)
            state=dict(job.payload or {})
            await db.commit();return token,state

    async def _run(self,native):
        claimed=await self.claim(native)
        if not claimed:return 'cached'
        token,state=claimed
        try:
            if state.get('projection_only'):
                return await self.reproject(native,token,state)
            recent=False
            if isinstance(state.get('started_at'),str):
                try:recent=now()-datetime.fromisoformat(state['started_at'])<timedelta(days=1)
                except ValueError:pass
            if not recent or 'cursor' not in state:
                state={'state':'partial','generation':uuid.uuid4().hex,'started_at':now().isoformat(),
                    'cursor':None,'seen':[],'received':0,'total':None,'pages':0,'complete':False}
            deadline=monotonic()+55
            for _ in range(self.page_budget):
                async with asyncio.timeout(max(1,deadline-monotonic())):
                    page=await self.source.credits(native,state['cursor'],person=True)
                if state['total'] is not None and page['total']!=state['total']:
                    await self.fail(native,token,'changed_connection',restart=True);return 'partial'
                received=state['received']+len(page['rows'])
                if (page['has_next'] and page['cursor'] in state['seen'] or received>page['total']
                    or not page['has_next'] and received!=page['total']):raise ProviderError('bad_response')
                if (await asyncio.to_thread(game_rows,page['rows']))[1]:raise ProviderError('bad_response')
                if not await self.publish(native,token,state,page):return 'superseded'
                if not page['has_next']:return 'ready'
                if monotonic()>=deadline:break
            await self.release(native,token,delay=2)
            return 'partial'
        except asyncio.CancelledError:
            await self.release(native,token);raise
        except (ProviderError,catalogue.IdentityConflict,TimeoutError) as error:
            await self.fail(native,token,getattr(error,'code','identity_conflict' if isinstance(error,catalogue.IdentityConflict) else 'timeout'),
                restart=isinstance(error,ProviderError) and error.code=='incomplete_cache',
                retry_after=getattr(error,'retry_after',None));return 'retry'
        except Exception:
            await self.fail(native,token,'ingestion_failed');return 'retry'

    async def publish(self,native,token,state,page):
        async with self.sessions() as db:
            await catalogue.write_lock(db)
            job=await db.scalar(snapshot(JOB,native).with_for_update())
            if not job or job.lease_token!=token:return False
            if not await catalogue.resolve_identity(db,'imdb.person',native):raise catalogue.IdentityConflict('Person identity changed')
            if state['pages']==0:
                retained=await db.scalar(snapshot(COMPONENT,native).with_only_columns(MetadataSnapshot.payload['generation'].astext))
                old=delete(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==PAGE,
                    MetadataSnapshot.external_id.like(f'{native}:%'),
                    ~MetadataSnapshot.external_id.like(f"{native}:{state['generation']}:%"))
                if retained:old=old.where(~MetadataSnapshot.external_id.like(f'{native}:{retained}:%'))
                # Keep a usable complete generation and this attempt, rather
                # than accumulating abandoned partial refreshes indefinitely.
                await db.execute(old)
            key=f"{native}:{state['generation']}:{state['pages']}"
            await db.execute(insert(MetadataSnapshot).values(provider='imdb',kind=PAGE,external_id=key,
                entity_id=job.entity_id,payload=page,coverage={'page':state['pages']},fetched_at=now()).on_conflict_do_nothing())
            state.update(cursor=page['cursor'] if page['has_next'] else None,received=state['received']+len(page['rows']),
                total=page['total'],pages=state['pages']+1,complete=not page['has_next'])
            if page['has_next']:state['seen']=[*state['seen'],page['cursor']]
            if state['complete']:
                pages=list(await db.scalars(select(MetadataSnapshot.payload).where(MetadataSnapshot.provider=='imdb',
                    MetadataSnapshot.kind==PAGE,MetadataSnapshot.external_id.like(f"{native}:{state['generation']}:%"))
                    .order_by(cast(MetadataSnapshot.coverage['page'].astext,Integer))))
                if len(pages)!=state['pages'] or sum(len(stored['rows']) for stored in pages)!=state['received']:
                    raise ProviderError('incomplete_cache')
                mapping=await project_mapped(db,native,[row for stored in pages for row in stored['rows']],state['generation'])
                component=await db.scalar(snapshot(COMPONENT,native).with_for_update())
                if not component:
                    component=MetadataSnapshot(provider='imdb',kind=COMPONENT,external_id=native);db.add(component)
                previous=(component.payload or {}).get('profile') or {}
                component.entity_id=job.entity_id
                incoming=profile(pages[0]['header'])
                field_times={**(component.payload or {}).get('profile_fetched_at',{}),
                    **{key:now().isoformat() for key in incoming}}
                component.payload={'profile':{**previous,**incoming},'profile_fetched_at':field_times,'generation':state['generation'],
                    'received':state['received'],'total':state['total'],'pages':state['pages']}
                from core.game_imdb_review import write_index
                component.payload={**component.payload,**await write_index(db,native,state['generation'],
                    [row for stored in pages for row in stored['rows']],entity_id=job.entity_id)}
                component.coverage={**mapping,'source_complete':True,'projection_revision':state['generation'],
                    'binding_revision':await binding_revision(db)}
                component.fetched_at=now();component.expires_at=now()+timedelta(days=30)
                state['state']='complete';state.pop('cursor');state.pop('seen')
                job.next_attempt_at=job.lease_token=job.lease_until=None
                job.expires_at=now()+timedelta(days=30)
                await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==PAGE,
                    MetadataSnapshot.external_id.like(f'{native}:%'),~MetadataSnapshot.external_id.like(f"{native}:{state['generation']}:%")))
            else:job.lease_until=now()+timedelta(minutes=3)
            job.payload=dict(state);job.coverage={key:state[key] for key in ('state','complete','received','total')}
            job.fetched_at=now();job.error_code=None;job.attempts=0
            await db.commit();return True

    async def reproject(self,native,token,state):
        async with self.sessions() as db:
            await catalogue.write_lock(db)
            job=await db.scalar(snapshot(JOB,native).with_for_update())
            if not job or job.lease_token!=token:return 'superseded'
            component=await db.scalar(snapshot(COMPONENT,native).with_for_update())
            if not component or not component.coverage.get('source_complete'):raise ProviderError('incomplete_cache')
            generation=component.payload['generation']
            pages=list(await db.scalars(select(MetadataSnapshot.payload['rows']).where(MetadataSnapshot.provider=='imdb',
                MetadataSnapshot.kind==PAGE,MetadataSnapshot.external_id.like(f'{native}:{generation}:%'))
                .order_by(cast(MetadataSnapshot.coverage['page'].astext,Integer))))
            if len(pages)!=component.payload['pages'] or sum(map(len,pages))!=component.payload['received']:
                raise ProviderError('incomplete_cache')
            projection=uuid.uuid4().hex
            mapping=await project_mapped(db,native,[row for page in pages for row in page],projection,source_generation=generation)
            from core.game_imdb_review import write_index
            component.payload={**component.payload,**await write_index(db,native,generation,
                [row for page in pages for row in page],entity_id=job.entity_id)}
            component.coverage={**mapping,'source_complete':True,'projection_revision':projection,
                'binding_revision':await binding_revision(db)}
            state.pop('projection_only',None)
            state.update(state='complete',complete=True)
            job.payload=state;job.coverage={key:state[key] for key in ('state','complete','received','total')}
            job.lease_token=job.lease_until=job.next_attempt_at=None
            job.error_code=None;job.attempts=0
            # Mapping changes never renew the source's original fetch/expiry.
            await db.commit();return 'ready'

    async def release(self,native,token,delay=0):
        async with self.sessions() as db:
            await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==JOB,
                MetadataSnapshot.external_id==native,MetadataSnapshot.lease_token==token).values(
                    lease_token=None,lease_until=None,next_attempt_at=now()+timedelta(seconds=delay)))
            await db.commit()

    async def fail(self,native,token,code,*,restart=False,retry_after=None):
        async with self.sessions() as db:
            job=await db.scalar(snapshot(JOB,native).with_for_update())
            if not job or job.lease_token!=token:return
            if restart:job.payload={'state':'partial','complete':False};job.coverage={'state':'partial','complete':False}
            job.lease_token=job.lease_until=None;job.attempts+=1;job.error_code=code
            delay=retry_after if isinstance(retry_after,(int,float)) and 0<retry_after<=3600 else 60*2**min(6,job.attempts-1)
            job.next_attempt_at=now()+timedelta(seconds=min(3600,delay));await db.commit()

    async def tick(self):
        async with self.sessions() as db:
            native=await db.scalar(select(MetadataSnapshot.external_id).where(MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==JOB,
                MetadataSnapshot.next_attempt_at<=now(),or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(1))
        if native:await self.run(native)


async def scheduler():
    from db import AsyncSessionLocal
    service=GameIMDbPersonEnrichment(AsyncSessionLocal)
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
