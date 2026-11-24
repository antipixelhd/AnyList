"""Provider-backed sources shared by game detail pages for related editions."""
import re
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from core.game_types import INDEPENDENT, capabilities, igdb_game_class
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueRelationship


EDITION_CLASSES = frozenset({'expanded_game', 'remaster', 'port'})
WORKS_EDITION_CLASSES = EDITION_CLASSES - {'port'}
ATTRIBUTE = 'igdb_edition_sources'
VERSION = 1
WORKS_BUNDLE_ATTRIBUTE = 'igdb_works_bundle'
WORKS_BUNDLE_VERSION = 1
MEMBER_LIMIT = 33
BUNDLE_ALIAS = 'game_bundle_alias'
BUNDLE_EDITION = 'game_bundle_edition'


def steam_website_apps(raw):
    from core.catalogue_normalize import steam_app
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
    return apps


def edition_sources(raw):
    """Project an original game and an explicitly linked matching Steam bundle."""
    from core.catalogue_normalize import entity, igdb_steam_identities
    from core.game_content import erotic_game

    sources = {'version': VERSION}
    parent = raw.get('parent_game')
    if (isinstance(parent, dict) and igdb_game_class(parent) == 'game'
            and type(parent.get('id')) is int and 0 < parent['id'] != raw['id']
            and parent.get('name')):
        sources['parent_igdb_id'] = str(parent['id'])
    if igdb_steam_identities(raw):
        return sources, None

    apps = steam_website_apps(raw)
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
    sources.update(steam_bundle_id=str(native), steam_app_id=app, steam_bundle_erotic=erotic_game(bundle))
    target = entity('game', 'igdb.game', bundle, attributes={'game_class': 'bundle', 'igdb_id': str(native)})
    target['identities'].append({'namespace': 'steam.app', 'external_id': app})
    return sources, target


def works_bundle_candidate(game_class, sources):
    return game_class in WORKS_EDITION_CLASSES and isinstance(sources, dict) and all(
        sources.get(key) for key in ('parent_igdb_id', 'steam_bundle_id', 'steam_app_id'))


async def verify_works_bundle(igdb, raw, sources):
    """Determine whether every component belongs to one game and one matches the app."""
    bundle = sources['steam_bundle_id']
    app = sources['steam_app_id']
    base = int(sources['parent_igdb_id'])
    members = await igdb('games', 'fields id,name,game_type.type,version_parent,parent_game.id,websites.url,'
        'external_games.uid,external_games.external_game_source.name; '
        f'where bundles=({int(bundle)}); limit {MEMBER_LIMIT};')
    negative = {'version': WORKS_BUNDLE_VERSION}
    if (not isinstance(members, list) or not 0 < len(members) < MEMBER_LIMIT
            or any(not isinstance(member, dict) or type(member.get('id')) is not int
                   or member['id'] <= 0 or igdb_game_class(member) == 'unknown' for member in members)
            or len({member['id'] for member in members}) != len(members)):
        return negative

    matches = []
    for member in members:
        parent = member.get('parent_game')
        parent = parent.get('id') if isinstance(parent, dict) else parent
        parents = [value for value in (parent, member.get('version_parent')) if value is not None]
        if any(type(value) is not int or value <= 0 for value in parents):
            return negative
        family = set(parents)
        if family != {base} and (member['id'] != base or family):
            return negative
        game_class = igdb_game_class(member)
        if game_class == 'bundle' or not capabilities({'game_class':game_class}).independent:
            continue
        if not isinstance(member.get('name'), str) or not member['name'].strip():
            return negative
        if member['name'].casefold() != raw['name'].casefold():
            continue
        websites, external = member.get('websites', []), member.get('external_games', [])
        if (not isinstance(websites, list) or not isinstance(external, list)
                or any(not isinstance(site, dict) or not isinstance(site.get('url'), str) for site in websites)
                or any(not isinstance(row, dict) or (row.get('uid') == app and (
                    not isinstance(row.get('external_game_source'), dict)
                    or not isinstance(row['external_game_source'].get('name'), str))) for row in external)):
            return negative
        try:
            for site in websites:
                urlsplit(site['url'])
        except ValueError:
            return negative
        if app in steam_website_apps(member) or any(
                row.get('uid') == app and isinstance(row.get('external_game_source'), dict)
                and row['external_game_source'].get('name') == 'Steam' for row in external):
            matches.append(member['id'])
    if matches != [raw['id']]:
        return negative
    return {**negative, 'bundle_id':bundle, 'steam_app_id':app, 'parent_igdb_id':str(base)}


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


