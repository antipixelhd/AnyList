"""Provider-backed sources shared by game detail pages for related editions."""
import re
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import aliased

from core.game_types import capabilities, igdb_game_class
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueRelationship


EDITION_CLASSES = frozenset({'expanded_game', 'remaster', 'port'})
ATTRIBUTE = 'igdb_edition_sources'
VERSION = 1


def edition_sources(raw):
    """Project an original game and an explicitly linked matching Steam bundle."""
    from core.catalogue_normalize import entity, igdb_steam_identities, steam_app

    sources = {'version': VERSION}
    parent = raw.get('parent_game')
    if (isinstance(parent, dict) and igdb_game_class(parent) == 'game'
            and type(parent.get('id')) is int and 0 < parent['id'] != raw['id']
            and parent.get('name')):
        sources['parent_igdb_id'] = str(parent['id'])
    if igdb_steam_identities(raw):
        return sources, None

    apps = set()
    for site in raw.get('websites') or []:
        url = site.get('url') if isinstance(site, dict) else None
        if not isinstance(url, str):
            continue
        try:
            if not urlsplit(url).username and (app := steam_app(url)):
                apps.add(app)
        except ValueError:
            continue
    candidates = {}
    for bundle in raw.get('bundles') or []:
        if (not isinstance(bundle, dict) or igdb_game_class(bundle) != 'bundle' or type(bundle.get('id')) is not int
                or not 0 < bundle['id'] != raw['id']
                or not isinstance(bundle.get('name'), str) or bundle['name'].casefold() != raw['name'].casefold()):
            continue
        identities = igdb_steam_identities(bundle)
        for app in {row['external_id'] for row in identities} & apps:
            if len(app) <= 10 and int(app) <= 4294967295:
                candidates[(bundle['id'], app)] = bundle
    if len(candidates) != 1:
        return sources, None
    (native, app), bundle = next(iter(candidates.items()))
    sources.update(steam_bundle_id=str(native), steam_app_id=app)
    target = entity('game', 'igdb.game', bundle, attributes={'game_class': 'bundle', 'igdb_id': str(native)})
    target['identities'].append({'namespace': 'steam.app', 'external_id': app})
    return sources, target


async def ingest_steam_bundle(db, bundle):
    """An edition may use a bundle already bound to this app, or publish its stub."""
    from core import catalogue

    app = next(row['external_id'] for row in bundle['identities'] if row['namespace'] == 'steam.app')
    owner = await catalogue.resolve_identity(db, 'steam.app', app)
    if owner:
        # An existing app belongs to its established catalogue record. The read
        # verifies that record's IGDB identity before using it for the edition.
        return
    await catalogue.upsert_entity(db, bundle, 'igdb')


def edition_sources_unknown(work):
    attrs = work.attributes or {}
    sources = attrs.get(ATTRIBUTE)
    return attrs.get('game_class') in EDITION_CLASSES and (
        not isinstance(sources, dict) or sources.get('version') != VERSION)


async def original_game(db, work):
    game_class = capabilities(work.attributes).game_class
    if game_class not in EDITION_CLASSES:
        return None
    query = select(CatalogueEntity).where(CatalogueEntity.id != work.id,
        CatalogueEntity.kind == 'game', CatalogueEntity.attributes['game_class'].astext == 'game')
    sources = (work.attributes or {}).get(ATTRIBUTE)
    parent = None
    if isinstance(sources, dict) and (work.field_sources or {}).get(f'attributes.{ATTRIBUTE}') == 'igdb':
        parent = sources.get('parent_igdb_id')
    if parent:
        query = query.join(CatalogueIdentity, CatalogueIdentity.entity_id == CatalogueEntity.id).where(
            CatalogueIdentity.namespace == 'igdb.game', CatalogueIdentity.external_id == parent)
    else:
        # IGDB can list a newer version on the original without a reverse parent.
        edge = CatalogueRelationship
        originals = select(edge.target_id).where(
            edge.source_id == work.id, edge.provider == 'igdb',
            edge.attributes['source_active'].astext.is_distinct_from('false'),
            (edge.relation == game_class) | ((edge.relation == 'version') &
                (edge.attributes['edition_type'].astext == game_class)))
        query = query.where(CatalogueEntity.id.in_(originals))
    originals = list(await db.scalars(query.limit(2)))
    return originals[0] if len(originals) == 1 else None


def steam_identity(identities, preferred=None):
    values = {row.external_id for row in identities if row.namespace == 'steam.app'
              and re.fullmatch(r'[1-9][0-9]{0,18}', row.external_id)}
    return preferred if preferred in values else next(iter(values)) if len(values) == 1 else None


async def steam_store(db, work, identities=None):
    """Resolve the Steam data owner and app for details, keeping import routing intact."""
    if work.kind != 'game' or not capabilities(work.attributes).searchable:
        return work, None
    if identities is None:
        identities = list(await db.scalars(select(CatalogueIdentity).where(
            CatalogueIdentity.entity_id == work.id, CatalogueIdentity.namespace == 'steam.app').limit(2)))
    attrs = work.attributes or {}
    app = steam_identity(identities, attrs.get('steam_primary_app_id'))
    if app or any(row.namespace == 'steam.app' for row in identities):
        return work, app if app and int(app) <= 4294967295 else None
    sources = attrs.get(ATTRIBUTE)
    if (not isinstance(sources, dict) or attrs.get('game_class') not in EDITION_CLASSES
            or (work.field_sources or {}).get(f'attributes.{ATTRIBUTE}') != 'igdb'):
        return work, None
    app, bundle = sources.get('steam_app_id'), sources.get('steam_bundle_id')
    if not app or not bundle:
        return work, None
    native = aliased(CatalogueIdentity)
    other_app = aliased(CatalogueIdentity)
    ambiguous = select(other_app.id).where(other_app.entity_id == CatalogueEntity.id,
        other_app.namespace == 'steam.app', other_app.external_id != app).exists()
    owner = await db.scalar(select(CatalogueEntity).join(CatalogueIdentity,
        CatalogueIdentity.entity_id == CatalogueEntity.id).join(native, native.entity_id == CatalogueEntity.id).where(
            CatalogueEntity.id != work.id, CatalogueEntity.kind == 'game',
            CatalogueEntity.attributes['game_class'].astext == 'bundle',
            CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id == app,
            native.namespace == 'igdb.game', native.external_id == bundle,
            ~ambiguous | (CatalogueEntity.attributes['steam_primary_app_id'].astext == app)))
    return (owner, app) if owner and capabilities(owner.attributes).searchable else (work, None)
