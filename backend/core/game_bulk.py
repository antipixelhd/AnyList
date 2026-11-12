"""Provisional portfolio participation, separate from verified credit evidence."""
import asyncio
import uuid
from datetime import timedelta, datetime, timezone

from sqlalchemy import Integer, String, column, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from core.game_imdb_enrichment import now, request as admit, snapshot
from core.game_types import igdb_game_class
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueRelationship, MetadataSnapshot

JOB='game_bulk'
PROVIDER='anylist'
_wake=asyncio.Event()


async def request(db,entity_id,kind,native):
    from core.game_work import demand, job_key, Priority
    key=f'{entity_id}:{kind}'
    state=await admit(db,key,kind=JOB,provider=PROVIDER,entity_id=entity_id)
    if state['pending']:
        await demand(db,job_key(PROVIDER,JOB,key),priority=Priority.FIRST_WORKS)
        await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider==PROVIDER,
            MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id==key).values(
                coverage=MetadataSnapshot.coverage.op('||')({'native':str(native),'kind':kind})))
        _wake.set()


async def status(db,entity_id,kind,state,*,current=None):
    row=(await db.execute(snapshot(JOB,f'{entity_id}:{kind}',provider=PROVIDER).with_only_columns(
        MetadataSnapshot.fetched_at,MetadataSnapshot.next_attempt_at,MetadataSnapshot.lease_until,MetadataSnapshot.coverage,
        func.jsonb_typeof(MetadataSnapshot.payload['source_ids']).label('source_ids_type')))).one_or_none()
    if not row:return state
    pending=bool(row.next_attempt_at or row.lease_until and row.lease_until>now())
    result={**state,'revision':f'{state.get("revision","")}:{row.fetched_at}',
        **({'pending':True,'complete':False,'retry_after':2} if pending else {})}
    if current is None:
        from core.game_contributor_works import game_credits
        credits=game_credits(entity_id,kind)
        current=await db.scalar(select(func.count(func.distinct(credits.c.id))))
    remaining=None
    if row.source_ids_type=='array' and row.coverage.get('source_complete'):
        source=func.jsonb_array_elements_text(MetadataSnapshot.payload['source_ids']).table_valued('id').render_derived(name='source_progress')
        sources=select(source.c.id).select_from(MetadataSnapshot).join(source,literal(True)).where(MetadataSnapshot.provider==PROVIDER,MetadataSnapshot.kind==JOB,
            MetadataSnapshot.external_id==f'{entity_id}:{kind}').subquery()
        accepted=func.jsonb_to_recordset(MetadataSnapshot.payload['rows']).table_valued(
            column('source',String)).render_derived(name='accepted_progress',with_types=True)
        accepted_ids=select(accepted.c.source).select_from(MetadataSnapshot).join(accepted,literal(True)).where(
            MetadataSnapshot.provider==PROVIDER,MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id==f'{entity_id}:{kind}')
        from models.catalogue import CatalogueCredit
        if kind!='studio':
            from sqlalchemy.orm import aliased
            from core.game_imdb_discovery import MAP,VERSION
            mapped=aliased(MetadataSnapshot)
            settled=select(mapped.external_id).where(mapped.provider=='wikidata',mapped.kind==MAP,
                mapped.expires_at>now(),mapped.payload['matcher_version'].as_integer()==VERSION,
                mapped.payload['retry_after'].as_integer().is_(None),mapped.payload['status'].astext!='verified')
        else:
            settled=select(literal('',String)).where(literal(False))
        remaining=await db.scalar(select(func.count(func.distinct(sources.c.id))).where(
            sources.c.id.not_in(accepted_ids),sources.c.id.not_in(settled),
            ~select(literal(1)).where(CatalogueCredit.contributor_id==entity_id,
                CatalogueCredit.provider=='imdb' if kind!='studio' else CatalogueCredit.provider=='igdb',
                (CatalogueCredit.attributes['imdb_title_id'].astext if kind!='studio' else CatalogueCredit.attributes['source_record']['id'].astext)==sources.c.id,
                CatalogueCredit.attributes['source_active'].astext.is_distinct_from('false')).exists()))
    works_pending=bool(result.get('pending') and (pending or remaining is None or remaining>0))
    if kind=='studio':works_pending=bool(pending or result.get('works_pending',works_pending))
    return {**result,'works_pending':works_pending,'works_current':current,
        'works_total':current+remaining if remaining is not None else None}



