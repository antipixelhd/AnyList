"""Verify when a bundle packages a release of one original game."""
from sqlalchemy import select, update

from core.game_types import igdb_game_class
from models.catalogue import CatalogueIdentity, CatalogueRelationship


VERSION = 1
EDITION_TYPE = 'release_bundle'
MEMBER_LIMIT = 33
MEMBER_FIELDS = ('id', 'name', 'game_type.type', 'version_parent', 'parent_game')
FAMILY_FIELDS = ','.join(prefix + field for prefix in ('', 'parent_game.', 'parent_game.parent_game.')
                         for field in MEMBER_FIELDS)
RELEASE_COMPONENTS = frozenset({'game', 'port', 'standalone_expansion', 'expansion', 'dlc'})


def original(member):
    seen = set()
    for _ in range(3):
        if not isinstance(member, dict):
            return None
        identifier = member.get('id')
        if type(identifier) is not int or identifier <= 0 or identifier in seen or member.get('version_parent'):
            return None
        seen.add(identifier)
        kind = igdb_game_class(member)
        if kind == 'game':
            return member if not member.get('parent_game') and member.get('name') else None
        if kind == 'unknown':
            return None
        member = member.get('parent_game')
    return None


def normalized_title(name):
    return ' '.join(name.casefold().split()) if isinstance(name, str) else ''


def classify(bundle, members):
    """Return one verified original, False for a different product, or None for incomplete evidence."""
    if not isinstance(members, list) or not 0 < len(members) < MEMBER_LIMIT:
        return None
    if any(not isinstance(member, dict) for member in members):
        return None
    roots = [original(member) for member in members]
    if any(root is None for root in roots) or len({member['id'] for member in members}) != len(members):
        return None
    root = roots[0]
    title = normalized_title(root['name'])
    if (len({item['id'] for item in roots}) != 1 or root['id'] == bundle['id']
            or not title or title != normalized_title(bundle.get('name'))):
        return False
    classes = {member['id']: igdb_game_class(member) for member in members}
    if not set(classes.values()) <= RELEASE_COMPONENTS:
        return False
    if root['id'] in classes or 'port' in classes.values():
        return root
    # Some console releases package the campaign as an add-on to a standalone
    # multiplayer component. Require both components and the original's title
    # on the playable add-on. A bundle of related add-ons alone is insufficient.
    if list(classes.values()).count('standalone_expansion') == 1 and any(
        classes[member['id']] in {'expansion', 'dlc'}
        and classes.get(member['parent_game']['id']) == 'standalone_expansion'
        and normalized_title(member.get('name')).startswith(title + ':')
        for member in members
    ):
        return root
    return False


async def verify(igdb, bundle):
    from core.catalogue_providers import ProviderError
    from core.game_browse_sources import BASE_FIELDS, card
    from core.game_release_dates import bounds, game_release

    members = await igdb('games', f'fields {FAMILY_FIELDS}; where bundles=({bundle["id"]}); limit {MEMBER_LIMIT};')
    root = classify(bundle, members)
    if root is None:
        raise ProviderError('bad_response')
    if root is False:
        return {}
    rows = await igdb('games', f'fields {BASE_FIELDS},version_parent,themes; where id={root["id"]}; limit 1;')
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or rows[0].get('id') != root['id'] or rows[0].get('version_parent')
            or normalized_title(rows[0].get('name')) != normalized_title(root['name'])):
        raise ProviderError('bad_response')
    projected = card(rows[0])
    if not projected or projected['game_class'] != 'game':
        return {}
    first, later = bounds(projected), bounds(game_release(bundle))
    if not first or not later:
        raise ProviderError('bad_response')
    if later[0] <= first[1]:
        return {}
    return {'original_card': projected, 'member_ids': sorted(member['id'] for member in members)}


def active_edge(edge):
    return (
        edge.relation == 'version',
        edge.provider == 'igdb',
        edge.attributes['edition_type'].astext == EDITION_TYPE,
        edge.attributes['source_active'].as_boolean().is_(True),
    )


async def publish(db, bundle, proof):
    from core import catalogue
    from core.catalogue_normalize import entity
    from core.game_edition_sources import BUNDLE_EDITION

    if (bundle.attributes or {}).get(BUNDLE_EDITION):
        proof = {}
    original_card = proof.get('original_card')
    target_id = None
    if original_card:
        native = original_card['igdb_id']
        work = await catalogue.upsert_entity(db, entity('game', 'igdb.game',
            {'id': native, 'name': original_card['title']}, artwork=original_card['poster'],
            attributes={'igdb_id': str(native), 'game_class': 'game'}), 'igdb')
        if work.id != bundle.id:
            target_id = work.id

    obsolete = update(CatalogueRelationship).where(
        CatalogueRelationship.source_id == bundle.id, *active_edge(CatalogueRelationship))
    if target_id is not None:
        obsolete = obsolete.where(CatalogueRelationship.target_id != target_id)
    await db.execute(obsolete.values(
        attributes=CatalogueRelationship.attributes.op('||')({'source_active': False})))
    if target_id is not None:
        await catalogue.upsert(db, CatalogueRelationship, {
            'source_id': bundle.id, 'target_id': target_id, 'relation': 'version', 'provider': 'igdb',
            'attributes': {**proof, 'edition_type': EDITION_TYPE, 'source_active': True},
        }, ['source_id', 'target_id', 'relation', 'provider'])


async def browse_originals(db, identifiers):
    rows = await db.execute(select(
        CatalogueIdentity.external_id, CatalogueRelationship.attributes['original_card'])
        .join(CatalogueRelationship, CatalogueRelationship.source_id == CatalogueIdentity.entity_id)
        .where(CatalogueIdentity.namespace == 'igdb.game', CatalogueIdentity.external_id.in_(identifiers),
               *active_edge(CatalogueRelationship)))
    return {int(native): card for native, card in rows}
