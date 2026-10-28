"""Compact cached title references for explicit admin review, not another catalogue."""
import asyncio

from sqlalchemy import Integer, Text, and_, column, delete, func, select, true
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import aliased

from core import catalogue
from core.game_imdb_enrichment import now,snapshot,request as admit
from core.game_imdb_source import identifier
from core.game_imdb_bindings import verified_bindings,BINDING_REVISION
from models.catalogue import CatalogueEntity,CatalogueIdentity,MetadataSnapshot

INDEX='game_person_index'
SIZE=50


class ReviewChanged(ValueError):
    pass


def title_index(rows):
    titles={}
    for position,row in enumerate(rows):
        title=row.get('title') or {}
        if (title.get('titleType') or {}).get('id')!='videoGame':continue
        native=title['id'];role=row['category']['id']
        name=(title.get('titleText') or {}).get('text')
        year=(title.get('releaseYear') or {}).get('year')
        record=titles.setdefault(native,{'id':native,'title':native,'year':None,'roles':[],'position':position})
        if isinstance(name,str) and name.strip() and record['title']==native:record['title']=name.strip()[:500]
        if type(year) is int and 1<=year<=9999 and record['year'] is None:record['year']=year
        if role not in record['roles']:record['roles'].append(role)
    return list(titles.values())


async def write_index(db,native,generation,rows,*,entity_id):
    titles=await asyncio.to_thread(title_index,rows)
    pages=(len(titles)+SIZE-1)//SIZE
    for page in range(pages):
        statement=insert(MetadataSnapshot).values(provider='imdb',kind=INDEX,external_id=f'{native}:{generation}:{page}',
            entity_id=entity_id,payload={'titles':titles[page*SIZE:(page+1)*SIZE]},coverage={'page':page},fetched_at=now())
        await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',
            set_={'payload':statement.excluded.payload,'fetched_at':statement.excluded.fetched_at}))
    await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==INDEX,
        MetadataSnapshot.external_id.like(f'{native}:%'),~MetadataSnapshot.external_id.like(f'{native}:{generation}:%')))
    return {'index_generation':generation,'index_pages':pages,'index_titles':len(titles)}


async def review_titles(db,native,*,page=1,query='',scope='unmapped',generation=None,title_id=None,bindings=None):
    from core.game_imdb_person import JOB,COMPONENT,queue_projection
    from core.game_imdb_discovery import MAP,VERSION as MATCH_VERSION
    identifier(native,person=True)
    if title_id:identifier(title_id)
    mapping=await catalogue.resolve_identity(db,'imdb.person',native)
    if not mapping:return None
    person=await db.get(CatalogueEntity,mapping.entity_id)
    if not person or person.kind!='person':return None
    if person.canonical_person_id:person=await db.get(CatalogueEntity,person.canonical_person_id)
    if not person or person.kind!='person':return None
    header=(await db.execute(snapshot(COMPONENT,native).with_only_columns(
        MetadataSnapshot.payload['generation'].astext.label('generation'),
        MetadataSnapshot.payload['index_generation'].astext.label('indexed'),MetadataSnapshot.coverage))).one_or_none()
    if generation and header and header.generation!=generation:raise ReviewChanged('Credits changed. Refresh this review.')
    state=await admit(db,native,kind=JOB,entity_id=person.id)
    ready=bool(header and header.generation and header.coverage.get('source_complete') and header.generation==header.indexed)
    if not ready and header and header.coverage.get('source_complete') and not state['pending']:
        if await queue_projection(db,native):state={**state,'pending':True,'complete':False,'state':'pending'}
    await db.commit()
    result={'person':{'id':person.id,'native':native,'name':person.name},'status':{'id':native,**state},
        'generation':header.generation if header else None,'ready':ready,'page':page,'total':None,'has_more':False,'results':[]}
    if not ready:return result
    entries=func.jsonb_to_recordset(MetadataSnapshot.payload['titles']).table_valued(
        column('id',Text),column('title',Text),column('year',Integer),column('roles',JSONB),column('position',Integer)
    ).render_derived(name='review_title',with_types=True).lateral()
    from core.game_credit_parents import person_bindings
    valid=person_bindings(None)
    prior=select(CatalogueIdentity.external_id.label('imdb_id'),CatalogueIdentity.entity_id.label('review_work_id')).join(
        CatalogueEntity,CatalogueEntity.id==CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace=='imdb.title',CatalogueEntity.kind=='game').subquery('existing_binding')
    classified=aliased(MetadataSnapshot)
    query_rows=select(entries.c.id.label('imdb_id'),entries.c.title,entries.c.year,entries.c.roles,entries.c.position,
        valid.c.work_id,valid.c.game_title,valid.c.igdb_id,prior.c.review_work_id,
        classified.payload['status'].astext.label('match_status'),classified.payload['reason'].astext.label('match_reason'),
        classified.payload['igdb_id'].astext.label('match_igdb'),classified.payload['game_title'].astext.label('match_title')).select_from(MetadataSnapshot).join(entries,true()).outerjoin(
            valid,valid.c.imdb_id==entries.c.id).outerjoin(prior,prior.c.imdb_id==entries.c.id).outerjoin(classified,and_(
                classified.provider=='wikidata',classified.kind==MAP,classified.external_id==entries.c.id,
                classified.expires_at>now(),classified.payload['matcher_version'].as_integer()==MATCH_VERSION)).where(
        MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==INDEX,
        MetadataSnapshot.external_id.like(f'{native}:{header.generation}:%'))
    if title_id:query_rows=query_rows.where(entries.c.id==title_id)
    elif scope=='unmapped':query_rows=query_rows.where(valid.c.work_id.is_(None))
    if query:
        literal=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        query_rows=query_rows.where(entries.c.title.ilike(f'%{literal}%',escape='\\'))
    eligible=query_rows.cte('review_games')
    total=select(func.count().label('total')).select_from(eligible).subquery('review_count')
    portion=select(eligible).order_by(eligible.c.position,eligible.c.imdb_id).offset((page-1)*SIZE).limit(SIZE).subquery('review_page')
    current=aliased(MetadataSnapshot)
    current_generation=select(current.payload['generation'].astext).where(current.provider=='imdb',current.kind==COMPONENT,
        current.external_id==native).scalar_subquery()
    current_bindings=select(current.coverage['revision'].astext).where(current.provider=='imdb',current.kind==BINDING_REVISION,
        current.external_id=='current').scalar_subquery()
    rows=(await db.execute(select(current_generation.label('generation'),current_bindings.label('bindings'),total.c.total,*portion.c).select_from(
        total.outerjoin(portion,true())))).mappings().all()
    # The count/page and current source version share one database snapshot.
    # Never return an empty page caused by a concurrent generation replacement.
    if not rows or rows[0]['generation']!=header.generation:raise ReviewChanged('Credits changed. Refresh this review.')
    result['bindings']=rows[0]['bindings'] or 'none'
    if bindings is not None and bindings!=result['bindings']:raise ReviewChanged('Game mappings changed. Refresh this review.')
    result['total']=rows[0]['total']
    result['results']=[]
    for source in rows:
        if not source['imdb_id']:continue
        row={key:source[key] for key in portion.c.keys() if key!='position' and not key.startswith('match_')}
        row['status']='bound' if row['work_id'] else 'review_required' if row['review_work_id'] else 'unmapped'
        if row['status']=='unmapped' and source['match_status']=='excluded':
            row.update(status='excluded',reason=source['match_reason'],igdb_id=source['match_igdb'],game_title=source['match_title'])
        result['results'].append(row)
    result['has_more']=(page-1)*SIZE+len(result['results'])<result['total']
    return result