def projection(entity_id,kind,verified):
    """Expand only public projection fields in SQL; never transfer whole careers."""
    rows=func.jsonb_to_recordset(MetadataSnapshot.payload['rows']).table_valued(
        column('work_id',Integer),column('source',String),column('role',String)).render_derived(name='bulk_rows',with_types=True)
    work=CatalogueEntity
    return select(work.id.label('id'),work.name.label('title'),work.image_url.label('poster'),
        work.attributes['backdrop_path'].astext.label('backdrop'),
        func.coalesce(work.attributes['release_date'].astext,'').label('released'),
        work.attributes['game_class'].astext.label('game_class'),
        select(func.min(CatalogueRelationship.target_id)).where(
            CatalogueRelationship.source_id==work.id,CatalogueRelationship.relation=='expansion').correlate(work).scalar_subquery().label('base_work_id'),
        literal(None,String).label('credit_kind'),literal(None,String).label('credit_title'),
        rows.c.role.label('role'),literal(None,String).label('role_label'),literal(None,String).label('character_label')
    ).select_from(MetadataSnapshot).join(rows,literal(True)).join(work,work.id==rows.c.work_id).where(
        MetadataSnapshot.provider==PROVIDER,MetadataSnapshot.kind==JOB,
        MetadataSnapshot.external_id==f'{entity_id}:{kind}',
        ~select(verified.c.id).where(verified.c.id==work.id).exists(),work.kind=='game',
        work.attributes['adult'].astext.is_distinct_from('true'),
        work.attributes['mobile_only'].astext.is_distinct_from('true'))


async def accepted_sources(db,entity_id,kinds):
    rows=func.jsonb_to_recordset(MetadataSnapshot.payload['rows']).table_valued(
        column('source',String)).render_derived(name='accepted_rows',with_types=True)
    return set(await db.scalars(select(rows.c.source).select_from(MetadataSnapshot).join(rows,literal(True)).where(
        MetadataSnapshot.provider==PROVIDER,MetadataSnapshot.kind==JOB,
        MetadataSnapshot.external_id.in_([f'{entity_id}:{kind}' for kind in kinds]))))


async def retire(db,entity_id,kind,sources=None):
    """Retire only established outcomes; provider failures never erase candidates."""
    row=await db.scalar(snapshot(JOB,f'{entity_id}:{kind}',provider=PROVIDER).with_for_update())
    if not row or not row.payload:return
    rows=row.payload.get('rows',[])
    retained=[] if sources is None else [item for item in rows if item['source'] not in sources]
    if retained!=rows:
        row.payload={**row.payload,'rows':retained};row.fetched_at=now()


async def queue_metadata(db,entity_id,kind):
    """After participation verification, hydrate canonical works at idle priority."""
    from core.game_work import current, Priority
    if current.get().priority==Priority.AUTOMATIC:return
    from core.game_contributor_works import verified_game_credits
    credits=verified_game_credits(entity_id,kind)
    records=(await db.execute(select(CatalogueIdentity.external_id,CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace=='igdb.game',CatalogueIdentity.entity_id.in_(select(credits.c.id))))).all()
    for offset in range(0,len(records),250):
        values=[{'provider':'igdb','kind':'game','external_id':native,'entity_id':work,
            'next_attempt_at':now(),'coverage':{'priority':'background'}} for native,work in records[offset:offset+250]]
        if values:
            await db.execute(insert(MetadataSnapshot).values(values).on_conflict_do_update(
                constraint='uq_catalogue_snapshot',set_={'next_attempt_at':now(),'error_code':None,
                    'coverage':MetadataSnapshot.coverage.op('||')({'priority':'background'})},where=(
                    MetadataSnapshot.next_attempt_at.is_(None)
                    & or_(MetadataSnapshot.expires_at.is_(None),MetadataSnapshot.expires_at<=now())
                    & or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))))


