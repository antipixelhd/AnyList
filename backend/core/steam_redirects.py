"""Store redirects are progress relationships, never equivalent game identities."""
import asyncio
import re
import uuid
from datetime import timedelta
from time import monotonic
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from core import catalogue, steam_cache, steam_software
from core.catalogue_normalize import image
from core.catalogue_providers import ProviderError, ProviderHTTP
from core.game_types import canonical_game, capabilities, igdb_game_class
from core.steam import SteamError
from models.catalogue import CatalogueIdentity, MetadataSnapshot

KIND = 'steam_app_redirect'
STORE = 'https://store.steampowered.com'
WAIT_SECONDS = 12


async def wait_for_lookup(factory, app, *, probe=None):
    deadline = monotonic() + WAIT_SECONDS
    while monotonic() < deadline:
        await asyncio.sleep(0.15)
        async with factory() as db:
            row = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider == 'steam',
                MetadataSnapshot.kind == KIND, MetadataSnapshot.external_id == app))
            stamp = steam_cache.now()
            if row and row.expires_at and row.expires_at > stamp:
                return (row.payload or {}).get('target_app')
            if row and row.next_attempt_at and row.next_attempt_at > stamp:
                return None
            active = row and row.lease_until and row.lease_until > stamp
        if not active:
            return await lookup(factory, app, probe=probe)
    raise SteamError('Steam redirect checks are still running. Try the import again shortly.')


def target_app(app, status, location):
    if status == 200:
        return None
    if status not in (301, 302, 303, 307, 308) or not location:
        raise ProviderError('unavailable')
    url = urlsplit(urljoin(STORE, location))
    match = re.fullmatch(r'/app/([1-9][0-9]{0,9})(?:/[^?#]*)?', url.path)
    if (url.scheme != 'https' or url.netloc != 'store.steampowered.com' or not match
            or url.fragment or int(match[1]) > 4294967295 or match[1] == app):
        raise ProviderError('unverified_redirect')
    return match[1]


async def request(factory, app):
    # Read only the first response headers. Never follow an arbitrary Location
    # or download the store HTML just to discover a parent application.
    async with asyncio.timeout(10), httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3), follow_redirects=False) as client:
        async with ProviderHTTP(session_factory=factory).lane('steam'):
            async with client.stream('GET', f'{STORE}/app/{app}/', params={'l': 'english', 'cc': 'US'}) as response:
                return target_app(app, response.status_code, response.headers.get('location'))


async def lookup(factory, app, *, probe=None):
    stamp, token = steam_cache.now(), uuid.uuid4().hex
    async with factory() as db:
        await db.execute(insert(MetadataSnapshot).values(provider='steam', kind=KIND, external_id=app)
            .on_conflict_do_nothing(constraint='uq_catalogue_snapshot'))
        row = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider == 'steam',
            MetadataSnapshot.kind == KIND, MetadataSnapshot.external_id == app).with_for_update())
        if row.expires_at and row.expires_at > stamp:
            return (row.payload or {}).get('target_app')
        if row.next_attempt_at and row.next_attempt_at > stamp:
            return None
        waiting = bool(row.lease_until and row.lease_until > stamp)
        if not waiting:
            row.lease_token, row.lease_until = token, stamp + timedelta(minutes=2)
        await db.commit()
    if waiting:
        return await wait_for_lookup(factory, app, probe=probe)
    try:
        target = await (probe(app) if probe else request(factory, app))
        values = {'payload': {'target_app': target}, 'fetched_at': stamp,
            'expires_at': stamp + timedelta(days=30), 'next_attempt_at': None, 'error_code': None}
    except (ProviderError, httpx.HTTPError, TimeoutError):
        target = None
        values = {'next_attempt_at': stamp + timedelta(minutes=15), 'error_code': 'redirect_unavailable'}
    async with factory() as db:
        await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider == 'steam',
            MetadataSnapshot.kind == KIND, MetadataSnapshot.external_id == app, MetadataSnapshot.lease_token == token)
            .values(**values, lease_token=None, lease_until=None))
        await db.commit()
    return target


async def associate(factory, apps, source, *, probe=None):
    from core import steam_targets
    pairs = {}
    # Bound provider work without truncating the library or selected apps.
    for offset in range(0, len(apps), 4):
        batch = apps[offset:offset + 4]
        results = await asyncio.gather(*(lookup(factory, app, probe=probe) for app in batch))
        pairs.update((app, target) for app, target in zip(batch, results) if target)
    parents = list(dict.fromkeys(pairs.values()))
    if not parents:
        return
    await steam_software.classify(factory, [{'app_id': app} for app in parents])
    async with factory() as db:
        public = await steam_cache.public_metadata(db, parents)
    parents = [app for app in parents if public.get(app, {}).get('steam_app_type') != 'software']
    matches = {}
    for offset in range(0, len(parents), 100):
        batch = parents[offset:offset + 100]
        try:
            rows = await source.steam_games([int(app) for app in batch])
        except ProviderError:
            continue
        if len(rows) >= 500:
            continue
        found, incomplete = {app: {} for app in batch}, set()
        for row in rows:
            app = str(row.get('uid', ''))
            if app not in found:
                continue
            raw = canonical_game(row.get('game'))
            if not raw or not isinstance(raw.get('name'), str) or not raw['name'].strip():
                incomplete.add(app)
            else:
                found[app][raw['id']] = raw
        for app, games in found.items():
            if app not in incomplete and len(games) == 1:
                raw = next(iter(games.values()))
                if capabilities({'game_class': igdb_game_class(raw)}).independent:
                    matches[app] = raw
    for app, parent in pairs.items():
        raw = matches.get(parent)
        if not raw:
            continue
        async with factory() as db:
            try:
                await catalogue.write_lock(db)
                # Provider I/O ran outside catalogue/owner locks. An existing
                # association or canonical source identity must win a race.
                if await steam_targets.targets(db, [app]):
                    continue
                identity = await catalogue.resolve_identity(db, 'steam.app', app)
                if identity and await db.scalar(select(CatalogueIdentity.id).where(
                        CatalogueIdentity.entity_id == identity.entity_id, CatalogueIdentity.namespace == 'igdb.game')):
                    continue
                public = await steam_cache.public_metadata(db, [app, parent])
                if any(public.get(value, {}).get('steam_app_type') == 'software' for value in (app, parent)):
                    continue
                # Recheck the source remains a verified miss, not a pack/mod or
                # a newly discovered game, before publishing a relationship.
                if public.get(app, {}).get('igdb_classification', {}).get('state') != 'unmatched':
                    continue
                await catalogue.upsert_entity(db, {'kind': 'game', 'name': raw['name'], 'image_url': image(raw.get('cover')),
                    'attributes': {'game_class': igdb_game_class(raw), 'igdb_id': str(raw['id'])},
                    'identities': [{'namespace': 'igdb.game', 'external_id': str(raw['id'])},
                        {'namespace': 'steam.app', 'external_id': parent}]}, 'igdb')
                await steam_targets.assign(db, app, parent, evidence={'source': 'steam_store_redirect', 'source_app': app,
                    'target_app': parent, 'igdb_game': str(raw['id'])})
                await db.commit()
            except catalogue.IdentityConflict:
                await db.rollback()
