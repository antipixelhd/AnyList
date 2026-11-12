"""Reviewed duplicate source titles contributing to an existing canonical game."""
import uuid
from urllib.parse import urlsplit

from sqlalchemy import String,literal,select
from sqlalchemy.dialects.postgresql import insert

from models.catalogue import CatalogueIdentity,MetadataSnapshot

KIND='game_credit_alias'


def alias_bindings(ids):
    """An alias is usable only while its reviewed primary identity still agrees."""
    from core.game_imdb_bindings import verified_bindings
    from core.game_credit_parents import KIND as PARENT_KIND
    primary=verified_bindings().subquery()
    parent=MetadataSnapshot.__table__.alias()
    query=select(MetadataSnapshot.external_id.label('imdb_id'),primary.c.work_id,
        primary.c.game_title,primary.c.igdb_id,literal(None,String).label('credit_kind'),
        literal(None,String).label('credit_title')).select_from(MetadataSnapshot).join(primary,
        (primary.c.work_id==MetadataSnapshot.entity_id)
        & (primary.c.imdb_id==MetadataSnapshot.payload['primary_title_id'].astext)).where(
        MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==KIND,
        MetadataSnapshot.coverage['reviewed'].astext=='true',
        ~select(CatalogueIdentity.id).where(CatalogueIdentity.namespace=='imdb.title',
            CatalogueIdentity.external_id==MetadataSnapshot.external_id).exists(),
        ~select(parent.c.id).where(parent.c.provider=='imdb',parent.c.kind==PARENT_KIND,
            parent.c.external_id==MetadataSnapshot.external_id).exists())
    return query.where(MetadataSnapshot.external_id.in_(ids)) if ids is not None else query


async def review_alias(db,title_id,work_id,*,primary_title_id,title,source_url,reviewer_id,reason,identities):
    """Explicit administrator review; never an automatic spelling-based merge."""
    from core import catalogue
    from core.game_imdb_source import identifier,game_header
    from core.game_imdb_bindings import verified_bindings
    from core.game_imdb_enrichment import now
    from core.game_mapping_lifecycle import revision
    from core.game_credit_parents import KIND as PARENT_KIND
    from models import User
    identifier(title_id);identifier(primary_title_id)
    reviewer=await db.get(User,reviewer_id)
    url=urlsplit(source_url)
    if (not reviewer or not reviewer.is_admin or title_id==primary_title_id
            or not isinstance(title,str) or not 1<=len(title.strip())<=500
            or not isinstance(reason,str) or not 3<=len(reason.strip())<=1000
            or url.scheme!='https' or not url.hostname):
        raise ValueError('A duplicate title, confirmed primary, public evidence and administrator review are required')
    if not isinstance(identities,dict):raise ValueError('Current source identity headers are required')
    headers={native:game_header(identities.get(native),native) for native in (title_id,primary_title_id)}
    if headers[title_id]['titleText']['text']!=title.strip():raise ValueError('The reviewed alias title changed')
    await catalogue.write_lock(db)
    primary=(await db.execute(verified_bindings([primary_title_id]))).one_or_none()
    existing=await db.scalar(select(CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.external_id==title_id))
    parent=await db.scalar(select(MetadataSnapshot.id).where(MetadataSnapshot.provider=='imdb',
        MetadataSnapshot.kind==PARENT_KIND,MetadataSnapshot.external_id==title_id))
    if not primary or primary.work_id!=work_id or existing is not None or parent is not None:
        raise ValueError('The primary must remain verified and the alias must have no conflicting binding or parent review')
    payload={'title':title.strip(),'primary_title_id':primary_title_id,'source_url':source_url,
        'reviewer_id':reviewer_id,'reason':reason.strip(),'identities':headers}
    previous=(await db.execute(select(MetadataSnapshot.entity_id,MetadataSnapshot.payload).where(MetadataSnapshot.provider=='imdb',
        MetadataSnapshot.kind==KIND,MetadataSnapshot.external_id==title_id))).one_or_none()
    if previous and previous.entity_id!=work_id:raise ValueError('A reviewed duplicate cannot be reassigned to another game')
    db.add(MetadataSnapshot(provider='imdb',kind='game_credit_alias_review',external_id=uuid.uuid4().hex,
        entity_id=work_id,payload={'previous':previous.payload if previous else None,'selection':payload},fetched_at=now()))
    statement=insert(MetadataSnapshot).values(provider='imdb',kind=KIND,external_id=title_id,
        entity_id=work_id,payload=payload,coverage={'reviewed':True},fetched_at=now())
    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',set_={
        'entity_id':statement.excluded.entity_id,'payload':statement.excluded.payload,
        'coverage':statement.excluded.coverage,'fetched_at':statement.excluded.fetched_at}))
    await revision(db)