async def has_private_edition(db, work_id, viewer_id=None):
    """Check facts that give a viewer a reason to reach the original record."""
    from models import UserProfileData
    from models.games import GameAchievementUnlock, GameActivity, GameChildState, GameEntry, GameFavorite

    records = [(model.user_id, model.work_id == work_id) for model in
               (GameEntry, GameFavorite, GameActivity, GameAchievementUnlock)]
    records.extend([
        (GameChildState.user_id, or_(GameChildState.work_id == work_id, GameChildState.base_work_id == work_id)),
        (UserProfileData.user_id, UserProfileData.game_banners.has_key(str(work_id))),
    ])
    queries = [select(user).where(predicate) for user, predicate in records]
    if viewer_id is not None:
        queries = [query.where(user == viewer_id) for query, (user, _) in zip(queries, records)]
    return await db.scalar(queries[0].union_all(*queries[1:]).limit(1)) is not None


async def publish_bundle_alias(db, edition):
    """Keep the Steam owner as the public card, leaving tracking IDs intact."""
    from core import catalogue

    attrs = edition.attributes or {}
    proof = attrs.get(WORKS_BUNDLE_ATTRIBUTE) or {}
    app = proof.get('steam_app_id')
    sources = attrs.get(ATTRIBUTE)
    if not app or attrs.get(BUNDLE_ALIAS) or not works_bundle_candidate(attrs.get('game_class'), sources):
        return
    steam = await catalogue.resolve_identity(db, 'steam.app', app)
    native = await catalogue.resolve_identity(db, 'igdb.game', proof['bundle_id'])
    if not steam or not native or steam.entity_id != native.entity_id or steam.entity_id == edition.id:
        return
    bundle = await db.get(CatalogueEntity, steam.entity_id)
    if (capabilities(bundle.attributes).game_class != 'bundle' or (bundle.attributes or {}).get(BUNDLE_EDITION)
            or edition.protected_fields or await has_private_edition(db, edition.id)):
        return
    catalogue.merge_fields(edition, {'attributes':{BUNDLE_ALIAS:{
        'work_id':bundle.id, 'igdb_id':native.external_id, 'is_erotic':sources.get('steam_bundle_erotic'),
    }}}, 'igdb')
    catalogue.merge_fields(bundle, {'attributes':{BUNDLE_EDITION:edition.id}}, 'igdb')


def edition_sources_unknown(work):
    attrs = work.attributes or {}
    sources = attrs.get(ATTRIBUTE)
    return attrs.get('game_class') in EDITION_CLASSES and (
        not isinstance(sources, dict) or sources.get('version') != VERSION)


async def original_game(db, work):
    game_class = capabilities(work.attributes).game_class
    if game_class not in EDITION_CLASSES:
        return None
    original_classes = INDEPENDENT - {'port'} if game_class == 'port' else {'game'}
    query = select(CatalogueEntity).where(CatalogueEntity.id != work.id,
        CatalogueEntity.kind == 'game', CatalogueEntity.attributes['game_class'].astext.in_(original_classes))
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
    if len(originals) != 1:
        return None
    original = originals[0]
    return original if game_class != 'port' or capabilities(original.attributes).independent else None


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
