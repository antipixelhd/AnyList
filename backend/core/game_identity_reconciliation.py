"""Exact IGDB/Steam bridges, preserving the imported root and private facts.

Root aliases are permanent catalogue records in the existing durable mapping
store, like reviewed credit aliases; they are never provider-cache entries.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select, tuple_, update

from models import Base, UserProfileData
from models.catalogue import (
    CatalogueEntity, CatalogueIdentity, CatalogueCredit, CharacterAppearance,
    CharacterPerformance, CatalogueRelationship, MetadataSnapshot,
)

ALIAS = 'game_root_alias'


async def alias_target(db, work_id):
    return await db.scalar(select(MetadataSnapshot.entity_id).where(
        MetadataSnapshot.provider == 'catalogue', MetadataSnapshot.kind == ALIAS,
        MetadataSnapshot.external_id == str(work_id)))


async def discovery_identities(db, values):
    """Keep a verified legacy Steam bridge in a sparse discovery document.

    An app already attached to a different IGDB game is not an equivalence:
    IGDB can repeat store references on ports/editions. Preserve that candidate
    for review, without moving an established canonical identity.
    """
    from core import catalogue
    primary = [i for i in values['identities'] if i['namespace'] == 'igdb.game']
    if len(primary) != 1:
        raise catalogue.IdentityConflict('One explicit IGDB game root is required')
    candidates = [i for i in values['identities'] if i not in primary]
    projected = {**values, 'identities': primary, 'attributes': {**values.get('attributes', {}),
        **({'source_identity_candidates': candidates} if candidates else {})}}
    steam = [i for i in candidates if i['namespace'] == 'steam.app']
    from core.game_types import capabilities
    if not steam or not capabilities(values.get('attributes')).independent:
        return projected
    apps = [catalogue.validate_identity('steam.app', i['external_id'], 'game') for i in steam]
    native = CatalogueIdentity.__table__.alias('native')
    mappings = (await db.execute(select(CatalogueIdentity.external_id, CatalogueIdentity.entity_id,
        native.c.external_id.label('igdb_id')).outerjoin(native,
            (native.c.entity_id == CatalogueIdentity.entity_id) & (native.c.namespace == 'igdb.game')).where(
                CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id.in_(apps)))).all()
    # Already canonical roots are found through the primary IGDB identity.
    # Only still-legacy roots need the additional reconciliation boundary.
    eligible = {row.entity_id for row in mappings if row.igdb_id is None}
    if len(eligible) > 1:
        raise catalogue.IdentityConflict('Steam references identify multiple imported roots')
    if eligible:
        existing = await catalogue.resolve_identity(db, 'igdb.game', primary[0]['external_id'])
        if existing and existing.entity_id not in eligible:
            # Both records already exist: leave their reconciliation to the
            # explicit admin scan. Discovery keeps the established IGDB root.
            return projected
    projected['identities'] = [*primary, *[i for i in steam if any(
        m.external_id == str(i['external_id']) and m.entity_id in eligible for m in mappings)]]
    return projected


async def reconcile(db, identities, source, attributes=None):
    """Manual repair only: require an exact IGDB-supplied bridge.

    Normal catalogue/discovery writes never invoke this merge boundary.
    """
    if source != 'igdb':
        return
    from core import catalogue
    igdb = {str(i['external_id']) for i in identities if i['namespace'] == 'igdb.game'}
    steam = {str(i['external_id']) for i in identities if i['namespace'] == 'steam.app'}
    if len(igdb) != 1 or not steam:
        return
    await catalogue.write_lock(db)
    pairs = [('igdb.game', next(iter(igdb))), *[('steam.app', app) for app in sorted(steam)]]
    mappings = list(await db.scalars(select(CatalogueIdentity).where(
        tuple_(CatalogueIdentity.namespace, CatalogueIdentity.external_id).in_(pairs)).execution_options(populate_existing=True)))
    roots = {i.entity_id for i in mappings if i.namespace == 'steam.app'}
    if len(roots) != 1:
        return
    root_id = next(iter(roots))
    native = next((i for i in mappings if i.namespace == 'igdb.game'), None)
    if native and native.entity_id == root_id:
        return
    stated_native = await db.scalar(select(CatalogueEntity.attributes['igdb_id'].astext).where(CatalogueEntity.id == root_id))
    if stated_native not in (None, '', next(iter(igdb))):
        raise catalogue.IdentityConflict('Imported game has contradictory IGDB evidence')
    if not native:
        return
    async with db.begin_nested():
        await merge_legacy_root(db, root_id, native.entity_id, pairs, attributes or {})


def _value(row, field):
    return (row.attributes or {}).get(field[11:]) if field.startswith('attributes.') else getattr(row, field, None)


def _retarget(value, old_id, root_id):
    if isinstance(value, list):
        return [_retarget(item, old_id, root_id) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: root_id if key in ('work_id', 'base_work_id', 'catalogue_id', 'entity_id', 'retained_root') and item == old_id
            else _retarget(item, old_id, root_id) for key, item in value.items()}


async def merge_legacy_root(db, root_id, duplicate_id, proof, incoming):
    """Conservative lossless merge; conflicting private records require review.

    The legacy root's entries, achievements, unlocks and import checkpoints do
    not move. A duplicate carrying private tracking is deliberately rejected.
    """
    from core import catalogue
    rows = {r.id: r for r in await db.scalars(select(CatalogueEntity).where(
        CatalogueEntity.id.in_((root_id, duplicate_id))).order_by(CatalogueEntity.id).with_for_update().execution_options(populate_existing=True))}
    root, duplicate = rows.get(root_id), rows.get(duplicate_id)
    if not root or not duplicate or root.kind != 'game' or duplicate.kind != 'game':
        raise catalogue.IdentityConflict('Game bridge requires two game roots')
    from core.game_types import capabilities, mobile_only
    known_class = (duplicate.attributes or {}).get('game_class', 'unknown')
    compatible = capabilities(duplicate.attributes).independent or (
        known_class == 'unknown' and capabilities(incoming).independent and not mobile_only(duplicate.attributes or {}))
    expected_native = next(value for namespace, value in proof if namespace == 'igdb.game')
    if (not compatible or mobile_only(root.attributes or {})
            or (root.attributes or {}).get('igdb_id') not in (None, '', expected_native)):
        raise catalogue.IdentityConflict('Game bridge has incompatible canonical game evidence')
    ids = list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id.in_(rows))))
    if (any(i.namespace == 'igdb.game' and i.entity_id == root_id for i in ids)
            or any(i.namespace == 'steam.app' and i.entity_id == duplicate_id for i in ids)
            or not any(i.namespace == 'steam.app' and i.entity_id == root_id for i in ids)):
        raise catalogue.IdentityConflict('Only a Steam-only imported root can be reconciled')
    if any(len({i.external_id for i in ids if i.namespace == ns}) > 1
           for ns in {i.namespace for i in ids} - {'steam.app'}):
        raise catalogue.IdentityConflict('Game bridge contradicts provider identities')
    for field in set(root.protected_fields or []) & set(duplicate.protected_fields or []):
        if _value(root, field) != _value(duplicate, field):
            raise catalogue.IdentityConflict('Game bridge has conflicting protected fields')
    for name, columns in (
        ('game_entries', ('work_id',)), ('game_child_states', ('work_id', 'base_work_id')),
        ('game_favorites', ('work_id',)), ('game_activity', ('work_id',)),
        ('game_achievements', ('work_id',)), ('game_achievement_unlocks', ('work_id',)),
    ):
        table = Base.metadata.tables[name]
        if await db.scalar(select(1).select_from(table).where(or_(*[table.c[c] == duplicate_id for c in columns])).limit(1)):
            raise catalogue.IdentityConflict('Duplicate game has private tracking; review is required')
    # Games must never occupy these contributor/screen/book identity roles.
    for name, columns in (
        ('catalogue_book_editions', ('entity_id', 'work_id', 'publisher_id')),
        ('catalogue_game_releases', ('entity_id',)),
        ('catalogue_legacy_links', ('entity_id',)), ('catalogue_show_links', ('entity_id',)),
        ('catalogue_contributor_search', ('entity_id',)), ('catalogue_screen_characters', ('actor_id',)),
        ('catalogue_credits', ('contributor_id',)), ('catalogue_character_appearances', ('character_id',)),
    ):
        table = Base.metadata.tables[name]
        if await db.scalar(select(1).select_from(table).where(or_(*[table.c[c] == duplicate_id for c in columns])).limit(1)):
            raise catalogue.IdentityConflict('Game has incompatible catalogue references')

    # Detach performance rows before changing their composite foreign keys.
    performances = [dict(r) for r in (await db.execute(select(CharacterPerformance.__table__).where(
        CharacterPerformance.work_id == duplicate_id))).mappings()]
    credit_map, appearance_map = {}, {}
    for model, keys, mapping in (
        (CatalogueCredit, ('provider', 'source_key'), credit_map),
        (CharacterAppearance, ('character_id', 'provider'), appearance_map),
    ):
        existing = {tuple(getattr(r, k) for k in keys): r for r in await db.scalars(select(model).where(model.work_id == root_id))}
        for row in await db.scalars(select(model).where(model.work_id == duplicate_id)):
            prior = existing.get(tuple(getattr(row, k) for k in keys))
            if prior and any(getattr(prior, c.name) != getattr(row, c.name)
                             for c in model.__table__.c if c.name not in ('id', 'work_id')):
                raise catalogue.IdentityConflict('Game bridge has conflicting credit or character records')
            mapping[row.id] = prior.id if prior else row.id
    relationships = list(await db.scalars(select(CatalogueRelationship).where(or_(
        CatalogueRelationship.source_id == duplicate_id, CatalogueRelationship.target_id == duplicate_id))))
    for row in relationships:
        if {row.source_id, row.target_id} == {root_id, duplicate_id}:
            raise catalogue.IdentityConflict('Related game records are not equivalent roots')
        target = {k: root_id if getattr(row, k) == duplicate_id else getattr(row, k)
                  for k in ('source_id', 'target_id', 'relation', 'provider')}
        prior = await db.scalar(select(CatalogueRelationship).where(*[
            getattr(CatalogueRelationship, k) == v for k, v in target.items()]))
        if prior and prior.attributes != row.attributes:
            raise catalogue.IdentityConflict('Game bridge has conflicting relationships')

    profiles = list(await db.scalars(select(UserProfileData).where(
        UserProfileData.game_banners.has_key(str(duplicate_id))).with_for_update()))
    for profile in profiles:
        banners = dict(profile.game_banners)
        if str(root_id) in banners and banners[str(root_id)] != banners[str(duplicate_id)]:
            raise catalogue.IdentityConflict('Game bridge has conflicting personal artwork')
        banners[str(root_id)] = banners.pop(str(duplicate_id)); profile.game_banners = banners

    # Preserve provenance per field, and carry explicit edits across the bridge.
    for field in duplicate.protected_fields or []:
        if field not in (root.protected_fields or []):
            if field.startswith('attributes.'):
                root.attributes = {**(root.attributes or {}), field[11:]: _value(duplicate, field)}
            else:
                setattr(root, field, _value(duplicate, field))
    root.protected_fields = sorted(set(root.protected_fields or []) | set(duplicate.protected_fields or []))
    for field in ('name', 'description', 'image_url'):
        catalogue.merge_fields(root, {field: getattr(duplicate, field)}, (duplicate.field_sources or {}).get(field, 'igdb'))
    for key, value in (duplicate.attributes or {}).items():
        catalogue.merge_fields(root, {'attributes': {key: deepcopy(value)}},
                               (duplicate.field_sources or {}).get('attributes.' + key, 'igdb'))

    await db.execute(delete(CharacterPerformance).where(CharacterPerformance.work_id == duplicate_id))
    for model, mapping in ((CatalogueCredit, credit_map), (CharacterAppearance, appearance_map)):
        duplicate_keys = [key for key, value in mapping.items() if key != value]
        if duplicate_keys:
            await db.execute(delete(model).where(model.id.in_(duplicate_keys)))
        await db.execute(update(model).where(model.work_id == duplicate_id).values(work_id=root_id))
    for row in performances:
        row.update(work_id=root_id, credit_id=credit_map[row['credit_id']], appearance_id=appearance_map[row['appearance_id']])
        prior = await db.scalar(select(CharacterPerformance).where(
            CharacterPerformance.credit_id == row['credit_id'],
            CharacterPerformance.appearance_id == row['appearance_id'],
            CharacterPerformance.language == row['language']))
        if prior and prior.source != row['source']:
            raise catalogue.IdentityConflict('Game bridge has conflicting performance records')
        from sqlalchemy.dialects.postgresql import insert
        statement = insert(CharacterPerformance).values(**row).on_conflict_do_nothing(
            index_elements=['credit_id', 'appearance_id', 'language'])
        await db.execute(statement)
    for row in relationships:
        target = {k: root_id if getattr(row, k) == duplicate_id else getattr(row, k)
                  for k in ('source_id', 'target_id', 'relation', 'provider')}
        prior = await db.scalar(select(CatalogueRelationship.id).where(*[
            getattr(CatalogueRelationship, k) == v for k, v in target.items()]))
        if prior:
            await db.delete(row)
        else:
            row.source_id, row.target_id = target['source_id'], target['target_id']
    for name in ('catalogue_game_releases', 'catalogue_steam_prices'):
        table = Base.metadata.tables[name]
        await db.execute(update(table).where(table.c.work_id == duplicate_id).values(work_id=root_id))
    await db.execute(update(CatalogueIdentity).where(CatalogueIdentity.entity_id == duplicate_id).values(entity_id=root_id))
    for row in await db.scalars(select(MetadataSnapshot).where(MetadataSnapshot.entity_id == duplicate_id)):
        row.entity_id = root_id
        row.payload = _retarget(row.payload, duplicate_id, root_id)
        row.coverage = _retarget(row.coverage, duplicate_id, root_id)
        if row.lease_token:
            row.lease_token = row.lease_until = None
            row.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if row.external_id == str(duplicate_id) and row.kind != ALIAS:
            prior = await db.scalar(select(MetadataSnapshot.id).where(
                MetadataSnapshot.provider == row.provider, MetadataSnapshot.kind == row.kind,
                MetadataSnapshot.external_id == str(root_id)))
            if prior:
                row.coverage = {**(row.coverage or {}), 'state': 'superseded', 'complete': True}
                row.next_attempt_at = row.lease_until = row.lease_token = None
            else:
                row.external_id = str(root_id)
        if row.error_code == 'identity_conflict':
            row.error_code = None; row.attempts = 0
            if row.coverage.get('state') != 'superseded':
                row.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None)
    provisional = list(await db.scalars(select(MetadataSnapshot).where(
        MetadataSnapshot.provider == 'anylist', MetadataSnapshot.kind == 'game_bulk',
        MetadataSnapshot.payload['rows'].contains([{'work_id': duplicate_id}])).order_by(MetadataSnapshot.id).with_for_update()))
    for row in provisional:
        payload = _retarget(row.payload, duplicate_id, root_id)
        unique = {tuple(item.get(key) for key in ('work_id', 'source', 'role')): item for item in payload['rows']}
        row.payload = {**payload, 'rows': list(unique.values())}
    db.add(MetadataSnapshot(provider='catalogue', kind=ALIAS, external_id=str(duplicate_id), entity_id=root_id,
        payload={'identities': proof, 'retained_root': root_id}, coverage={'complete': True}))
    await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.entity_id == root_id,
        MetadataSnapshot.error_code == 'identity_conflict').values(error_code=None, attempts=0,
            next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    stamp = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.entity_id == root_id,
        MetadataSnapshot.provider == 'igdb', MetadataSnapshot.kind == 'game_steam_identity').values(
            coverage={'state': 'complete', 'complete': True, 'igdb_id': expected_native},
            fetched_at=stamp, expires_at=stamp + timedelta(days=30), next_attempt_at=None,
            lease_token=None, lease_until=None, error_code=None, attempts=0))
    await db.flush()
    await db.delete(duplicate)
    await db.flush()