async def defer_verification(db,provider,job_kind,native,token,kinds):
    """Give the bounded initial pass first use of shared provider lanes.

    An abandoned/retrying bulk job cannot hold normal acquisition indefinitely.
    Projection-only replays must call through without this gate.
    """
    from sqlalchemy import cast
    from sqlalchemy.orm import aliased
    source=aliased(MetadataSnapshot)
    entity=select(source.entity_id).where(source.provider==provider,source.kind==job_kind,source.external_id==str(native)).scalar_subquery()
    keys=[cast(entity,String)+':'+kind for kind in kinds]
    pending=await db.scalar(select(MetadataSnapshot.id).where(MetadataSnapshot.provider==PROVIDER,
        MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id.in_(keys),
        MetadataSnapshot.error_code.is_(None),MetadataSnapshot.next_attempt_at<=now(),
        MetadataSnapshot.next_attempt_at>=now()-timedelta(seconds=6)).limit(1))
    if not pending:return False
    await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider==provider,
        MetadataSnapshot.kind==job_kind,MetadataSnapshot.external_id==str(native),MetadataSnapshot.lease_token==token)
        .values(lease_token=None,lease_until=None,next_attempt_at=now()+timedelta(seconds=.5)))
    await db.commit();return True


async def publish(db,job,candidates):
    from core import catalogue
    await catalogue.write_lock(db)
    games={str(item['game']['id']):item['game'] for item in candidates}
    for game in list(games.values()):
        parent=game.get('parent_game')
        if igdb_game_class(game)=='expansion' and isinstance(parent,dict):games[str(parent['id'])]=parent
    known=dict((await db.execute(select(CatalogueIdentity.external_id,CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace=='igdb.game',CatalogueIdentity.external_id.in_(games)))).all()) if games else {}
    from core.catalogue_normalize import igdb_steam_identities
    from core.game_identity_reconciliation import discovery_identities
    steam_by_game = {native: igdb_steam_identities(game) for native, game in games.items()}
    apps = {identity['external_id'] for identities in steam_by_game.values() for identity in identities}
    bound_native = CatalogueIdentity.__table__.alias('bound_native')
    legacy_apps = set(await db.scalars(select(CatalogueIdentity.external_id).where(
        CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id.in_(apps),
        ~select(bound_native.c.id).where(bound_native.c.entity_id == CatalogueIdentity.entity_id,
            bound_native.c.namespace == 'igdb.game').exists()))) if apps else set()
    for native, game in games.items():
        steam = steam_by_game[native]
        if not any(identity['external_id'] in legacy_apps for identity in steam):
            continue
        values = await discovery_identities(db, {'kind': 'game', 'name': game['name'],
            'attributes': {'game_class': igdb_game_class(game)},
            'identities': [{'namespace': 'igdb.game', 'external_id': native}, *steam]})
        if len(values['identities']) > 1:
            known[native] = (await catalogue.upsert_entity(db, values, 'igdb')).id
    new=[]
    for native,game in games.items():
        if native in known:continue
        image_id=(game.get('cover') or {}).get('image_id')
        stamp=game.get('first_release_date')
        released=str(datetime.fromtimestamp(stamp,timezone.utc).year) if type(stamp) is int else None
        new.append({'kind':'game','name':game['name'],'image_url':f'https://images.igdb.com/igdb/image/upload/t_cover_big/{image_id}.jpg' if image_id else None,
            'attributes':{'igdb_id':native,'game_class':igdb_game_class(game),'mobile_only':False,
                'release_date':released,'release_date_precision':'year' if released else 'unknown'},
            'field_sources':{},'protected_fields':[]})
    for offset in range(0,len(new),250):
        created=(await db.execute(insert(CatalogueEntity).values(new[offset:offset+250]).returning(
            CatalogueEntity.id,CatalogueEntity.attributes['igdb_id'].astext))).all()
        await db.execute(insert(CatalogueIdentity).values([{'entity_id':work,'namespace':'igdb.game','external_id':native,'source':'igdb','evidence':{}} for work,native in created]))
        known.update({native:work for work,native in created})
    rows=[]
    for candidate in candidates:
        game=candidate['game'];work_id=known[str(game['id'])]
        parent=game.get('parent_game')
        if igdb_game_class(game)=='expansion' and isinstance(parent,dict):
            await db.execute(insert(CatalogueRelationship).values(source_id=work_id,target_id=known[str(parent['id'])],relation='expansion',provider='igdb',attributes={}).on_conflict_do_nothing())
        rows.extend({'work_id':work_id,'source':candidate['source'],'role':role} for role in candidate['roles'])
    if job.coverage.get('kind')!='studio' and rows:
        from core.game_imdb_discovery import MAP,VERSION
        evidence=(await db.execute(select(MetadataSnapshot.external_id,MetadataSnapshot.entity_id,
            MetadataSnapshot.payload['status'].astext).where(
            MetadataSnapshot.provider=='wikidata',MetadataSnapshot.kind==MAP,
            MetadataSnapshot.external_id.in_({row['source'] for row in rows}),
            MetadataSnapshot.expires_at>now(),MetadataSnapshot.payload['matcher_version'].as_integer()==VERSION,
            MetadataSnapshot.payload['status'].astext.in_(('verified','excluded','conflict'))))).all()
        settled={source:(work,state) for source,work,state in evidence}
        rows=[row for row in rows if row['source'] not in settled or (
            settled[row['source']][1]=='verified' and settled[row['source']][0]==row['work_id'])]
    if job.coverage.get('kind')=='studio':
        from core.game_company_works import JOB as COMPANY_JOB
        state=(await db.execute(snapshot(COMPANY_JOB,job.coverage['native'],provider='igdb').with_only_columns(
            MetadataSnapshot.coverage['complete'].as_boolean(),MetadataSnapshot.next_attempt_at,MetadataSnapshot.lease_until))).one_or_none()
        if state and state[0] and not state[1] and not (state[2] and state[2]>now()):rows=[]
    job.payload={**(job.payload or {}),'rows':rows};job.fetched_at=now()


