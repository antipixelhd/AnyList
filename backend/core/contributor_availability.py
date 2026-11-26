"""Small, source-verified domain availability; a missing mapping is not empty."""
import hashlib
import json

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from core import contributor_evidence as evidence
from core.contributor_details import resolve_contributor, positive_id
from core.credit_roles import work_credit
from core.game_access import allowed, require
from core.game_contributor_works import game_credits
from core.statistics_staff import ACTING
from core.studio_groups import studio_companies, studio_root, studio_members, studio_entity_ids
from models import Media
from models.catalogue import ContributorEvidence, CatalogueEntity, CatalogueCredit, MetadataSnapshot
from models.title_credits import TitleCredits

TAB_SNAPSHOT='person_tab_availability'
STUDIO_TAB_SNAPSHOT='studio_tab_availability'
PERSON_ROLES=('actor','staff')
DOMAINS=('movie','series','game')


async def saved_tabs(db,kind,key,roles,*,include_games):
    domains=DOMAINS if include_games else ('movie','series')
    fields=[MetadataSnapshot.payload[f'{role}.{domain}'].astext.label(f'{role}.{domain}')
        for role in roles for domain in domains]
    values=(await db.execute(select(*fields).where(
        MetadataSnapshot.provider=='anylist',MetadataSnapshot.kind==kind,
        MetadataSnapshot.external_id==key))).mappings().one_or_none()
    if not values:return None
    states={role:{domain:values.get(f'{role}.{domain}') or 'unknown'
        for domain in DOMAINS} for role in roles}
    return states if any(value!='unknown' for domains in states.values() for value in domains.values()) else None


async def saved_person_tabs(db,entity_id,*,include_games):
    return await saved_tabs(db,TAB_SNAPSHOT,str(entity_id),PERSON_ROLES,include_games=include_games)


def studio_tab_key(key):
    return f"tmdb:{studio_root(int(key.split(':')[1]))}" if key.startswith('tmdb:') else key


async def saved_studio_tabs(db,key,*,include_games):
    states=await saved_tabs(db,STUDIO_TAB_SNAPSHOT,studio_tab_key(key),('studio',),include_games=include_games)
    return states['studio'] if states else None


async def save_tabs(db,kind,key,entity_id,states,*,include_games):
    observed={(role,domain):value for role,domains in states.items() for domain,value in domains.items()
        if value!='unknown'}
    if not observed:return
    previous=await saved_tabs(db,kind,key,states,include_games=include_games) or {}
    changed={f'{role}.{domain}':value for (role,domain),value in observed.items()
        if previous.get(role,{}).get(domain)!=value}
    if not changed:return
    write=insert(MetadataSnapshot).values(provider='anylist',kind=kind,
        external_id=key,entity_id=entity_id,payload=changed,fetched_at=evidence.now())
    await db.execute(write.on_conflict_do_update(constraint='uq_catalogue_snapshot',set_={
        'payload':MetadataSnapshot.payload.op('||')(write.excluded.payload),
        'fetched_at':write.excluded.fetched_at}))


def person_domains(credits):
    """Compute once in the worker, independent of personal list/curation filters."""
    result={}
    for kind,field in (('actor','cast'),('staff','crew')):
        works={'movie':set(),'series':set()};uncertain=False
        rows=credits.get(field)
        if not isinstance(rows,list):
            result[kind]={'movie':'unknown','series':'unknown'};continue
        for row in rows:
            if not isinstance(row,dict):uncertain=True;continue
            media={'movie':'movie','tv':'series'}.get(row.get('media_type'))
            if not media or not positive_id(row.get('id')) or not (row.get('title') or row.get('name')):
                uncertain=True;continue
            role='Actor' if kind=='actor' else row.get('job')
            if kind=='staff' and (not isinstance(role,str) or not role.strip()):uncertain=True;continue
            if row.get('adult') or not work_credit(role,row.get('character')):continue
            if kind=='staff' and role.casefold() in ACTING:continue
            works[media].add(row['id'])
        result[kind]={media:'available' if ids else 'unknown' if uncertain else 'empty' for media,ids in works.items()}
    return result


