"""Bridge contributors only through explicit authoritative provider identifiers."""
import re
from sqlalchemy import select, update
from models.catalogue import CatalogueEntity, CatalogueIdentity


def tvdb_tmdb_person(raw):
    ids = {str(row.get('id')) for row in raw.get('remoteIds') or []
           if row.get('sourceName') == 'TheMovieDB.com' and str(row.get('id', '')).isdecimal()
           and 0 < int(row['id']) <= 2147483647}
    return next(iter(ids)) if len(ids) == 1 else None


async def canonical_people(db, people, *, lightweight=False):
    def target(person):
        return getattr(person, 'canonical_person_id', None) or getattr(person, 'canonical_company_id', None)
    ids = {target(person) for person in people if target(person)}
    statement = select(CatalogueEntity).where(CatalogueEntity.id.in_(ids))
    if lightweight:
        from sqlalchemy.orm import load_only
        statement = statement.options(load_only(CatalogueEntity.id,CatalogueEntity.kind,CatalogueEntity.name,
            CatalogueEntity.image_url,CatalogueEntity.canonical_person_id,CatalogueEntity.canonical_company_id))
    roots = {person.id: person for person in await db.scalars(statement)} if ids else {}
    return {person.id: roots.get(target(person), person) for person in people}


async def reconcile_person_identities(db, identities, source):
    if source=='wikidata':
        await reconcile_wikidata_person(db,identities)
        return
    if source=='tmdb':
        pairs={item['namespace']:str(item['external_id']) for item in identities}
        if len(pairs)!=len(identities) or set(pairs)!={'tmdb.person','imdb.person'}:return
        from core import catalogue
        for namespace,native in pairs.items():catalogue.validate_identity(namespace,native,'person')
        await catalogue.write_lock(db)
        mappings={row.namespace:row for row in await db.scalars(select(CatalogueIdentity).where(
            ((CatalogueIdentity.namespace=='tmdb.person')&(CatalogueIdentity.external_id==pairs['tmdb.person']))|
            ((CatalogueIdentity.namespace=='imdb.person')&(CatalogueIdentity.external_id==pairs['imdb.person']))))}
        tmdb,imdb=mappings.get('tmdb.person'),mappings.get('imdb.person')
        if tmdb and imdb and tmdb.entity_id!=imdb.entity_id:
            await merge_contributor_alias(db,await db.get(CatalogueEntity,imdb.entity_id),
                await db.get(CatalogueEntity,tmdb.entity_id),'tmdb','person','canonical_person_id')
        return
    await reconcile_contributor_identities(db, identities, source, 'person', 'person', 'canonical_person_id')


async def reconcile_company_identities(db, identities, source):
    await reconcile_contributor_identities(db, identities, source, 'organization', 'company', 'canonical_company_id')


async def reconcile_wikidata_person(db,identities):
    from core import catalogue
    pairs={item['namespace']:str(item['external_id']) for item in identities}
    if len(pairs)!=len(identities) or 'wikidata.item' not in pairs or not set(pairs)<={'wikidata.item','tmdb.person','imdb.person'}:
        return
    for namespace,native in pairs.items():catalogue.validate_identity(namespace,native,'person')
    await catalogue.write_lock(db)
    from sqlalchemy import tuple_
    mappings=list((await db.execute(select(CatalogueIdentity,CatalogueEntity).join(
        CatalogueEntity,CatalogueEntity.id==CatalogueIdentity.entity_id).where(
            tuple_(CatalogueIdentity.namespace,CatalogueIdentity.external_id).in_(list(pairs.items()))))).all())
    roots={identity.namespace:person for identity,person in mappings}
    root=next((roots[namespace] for namespace in ('tmdb.person','imdb.person','wikidata.item') if namespace in roots),None)
    if root is None:return
    for alias in {person.id:person for _,person in mappings}.values():
        if alias.id!=root.id:await merge_contributor_alias(db,alias,root,'wikidata','person','canonical_person_id')


async def reconcile_contributor_identities(db, identities, source, kind, namespace_kind, alias_field):
    from core import catalogue
    if source != 'tvdb':
        return
    pairs = {i['namespace']: str(i['external_id']) for i in identities}
    tvdb_ns, tmdb_ns = f'tvdb.{namespace_kind}', f'tmdb.{namespace_kind}'
    if set(pairs) != {tvdb_ns, tmdb_ns} or len(identities) != 2:
        return
    for namespace, native in pairs.items():
        catalogue.validate_identity(namespace, native, kind)
    await catalogue.write_lock(db)
    mappings = {i.namespace: i for i in await db.scalars(select(CatalogueIdentity).where(
        ((CatalogueIdentity.namespace == tvdb_ns) & (CatalogueIdentity.external_id == pairs[tvdb_ns])) |
        ((CatalogueIdentity.namespace == tmdb_ns) & (CatalogueIdentity.external_id == pairs[tmdb_ns]))))}
    tvdb = mappings.get(tvdb_ns)
    tmdb = mappings.get(tmdb_ns)
    if not tvdb or not tmdb or tvdb.entity_id == tmdb.entity_id:
        return
    alias = await db.get(CatalogueEntity, tvdb.entity_id)
    root = await db.get(CatalogueEntity, tmdb.entity_id)
    await merge_contributor_alias(db,alias,root,source,kind,alias_field)


