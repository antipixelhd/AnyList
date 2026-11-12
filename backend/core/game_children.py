"""Verified playable children, with bounded metadata and viewer-only state."""
from fastapi import HTTPException
from datetime import datetime,timezone
from sqlalchemy import case,func,select,or_
from sqlalchemy.orm import aliased

from core.game_tracking import work,states
from core.game_types import capabilities,NONPLAYABLE_DLC_NAME
from models.catalogue import CatalogueEntity,CatalogueRelationship,MetadataSnapshot


async def children(db,work_id,user_id,*,offset=0,limit=24):
    base=await work(db,work_id)
    if not capabilities(base.attributes).independent:
        raise HTTPException(422,'An independently playable base game is required')
    other=aliased(CatalogueRelationship)
    kind=CatalogueEntity.attributes['game_class'].astext
    # A relation field alone is not enough: IGDB dlcs can contain packs. Match
    # the child's explicit type and require one unambiguous base relationship.
    unique_base=select(func.count(func.distinct(other.target_id))).where(
        other.source_id==CatalogueEntity.id,other.relation==kind,
        other.attributes['source_active'].astext.is_distinct_from('false')).correlate(CatalogueEntity).scalar_subquery()
    eligible=select(CatalogueEntity.id).join(CatalogueRelationship,CatalogueRelationship.source_id==CatalogueEntity.id).where(
        CatalogueEntity.kind=='game',kind.in_(('expansion','dlc')),
        or_(kind=='expansion',CatalogueEntity.name.op('!~*')(NONPLAYABLE_DLC_NAME)),
        CatalogueRelationship.target_id==work_id,CatalogueRelationship.relation==kind,
        CatalogueRelationship.attributes['source_active'].astext.is_distinct_from('false'),
        unique_base==1).distinct().subquery()
    total=await db.scalar(select(func.count()).select_from(eligible))
    complete=bool(await db.scalar(select(MetadataSnapshot.id).where(MetadataSnapshot.entity_id==work_id,
        MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',
        MetadataSnapshot.error_code.is_(None),MetadataSnapshot.expires_at>datetime.now(timezone.utc).replace(tzinfo=None),
        MetadataSnapshot.coverage['children_complete'].as_boolean().is_(True)).limit(1)))
    rows=list(await db.scalars(select(CatalogueEntity).where(CatalogueEntity.id.in_(select(eligible.c.id))).order_by(
        case((kind=='expansion',0),else_=1),CatalogueEntity.attributes['release_date'].astext.asc().nullslast(),
        CatalogueEntity.id).offset(offset).limit(limit)))
    personal={state.subject.catalogue_id:state for state in await states(db,user_id,[row.id for row in rows])}
    results=[]
    for row in rows:
        state=personal[row.id];state.base_work_id=work_id
        results.append({'id':row.id,'type':'game','subject':state.subject.model_dump(),
            'title':row.name,'poster':row.image_url,'overview':row.description,
            'release_date':(row.attributes or {}).get('release_date'),
            'game_class':capabilities(row.attributes).game_class,'base_work_id':work_id,
            'game_state':state.model_dump(mode='json'),'entry':state.model_dump(mode='json') if state.listed else None})
    return {'results':results,'total':total,'coverage':'complete' if complete else 'unknown',
        'offset':offset,'limit':limit,'has_more':offset+len(results)<total}