def movie_job(kind,native):
    if kind!='studio':return evidence.evidence_key('person',native,{},True)
    config={'version':1,'scope':'domain_availability','companies':studio_companies(native)}
    variant=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
    return ('studio',studio_root(native),variant),config


async def movie_domains(db,viewer,kind,native,kinds):
    unknown={'movie':'unknown','series':'unknown'}
    if not native:return {mode:unknown.copy() for mode in kinds},False,3
    key,config=movie_job(kind,native);table=ContributorEvidence;stamp=evidence.now()
    statement=select(table.payload['domain_availability'].label('domains'),table.expires_at,
        table.next_attempt_at,table.lease_until,table.attempts).where(
        table.kind==key[0],table.native_id==key[1],table.variant==key[2])
    row=(await db.execute(statement)).one_or_none()
    fresh=bool(row and row.expires_at and row.expires_at>stamp and not row.attempts
        and not row.next_attempt_at and not (row.lease_until and row.lease_until>stamp))
    summaries=(row.domains or {}) if row else {}
    states={mode:{**unknown,**{
        media:value if value in ('available','empty') and (fresh or value=='available') else 'unknown'
        for media,value in summaries.get(mode,{}).items() if media in unknown
    }} for mode in kinds}
    pending=bool(row and (row.next_attempt_at or row.lease_until and row.lease_until>stamp))
    missing=[mode for mode in kinds if mode not in summaries]
    due=not row or not pending and (not row.expires_at or row.expires_at<=stamp or missing and not row.attempts)
    delay=max(3,int((row.next_attempt_at-stamp).total_seconds())+1) if row and row.next_attempt_at else 3
    if due:pending=True
    if due and await db.scalar(select(func.pg_try_advisory_xact_lock(724052))):
        count=await db.scalar(select(func.count()).select_from(table).where(table.next_attempt_at.is_not(None)))
        if count<evidence.MAX_PENDING:
            viewer_id=viewer.id if viewer else None
            write=insert(table).values(kind=key[0],native_id=key[1],variant=key[2],config=config,
                requested_by=viewer_id,next_attempt_at=stamp)
            await db.execute(write.on_conflict_do_update(index_elements=['kind','native_id','variant'],
                set_={'next_attempt_at':stamp,'requested_by':viewer_id},where=table.next_attempt_at.is_(None)
                & or_(table.lease_until.is_(None),table.lease_until<=stamp)
                & or_(table.expires_at.is_(None),table.expires_at<=stamp,
                    *(table.payload['domain_availability'][mode].astext.is_(None) & (table.attempts==0) for mode in missing))))
            pending=True;evidence._wake.set()
    return states,pending,min(60,delay)


async def preserve_local_movies(db,states,entity,native,kind):
    """A source's negative must not hide other stored provider participation."""
    columns=[]
    for media,state in states.items():
        if state!='empty':continue
        terms=[]
        if entity:
            aliases=studio_entity_ids(native,entity.id) if kind=='studio' else select(CatalogueEntity.id).where(or_(CatalogueEntity.id==entity.id,
                CatalogueEntity.canonical_person_id==entity.id,CatalogueEntity.canonical_company_id==entity.id)).correlate(None)
            terms.append(select(CatalogueCredit.id).join(CatalogueEntity,CatalogueEntity.id==CatalogueCredit.work_id).where(
                CatalogueCredit.contributor_id.in_(aliases),CatalogueEntity.kind==media,
                CatalogueCredit.role=='producer' if kind=='studio' else CatalogueCredit.role.in_(('actor','voice_actor','narrator'))
                if kind=='actor' else ~CatalogueCredit.role.in_(('actor','voice_actor','narrator')),
                CatalogueCredit.attributes['source_active'].astext.is_distinct_from('false')).exists())
        if native:
            natives=studio_members(native) if kind=='studio' else (native,)
            fields=('studios',) if kind=='studio' else ('cast',) if kind=='actor' else ('directors','writers')
            terms.append(select(TitleCredits.id).where(TitleCredits.media_type==media,
                or_(*(getattr(TitleCredits,field).contains([{'id':member}]) for member in natives for field in fields))).exists())
            if kind!='actor':
                field='production_companies' if kind=='studio' else 'created_by'
                terms.append(select(Media.id).where(Media.media_type==media,
                    or_(*(Media.tmdb_data[field].contains([{'id':member}]) for member in natives))).exists())
        if terms:columns.append(or_(*terms).label(media))
    if columns:
        found=(await db.execute(select(*columns))).mappings().one()
        states={**states,**{media:'available' for media,present in found.items() if present}}
    return states


