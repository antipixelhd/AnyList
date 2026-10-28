"""Reviewed child participation when a provider has no separate IGDB work yet."""
from sqlalchemy import select,literal,String,func
from sqlalchemy.dialects.postgresql import insert
from core.game_types import CREDIT_CHILDREN,INDEPENDENT
from models.catalogue import CatalogueEntity,CatalogueIdentity,MetadataSnapshot

KIND='game_credit_parent'


def person_bindings(ids):
    from core.game_imdb_bindings import verified_bindings
    verified=verified_bindings(ids).subquery()
    direct=select(verified.c.imdb_id,verified.c.work_id,verified.c.game_title,verified.c.igdb_id,
        literal(None,String).label('credit_kind'),literal(None,String).label('credit_title'))
    igdb=select(CatalogueIdentity.external_id).where(CatalogueIdentity.entity_id==CatalogueEntity.id,
        CatalogueIdentity.namespace=='igdb.game').order_by(CatalogueIdentity.id).limit(1).correlate(CatalogueEntity).scalar_subquery()
    reviewed=select(MetadataSnapshot.external_id.label('imdb_id'),MetadataSnapshot.entity_id.label('work_id'),
        CatalogueEntity.name.label('game_title'),func.coalesce(CatalogueEntity.attributes['igdb_id'].astext,igdb).label('igdb_id'),
        MetadataSnapshot.payload['kind'].astext.label('credit_kind'),MetadataSnapshot.payload['title'].astext.label('credit_title')).join(
        CatalogueEntity,CatalogueEntity.id==MetadataSnapshot.entity_id).where(
        MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==KIND,
        MetadataSnapshot.payload['kind'].astext.in_(CREDIT_CHILDREN),
        CatalogueEntity.kind=='game',CatalogueEntity.attributes['game_class'].astext.in_(INDEPENDENT-{'port'}),
        select(CatalogueIdentity.id).where(CatalogueIdentity.entity_id==CatalogueEntity.id,
            CatalogueIdentity.namespace=='igdb.game').exists(),
        ~select(verified.c.imdb_id).where(verified.c.imdb_id==MetadataSnapshot.external_id).exists())
    if ids is not None:reviewed=reviewed.where(MetadataSnapshot.external_id.in_(ids))
    return direct.union_all(reviewed).cte('person_game_bindings')


async def review_parent(db,title_id,parent_id,*,title,kind,source_url,reviewer_id):
    from urllib.parse import urlsplit
    from core import catalogue
    from core.game_imdb_source import identifier
    from core.game_imdb_enrichment import now
    from core.game_mapping_lifecycle import revision
    from models import User
    identifier(title_id)
    url=urlsplit(source_url)
    reviewer=await db.get(User,reviewer_id)
    if kind not in CREDIT_CHILDREN or not title.strip() or url.scheme!='https' or not url.hostname or not reviewer or not reviewer.is_admin:
        raise ValueError('A reviewed child title, public evidence URL and administrator are required')
    await catalogue.write_lock(db)
    parent=await db.get(CatalogueEntity,parent_id)
    if not parent or parent.kind!='game' or (parent.attributes or {}).get('game_class') not in INDEPENDENT-{'port'}:
        raise ValueError('An independent parent game is required')
    if not await db.scalar(select(CatalogueIdentity.id).where(CatalogueIdentity.entity_id==parent_id,CatalogueIdentity.namespace=='igdb.game')):
        raise ValueError('The parent must have an IGDB identity')
    statement=insert(MetadataSnapshot).values(provider='imdb',kind=KIND,external_id=title_id,entity_id=parent_id,
        payload={'title':title.strip(),'kind':kind,'source_url':source_url,'reviewer_id':reviewer_id},
        coverage={'reviewed':True},fetched_at=now())
    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',set_={
        'entity_id':statement.excluded.entity_id,'payload':statement.excluded.payload,'coverage':statement.excluded.coverage,
        'fetched_at':statement.excluded.fetched_at}))
    await revision(db)
