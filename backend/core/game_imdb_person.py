"""On-demand IMDb person components, isolated from public movie/series profiles.

Complete source connections are cached independently of game mapping coverage.
Explicit IMDb-to-IGDB bindings become participation; missing bindings are discovered on demand.
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from time import monotonic

from sqlalchemy import Integer, String, cast, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert,JSONB

from core import catalogue
from core.catalogue_providers import ProviderError, ProviderHTTP
from core.game_imdb_enrichment import now, snapshot, request as admit, status as job_status
from core.game_imdb_source import IMDbGameSource, identifier, TITLE_ID
from core.game_imdb_projection import ROLE,texts,VERSIONS
from core.game_imdb_bindings import revision as binding_revision, verified_bindings
from core.game_credit_roles import ACTING,SOURCE_ACTING,CREDIT_POLICY_VERSION
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, MetadataSnapshot, CharacterPerformance

JOB='game_person_job'
PAGE='game_person_page'
COMPONENT='game_person_credits'
VERSION=2
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
        MetadataSnapshot.payload['identity_bridge'].label('identity_bridge'),
        *([MetadataSnapshot.payload['generation'].astext.label('generation'),MetadataSnapshot.entity_id] if check_mapping else []),
        MetadataSnapshot.fetched_at))).one_or_none()
    if state is None:state=await job_status(db,native,kind=JOB)
    if check_mapping and row and row.coverage.get('person_version')!=VERSION and not state['pending']:
        # Reuse the component header already read above. Old connections lacked
        # Cast fields; refresh once without per-work acquisition or losing rows.
        await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',
            MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id==native,
            MetadataSnapshot.next_attempt_at.is_(None),
            or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
            .values(expires_at=now(),payload={}))
        state=await admit(db,native,kind=JOB,entity_id=row.entity_id)
    if check_mapping and row and row.coverage.get('source_complete') and not state['pending']:
        current=await binding_revision(db)
        if row.coverage.get('binding_revision')!=current or row.coverage.get('credit_policy_version')!=CREDIT_POLICY_VERSION:
            # A reviewed/new game mapping can unlock cached person works.
            # Replay the saved connection instead of refetching IMDb for it.
            if await queue_projection(db,native):state={**state,'state':'pending','pending':True,'complete':False,'error':None,'retry_after':2}
    if row and row.coverage.get('source_complete') and row.coverage.get('unmapped_games',0) and not state['pending']:
        from core.game_imdb_discovery import request as discover,header as discovery_header,status as discovery_status
        if check_mapping and row.generation and row.entity_id:
            discovery=await discover(db,native,row.generation,row.entity_id)
        else:
            saved=await discovery_header(db,native)
            discovery=discovery_status(saved) if saved else None
        if discovery and discovery['pending']:
            state={**state,**{key:discovery[key] for key in ('state','pending','complete','retry_after','error')}}
    # Keep admission order consistent with publication: source/discovery jobs
    # precede identity jobs. Do not hold an identity write while waiting for a
    # source job that is itself publishing the initial identity handoff.
    identity_state=None
    if row and row.coverage.get('source_complete'):
        from core.game_person_identity import request as resolve_person
        identity_state=await resolve_person(db,native,row.entity_id if check_mapping else None,
            row.identity_bridge,enqueue=check_mapping)
    identity_pending=bool(identity_state and identity_state['pending'])
    return {'id':native,**state,**({'profile':row.profile if row else None} if include_profile else {}),
        **({'identity_pending':True,**({'retry_after':identity_state['retry_after']} if not state['pending'] else {})} if identity_pending else {}),
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
        item=games.setdefault(key,{'title_id':native,'category':role,'position':index,'characters':[],'attributes':[]})
        for field,label in (('characters','name'),('attributes','text')):
            item[field]=list(dict.fromkeys([*item[field],*texts(row.get(field),label)]))
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
    from core.game_credit_parents import person_bindings
    source_bindings=person_bindings(ids)
    bound=(await db.execute(select(source_bindings))).all() if ids else []
    bindings={row.imdb_id:row.work_id for row in bound}
    child_credits={row.imdb_id:{'kind':row.credit_kind,'title':row.credit_title} for row in bound if row.credit_kind}
    # Unknown support is not a confirmed identity contradiction. Retain only
    # this person's already published participation; a stale binding cannot
    # create participation for another person or a newly observed role.
    retained={}
    if ids:
        from core.game_title_match import SUPPORTED_VERSIONS
        retained_query=select(
            CatalogueCredit.attributes['imdb_title_id'].astext,
            CatalogueCredit.attributes['imdb_category'].astext,CatalogueCredit.work_id,
            literal(None,String),literal(None,String)).join(
            CatalogueIdentity,CatalogueIdentity.entity_id==CatalogueCredit.work_id).where(
                CatalogueIdentity.namespace=='imdb.title',or_(CatalogueIdentity.evidence['mapping_state'].astext=='stale',
                    (CatalogueIdentity.source=='matched') & (func.coalesce(CatalogueIdentity.evidence['matcher_version'].as_integer(),0).not_in(SUPPORTED_VERSIONS))),
                CatalogueCredit.provider=='imdb',CatalogueCredit.scope=='person',
                CatalogueCredit.attributes['imdb_person_id'].astext==native,
                CatalogueCredit.attributes['source_active'].astext=='true',
                CatalogueCredit.attributes['imdb_title_id'].astext.in_(ids))
        from core.game_credit_parents import KIND as PARENT_KIND,VERSION as PARENT_VERSION
        retained_parents=select(CatalogueCredit.attributes['imdb_title_id'].astext,
            CatalogueCredit.attributes['imdb_category'].astext,CatalogueCredit.work_id,
            MetadataSnapshot.payload['kind'].astext,MetadataSnapshot.payload['title'].astext).join(
                MetadataSnapshot,(MetadataSnapshot.entity_id==CatalogueCredit.work_id) &
                (MetadataSnapshot.external_id==CatalogueCredit.attributes['imdb_title_id'].astext)).where(
                    MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==PARENT_KIND,
                    MetadataSnapshot.coverage['automatic'].astext=='true',
                    or_(MetadataSnapshot.coverage['mapping_state'].astext=='stale',
                        MetadataSnapshot.coverage['policy_version'].as_integer()!=PARENT_VERSION,
                        MetadataSnapshot.payload['evidence']['version'].as_integer().not_in(SUPPORTED_VERSIONS)),
                    CatalogueCredit.provider=='imdb',CatalogueCredit.scope=='person',
                    CatalogueCredit.attributes['imdb_person_id'].astext==native,
                    CatalogueCredit.attributes['source_active'].astext=='true',
                    CatalogueCredit.attributes['imdb_title_id'].astext.in_(ids))
        for title,category,work,kind,label in (await db.execute(retained_query.union_all(retained_parents))).all():
            retained[title,category]=work
            if kind and title not in bindings:child_credits[title]={'kind':kind,'title':label}
    from core.game_imdb_discovery import MAP,VERSION as DISCOVERY_VERSION
    excluded=set(await db.scalars(select(MetadataSnapshot.external_id).where(MetadataSnapshot.provider=='wikidata',
        MetadataSnapshot.kind==MAP,MetadataSnapshot.external_id.in_(ids),MetadataSnapshot.payload['status'].astext=='excluded',
        MetadataSnapshot.payload['matcher_version'].as_integer()==DISCOVERY_VERSION,
        MetadataSnapshot.expires_at>now()))) if ids else set()
    excluded|=ids-{row['title_id'] for row in games if row['category']!='thanks'}
    credits=[]
    for row in games:
        work=bindings.get(row['title_id']) or retained.get((row['title_id'],row['category']))
        if not work:continue
        role='actor' if row['category'] in ('actor','actress','cast') else row['category']
        characters=row['characters'] if role in ACTING else []
        attributes=row['attributes']
        credits.append({'work_id':work,'contributor_id':root,'role':role,'provider':'imdb','scope':'person',
            'role_label':'Actor' if role=='actor' else role.replace('_',' ').title(),
            'source_key':f"person:{native}:{row['title_id']}:{row['category']}",
            'position':None,'attributes':{'work_domain':'game','source_active':True,
                'imdb_person_id':native,'imdb_person_evidence':True,'imdb_person_generation':generation,
                'imdb_person_source_generation':source_generation or generation,
                'imdb_title_id':row['title_id'],'imdb_category':row['category'],
                'credited_child':child_credits.get(row['title_id']),
                'person_credit_order':row['position'],'voice':True if any(value.casefold()=='voice' for value in attributes) else None,
                'languages':list(dict.fromkeys(VERSIONS[value] for value in attributes if value in VERSIONS)),
                'game_role':{'characters':characters,'text_characters':characters},
                'person_character_evidence':bool(characters),
                'source_url':f'https://www.imdb.com/name/{native}/'}})
        credits[-1]['character_label']=' / '.join(characters)[:500] or None
    for offset in range(0,len(credits),100):
        statement=insert(CatalogueCredit).values(credits[offset:offset+100])
        await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_credit_source',set_={
            'contributor_id':statement.excluded.contributor_id,'role':statement.excluded.role,
            'scope':statement.excluded.scope,
            'character_label':statement.excluded.character_label,
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
    role_coverage={}
    for kind in ('actor','staff'):
        titles={row['title_id'] for row in games if (row['category'] in SOURCE_ACTING)==(kind=='actor') and row['category']!='thanks'}
        role_coverage[kind]={'source':len(titles-excluded),'raw_source':len(titles),'excluded':len(titles&excluded),
            'unmapped':len(titles-set(bindings)-excluded)}
    return {'credit_policy_version':CREDIT_POLICY_VERSION,'role_coverage':role_coverage,'state':'partial' if omitted or ids-set(bindings)-excluded else 'mapped','source_games':len(ids),
        'mapped_games':len(set(bindings)&ids),'excluded_games':len(excluded),'unmapped_games':len(ids-set(bindings)-excluded),'omitted_rows':omitted,
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
                component.payload={**({'identity_bridge':component.payload['identity_bridge']} if
                    component.payload and 'identity_bridge' in component.payload else {}),
                    'profile':{**previous,**incoming},'profile_fetched_at':field_times,'generation':state['generation'],
                    'received':state['received'],'total':state['total'],'pages':state['pages']}
                from core.game_imdb_review import write_index
                component.payload={**component.payload,**await write_index(db,native,state['generation'],
                    [row for stored in pages for row in stored['rows']],entity_id=job.entity_id)}
                component.coverage={**mapping,'person_version':VERSION,'source_complete':True,'projection_revision':state['generation'],
                    'binding_revision':await binding_revision(db)}
                component.fetched_at=now();component.expires_at=now()+timedelta(days=30)
                if mapping.get('unmapped_games'):
                    from core.game_imdb_discovery import request as discover
                    await discover(db,native,state['generation'],job.entity_id)
                from core.game_person_identity import request as resolve_person
                await resolve_person(db,native,job.entity_id,component.payload.get('identity_bridge'),enqueue=True)
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
            component.coverage={**mapping,**({'person_version':component.coverage['person_version']} if 'person_version' in component.coverage else {}),'source_complete':True,'projection_revision':projection,
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
        from core.game_person_identity import JOB as IDENTITY_JOB,GamePersonIdentity
        async with self.sessions() as db:
            queued=(await db.execute(select(MetadataSnapshot.kind,MetadataSnapshot.external_id).where(MetadataSnapshot.provider=='imdb',
                MetadataSnapshot.kind.in_((JOB,IDENTITY_JOB)),
                MetadataSnapshot.next_attempt_at<=now(),or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(1))).one_or_none()
        if queued:
            if queued.kind==IDENTITY_JOB:await GamePersonIdentity(self.sessions).run(queued.external_id)
            else:await self.run(queued.external_id)


async def scheduler():
    from db import AsyncSessionLocal
    from time import monotonic
    from core.game_person_identity import backfill
    service=GameIMDbPersonEnrichment(AsyncSessionLocal)
    next_backfill=0
    try:
        while True:
            delay=2
            try:
                if monotonic()>=next_backfill:
                    async with AsyncSessionLocal() as db:await backfill(db)
                    next_backfill=monotonic()+60
                await service.tick()
            except asyncio.CancelledError:raise
            except Exception:delay=30
            await asyncio.sleep(delay)
    finally:
        tasks=[task for (scope,_),task in list(_flights.items()) if scope==id(AsyncSessionLocal)]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
