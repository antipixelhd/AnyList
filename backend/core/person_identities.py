"""Bridge people only through authoritative TVDB-to-TMDB identifiers."""
from sqlalchemy import select, update
from models.catalogue import CatalogueEntity, CatalogueIdentity


def tvdb_tmdb_person(raw):
    ids = {str(row.get('id')) for row in raw.get('remoteIds') or []
           if row.get('sourceName') == 'TheMovieDB.com' and str(row.get('id', '')).isdecimal()
           and 0 < int(row['id']) <= 2147483647}
    return next(iter(ids)) if len(ids) == 1 else None


async def canonical_people(db, people):
    ids = {person.canonical_person_id for person in people if getattr(person, 'canonical_person_id', None)}
    roots = {person.id: person for person in await db.scalars(select(CatalogueEntity).where(CatalogueEntity.id.in_(ids)))} if ids else {}
    return {person.id: roots.get(getattr(person, 'canonical_person_id', None), person) for person in people}


async def reconcile_person_identities(db, identities, source):
    from core import catalogue
    if source != 'tvdb':
        return
    pairs = {i['namespace']: str(i['external_id']) for i in identities}
    if set(pairs) != {'tvdb.person', 'tmdb.person'} or len(identities) != 2:
        return
    for namespace, native in pairs.items():
        catalogue.validate_identity(namespace, native, 'person')
    await catalogue.write_lock(db)
    mappings = {i.namespace: i for i in await db.scalars(select(CatalogueIdentity).where(
        ((CatalogueIdentity.namespace == 'tvdb.person') & (CatalogueIdentity.external_id == pairs['tvdb.person'])) |
        ((CatalogueIdentity.namespace == 'tmdb.person') & (CatalogueIdentity.external_id == pairs['tmdb.person']))))}
    tvdb = mappings.get('tvdb.person')
    tmdb = mappings.get('tmdb.person')
    if not tvdb or not tmdb or tvdb.entity_id == tmdb.entity_id:
        return
    alias = await db.get(CatalogueEntity, tvdb.entity_id)
    root = await db.get(CatalogueEntity, tmdb.entity_id)
    if alias.kind != 'person' or root.kind != 'person' or alias.canonical_person_id or root.canonical_person_id:
        raise catalogue.IdentityConflict('Person bridge has an invalid root')
    identifiers = list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id.in_((alias.id, root.id)))))
    for namespace in {i.namespace for i in identifiers}:
        if len({i.external_id for i in identifiers if i.namespace == namespace}) > 1:
            raise catalogue.IdentityConflict('Person bridge contradicts existing provider identifiers')
    # Preserve edited fields rather than silently choosing between incompatible edits.
    for field in set(alias.protected_fields or []) | set(root.protected_fields or []):
        def value(person):
            return (person.attributes or {}).get(field[11:]) if field.startswith('attributes.') else getattr(person, field, None)
        if field in (alias.protected_fields or []) and field in (root.protected_fields or []) and value(alias) != value(root):
            raise catalogue.IdentityConflict('Person bridge has conflicting protected fields')
        if field in (alias.protected_fields or []):
            if field.startswith('attributes.'):
                root.attributes = {**(root.attributes or {}), field[11:]: value(alias)}
            else:
                setattr(root, field, value(alias))
            root.protected_fields = sorted(set(root.protected_fields or []) | {field})
    values = {field: getattr(alias, field) for field in ('description', 'image_url') if not catalogue.has_value(getattr(root, field))}
    values['attributes'] = {key: value for key, value in (alias.attributes or {}).items() if not catalogue.has_value((root.attributes or {}).get(key))}
    catalogue.merge_fields(root, values, 'tvdb')
    alias.canonical_person_id = root.id
    await db.execute(update(CatalogueEntity).where(CatalogueEntity.canonical_person_id == alias.id).values(canonical_person_id=root.id))
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