class GameBulk:
    def __init__(self,sessions,source=None):
        from core.game_bulk_source import GameBulkSource
        self.sessions=sessions;self.source=source or GameBulkSource(sessions)

    async def tick(self):
        token=uuid.uuid4().hex
        async with self.sessions() as db:
            job=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider==PROVIDER,
                MetadataSnapshot.kind==JOB,MetadataSnapshot.next_attempt_at<=now(),
                or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
                .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(1).with_for_update(skip_locked=True))
            if not job:return False
            job.lease_token=token;job.lease_until=now()+timedelta(minutes=1)
            key=job.external_id;coverage=dict(job.coverage);await db.commit()
        try:
            from core.game_work import job_context
            async with job_context(self.sessions,PROVIDER,JOB,key,first=True):
                candidates,complete=await self.source.acquire(coverage['native'],coverage['kind'])
            async with self.sessions() as db:
                from core import catalogue
                await catalogue.write_lock(db)
                job=await db.scalar(snapshot(JOB,key,provider=PROVIDER).with_for_update())
                if not job or job.lease_token!=token:return True
                if candidates:await publish(db,job,candidates)
                job.payload={**(job.payload or {}),'source_ids':sorted(getattr(self.source,'candidate_sources',set()))}
                if coverage['kind']!='studio':
                    job.payload={**(job.payload or {}),'priority_sources':sorted(getattr(self.source,'priority_sources',set()))}
                job.coverage={**coverage,'complete':complete,'source_complete':getattr(self.source,'source_complete',complete),'state':'ready' if complete else 'partial'}
                job.expires_at=now()+timedelta(days=7 if complete else 1)
                job.next_attempt_at=None;job.lease_until=None;job.lease_token=None;await db.commit()
        except BaseException as error:
            async with self.sessions() as db:
                await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider==PROVIDER,
                    MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id==key,MetadataSnapshot.lease_token==token)
                    .values(lease_token=None,lease_until=None,next_attempt_at=now()+timedelta(seconds=60),error_code='retry_pending'))
                await db.commit()
            if isinstance(error,asyncio.CancelledError):raise
        return True


async def scheduler():
    from db import AsyncSessionLocal
    service=GameBulk(AsyncSessionLocal)
    while True:
        if await service.tick():continue
        _wake.clear()
        try:await asyncio.wait_for(_wake.wait(),.5)
        except TimeoutError:pass