async def availability(db,viewer,*,kind,key):
    if kind in ('actor','staff'):require(viewer)
    resolved=await resolve_contributor(db,kind,key,include_identities=True)
    if resolved is None:return None
    entity,native,identities=resolved
    kinds=PERSON_ROLES if kind=='person' else (kind,)
    states,pending,retry=await movie_domains(db,viewer,kind,native,kinds)
    for mode in kinds:
        states[mode]=await preserve_local_movies(db,states[mode],entity,native,mode)
        states[mode]['game']='unknown'
    delays=[retry] if pending else []
    game_pending=False;source=None
    include_games=allowed(viewer)
    if entity and include_games:
        for mode in kinds:
            if await db.scalar(select(select(game_credits(entity.id,mode).c.id).exists())):
                states[mode]['game']='available'
        if kind=='studio':
            from core.game_company_works import status
            source=await status(db,entity.id,enqueue=True)
            game_pending=bool(source and source['pending'])
            if states[kind]['game']=='unknown' and source and source['complete'] and source['coverage']=='complete':
                states[kind]['game']='empty'
        else:
            from core.game_imdb_person import request,view,queue_projection
            admitted=await request(db,entity.id)
            if admitted:
                source=await view(db,admitted['id'],state=admitted,include_profile=False,check_mapping=True)
                game_pending=source['pending'] or source.get('identity_pending',False);coverage=source['mapping'];roles=coverage.get('role_coverage')
                if kind=='person' and not (source['complete'] and coverage.get('source_complete')) and any(
                        states[mode]['game']=='unknown' for mode in kinds):
                    # The live worker can be pending or in backoff. The local
                    # snapshot supplies positives independently of that state.
                    from core.game_imdb_snapshot import person_game_roles
                    for mode in await person_game_roles(db,admitted['id']):
                        states[mode]['game']='available'
                if kind=='person' and not native and not coverage.get('source_complete'):
                    # Movies & Series can resolve independently of an IMDb
                    # outage or a cold Games filmography.
                    from core.game_person_identity import request as request_identity
                    identity=await request_identity(db,admitted['id'],entity.id,None,enqueue=True)
                    if identity and identity['pending']:
                        delays.append(identity['retry_after'])
                if source['complete'] and not game_pending and coverage.get('source_complete'):
                    if roles is None:
                        game_pending=await queue_projection(db,admitted['id']) or game_pending
                    # A positive source connection with no displayed work may
                    # still have unresolved game type/parent metadata. Only an
                    # explicit zero for this role certifies absence here.
                    else:
                        for mode in kinds:
                            role=roles.get(mode)
                            if states[mode]['game']=='unknown' and role and role.get('source')==0 and role.get('unmapped')==0:
                                states[mode]['game']='empty'
                if kind=='person' and source.get('error'):
                    for mode in kinds:
                        if states[mode]['game']=='unknown':
                            # The display projection has no eligible credits,
                            # and the live source cannot confirm the career.
                            states[mode]['game']='unavailable'
    if game_pending:delays.append(max(3,min(60,(source or {}).get('retry_after',3))))
    resolved_key=f'catalogue:{entity.id}' if entity else key
    if kind=='person' and entity:
        await save_tabs(db,TAB_SNAPSHOT,str(entity.id),entity.id,states,include_games=include_games)
    elif kind=='studio':
        await save_tabs(db,STUDIO_TAB_SNAPSHOT,studio_tab_key(resolved_key),entity.id if entity else None,
            states,include_games=include_games)
    await db.commit()
    return {'key':resolved_key,
        **({'roles':states} if kind=='person' else {'domains':states[kind]}),
        'pending':bool(delays),'retry_after':max(3,min(delays,default=3))}