async def merge_contributor_alias(db,alias,root,source,kind,alias_field):
    from core import catalogue
    if alias.kind != kind or root.kind != kind or getattr(alias, alias_field) or getattr(root, alias_field):
        raise catalogue.IdentityConflict('Contributor bridge has an invalid root')
    identifiers = list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id.in_((alias.id, root.id)))))
    for namespace in {i.namespace for i in identifiers}:
        if len({i.external_id for i in identifiers if i.namespace == namespace}) > 1:
            raise catalogue.IdentityConflict('Contributor bridge contradicts existing provider identifiers')
    # Preserve edited fields rather than silently choosing between incompatible edits.
    for field in set(alias.protected_fields or []) | set(root.protected_fields or []):
        def value(person):
            return (person.attributes or {}).get(field[11:]) if field.startswith('attributes.') else getattr(person, field, None)
        if field in (alias.protected_fields or []) and field in (root.protected_fields or []) and value(alias) != value(root):
            raise catalogue.IdentityConflict('Contributor bridge has conflicting protected fields')
        if field in (alias.protected_fields or []):
            if field.startswith('attributes.'):
                root.attributes = {**(root.attributes or {}), field[11:]: value(alias)}
            else:
                setattr(root, field, value(alias))
            root.protected_fields = sorted(set(root.protected_fields or []) | {field})
    values = {field: getattr(alias, field) for field in ('description', 'image_url') if not catalogue.has_value(getattr(root, field))}
    values['attributes'] = {key: value for key, value in (alias.attributes or {}).items() if not catalogue.has_value((root.attributes or {}).get(key))}
    catalogue.merge_fields(root, values, source)
    if (kind == 'organization' or source=='wikidata') and 'attributes.also_known_as' not in (root.protected_fields or []):
        root.attributes = {**(root.attributes or {}), 'also_known_as': sorted(set(
            [alias.name, *(alias.attributes or {}).get('also_known_as', []),
             *(root.attributes or {}).get('also_known_as', [])]))}
    setattr(alias, alias_field, root.id)
    await db.execute(update(CatalogueEntity).where(getattr(CatalogueEntity, alias_field) == alias.id).values(**{alias_field: root.id}))
    await db.execute(update(CatalogueIdentity).where(CatalogueIdentity.entity_id == alias.id).values(entity_id=root.id))
    await db.flush()


async def verified_tvdb_person(db, native, raw):
    from core import catalogue
    if str(raw.get('id')) != str(native):
        raise catalogue.IdentityConflict('Provider person identity mismatch')
    tmdb = tvdb_tmdb_person(raw)
    if not tmdb:
        return False
    identities = [{'namespace': 'tvdb.person', 'external_id': str(native)}, {'namespace': 'tmdb.person', 'external_id': tmdb}]
    await reconcile_person_identities(db, identities, 'tvdb')
    mapping = await catalogue.resolve_identity(db, 'tvdb.person', str(native))
    if mapping:
        await catalogue.bind_identity(db, mapping.entity_id, 'tmdb.person', tmdb, 'tvdb', {'tvdb_person': str(native)})
    return bool(mapping)


async def verified_tmdb_person(db,native,raw):
    """Reuse an already requested provider profile, never a name-search match."""
    from core import catalogue
    if not isinstance(raw,dict) or str(raw.get('id'))!=str(native):
        raise catalogue.IdentityConflict('Provider person identity mismatch')
    imdb=raw.get('imdb_id');name=raw.get('name')
    if not isinstance(imdb,str) or not re.fullmatch(r'nm[0-9]+',imdb) or not isinstance(name,str) or not name.strip():return None
    identities=[{'namespace':'tmdb.person','external_id':str(native)},
        {'namespace':'imdb.person','external_id':imdb}]
    path=raw.get('profile_path')
    artwork='https://image.tmdb.org/t/p/w185'+path if isinstance(path,str) and re.fullmatch(r'/[A-Za-z0-9_.-]+',path) else None
    async with db.begin_nested():
        await catalogue.write_lock(db)
        row=await catalogue.upsert_entity(db,{'kind':'person','name':name,'identities':identities,
            **({'image_url':artwork} if artwork else {}),'attributes':{'imdb_id':imdb}},'tmdb')
    return row
