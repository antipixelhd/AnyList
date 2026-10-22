"""Small, source-verified domain availability; a missing mapping is not empty."""
import hashlib
import json

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from core import contributor_evidence as evidence
from core.contributor_details import resolve_contributor, positive_id
from core.credit_roles import work_credit
from core.game_access import require
from core.game_contributor_works import game_credits
from core.statistics_staff import ACTING
from core.studio_groups import studio_companies, studio_root, studio_members, studio_entity_ids
from models import Media
from models.catalogue import ContributorEvidence, CatalogueEntity, CatalogueCredit
from models.title_credits import TitleCredits


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


async def movie_domains(db,viewer,kind,native):
    unknown={'movie':'unknown','series':'unknown'}
    if not native:return unknown,False,3
    key,config=movie_job(kind,native);table=ContributorEvidence;stamp=evidence.now()
    statement=select(table.payload['domain_availability'].label('domains'),table.expires_at,
        table.next_attempt_at,table.lease_until,table.attempts).where(
        table.kind==key[0],table.native_id==key[1],table.variant==key[2])
    row=(await db.execute(statement)).one_or_none()
    fresh=bool(row and row.expires_at and row.expires_at>stamp and not row.attempts
        and not row.next_attempt_at and not (row.lease_until and row.lease_until>stamp))
    summary=(row.domains or {}).get(kind) if row else None
    states={media:value if value in ('available','empty') and (fresh or value=='available') else 'unknown'
        for media,value in (summary or unknown).items() if media in unknown}
    states={**unknown,**states}
    pending=bool(row and (row.next_attempt_at or row.lease_until and row.lease_until>stamp))
    due=not row or not pending and (not row.expires_at or row.expires_at<=stamp or summary is None and not row.attempts)
    delay=max(3,int((row.next_attempt_at-stamp).total_seconds())+1) if row and row.next_attempt_at else 3
    if due:pending=True
    if due and await db.scalar(select(func.pg_try_advisory_xact_lock(724052))):
        count=await db.scalar(select(func.count()).select_from(table).where(table.next_attempt_at.is_not(None)))
        if count<evidence.MAX_PENDING:
            write=insert(table).values(kind=key[0],native_id=key[1],variant=key[2],config=config,
                requested_by=viewer.id,next_attempt_at=stamp)
            await db.execute(write.on_conflict_do_update(index_elements=['kind','native_id','variant'],
                set_={'next_attempt_at':stamp,'requested_by':viewer.id},where=table.next_attempt_at.is_(None)
                & or_(table.lease_until.is_(None),table.lease_until<=stamp)
                & or_(table.expires_at.is_(None),table.expires_at<=stamp,
                    table.payload['domain_availability'][kind].is_(None) & (table.attempts==0))))
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
    # This response includes Games evidence even on a Movies & Series page.
    require(viewer)
    resolved=await resolve_contributor(db,kind,key,include_identities=True)
    if resolved is None:return None
    entity,native,identities=resolved
    states,pending,retry=await movie_domains(db,viewer,kind,native)
    states=await preserve_local_movies(db,states,entity,native,kind)
    delays=[retry] if pending else []
    games='unknown';game_pending=False;source=None
    if entity:
        exists=bool(await db.scalar(select(select(game_credits(entity.id,kind).c.id).exists())))
        if exists:games='available'
        if kind=='studio':
            from core.game_company_works import status
            source=await status(db,entity.id,enqueue=True)
            game_pending=bool(source and source['pending'])
            if not exists and source and source['complete'] and source['coverage']=='complete':games='empty'
        else:
            from core.game_imdb_person import request,view,queue_projection
            admitted=await request(db,entity.id)
            if admitted:
                source=await view(db,admitted['id'],state=admitted,include_profile=False,check_mapping=True)
                game_pending=source['pending'];coverage=source['mapping'];roles=coverage.get('role_coverage')
                if source['complete'] and not source['pending'] and coverage.get('source_complete'):
                    if roles is None:
                        game_pending=await queue_projection(db,admitted['id']) or game_pending
                    # A positive source connection with no displayed work may
                    # still have unresolved game type/parent metadata. Only an
                    # explicit zero for this role certifies absence here.
                    elif not exists and (role:=roles.get(kind)) and role.get('source')==0 and role.get('unmapped')==0:
                        games='empty'
    if game_pending:delays.append(max(3,min(60,(source or {}).get('retry_after',3))))
    await db.commit()
    return {'key':f'catalogue:{entity.id}' if entity else key,'domains':{**states,'game':games},
        'pending':pending or game_pending,'retry_after':min(delays,default=3)}
