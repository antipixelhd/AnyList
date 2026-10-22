"""Batch existing external-ID evidence; never match contributor names."""
from collections import defaultdict

from sqlalchemy import or_, select

from core import catalogue
from core.catalogue_normalize import identity
from core.person_identities import merge_contributor_alias
from models.base import MediaType
from models.catalogue import CatalogueEntity, CatalogueIdentity
from models.media import Media


async def bridge_people(db,credits):
    natives={row['contributor']['identities'][0]['external_id'] for row in credits}
    if not natives:return
    # Read only identifier evidence, not stored biographies/filmographies.
    legacy=(await db.execute(select(Media.tmdb_id,Media.tvdb_id,Media.title,Media.imdb_id,
        Media.tmdb_data['imdb_id'].astext.label('json_imdb')).where(Media.media_type==MediaType.person,
        or_(Media.imdb_id.in_(natives),Media.tmdb_data['imdb_id'].astext.in_(natives))))).all()
    candidates=defaultdict(lambda:defaultdict(set))
    names={}
    for row in legacy:
        ids={value for value in (row.imdb_id,row.json_imdb) if value}
        if len(ids)!=1:continue
        imdb=next(iter(ids))
        for ns,native in [('tmdb.person',row.tmdb_id),('tvdb.person',row.tvdb_id)]:
            if native:
                candidates[imdb][ns].add(str(native))
                names[ns,str(native)]=row.title
    known=(await db.execute(select(CatalogueEntity.attributes['imdb_id'].astext,
        CatalogueIdentity.namespace,CatalogueIdentity.external_id).join(CatalogueIdentity,
        CatalogueIdentity.entity_id==CatalogueEntity.id).where(CatalogueEntity.kind=='person',
        CatalogueEntity.attributes['imdb_id'].astext.in_(natives),
        CatalogueEntity.field_sources['attributes.imdb_id'].astext.in_(('tmdb','tvdb','reviewed','legacy')),
        CatalogueIdentity.namespace.in_(('tmdb.person','tvdb.person'))))).all()
    for imdb,namespace,native in known:candidates[imdb][namespace].add(native)
    conflicts=set()
    for imdb,providers in candidates.items():
        if any(len(values)!=1 for values in providers.values()):
            conflicts.add(imdb)
            continue
        namespace='tmdb.person' if 'tmdb.person' in providers else 'tvdb.person'
        native=next(iter(providers[namespace]))
        # A contradictory identity or protected edit is reviewable. Isolate
        # it without losing all of the game's other verified credit entries.
        try:
            async with db.begin_nested():
                existing=await catalogue.resolve_identity(db,namespace,native)
                if existing:
                    root=await db.get(CatalogueEntity,existing.entity_id)
                    if root.canonical_person_id:root=await db.get(CatalogueEntity,root.canonical_person_id)
                else:
                    root=await catalogue.upsert_entity(db,{'kind':'person',
                        'identities':[identity(namespace,native)],'name':names.get((namespace,native)),
                        'attributes':{}},'legacy')
                if not root or root.kind!='person':raise catalogue.IdentityConflict('Person identity requires review')
                previous=await catalogue.resolve_identity(db,'imdb.person',imdb)
                if previous and previous.entity_id!=root.id:
                    alias=await db.get(CatalogueEntity,previous.entity_id)
                    if alias.canonical_person_id:alias=await db.get(CatalogueEntity,alias.canonical_person_id)
                    if alias.id!=root.id:
                        await merge_contributor_alias(db,alias,root,'imdb','person','canonical_person_id')
                if not previous:
                    await catalogue.bind_identity(db,root.id,'imdb.person',imdb,'imdb',{
                        'verified_external_identifier':{'namespace':namespace,'external_id':native},
                        'evidence_source':'existing_provider_person_metadata'})
        except catalogue.IdentityConflict:
            conflicts.add(imdb)
    for row in credits:
        if row['contributor']['identities'][0]['external_id'] in conflicts:
            row['attributes']['person_identity_review_required']=True
