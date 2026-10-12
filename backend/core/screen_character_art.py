"""Resumable, quota-aware TVDB role artwork, matched only by verified identities."""
import uuid
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, or_, String
from sqlalchemy.dialects.postgresql import insert

from core import catalogue, settings_store, tvdb, tmdb
from core.catalogue_providers import CatalogueProviders, ProviderHTTP, ProviderError, retry_after_seconds
from core.catalogue_normalize import normalize_tvdb
from core.media_details import screen_work
from core.screen_characters import project_characters
from db import AsyncSessionLocal
from models import Media
from models.base import MediaType
from models.catalogue import CatalogueCredit, CatalogueIdentity, CatalogueLegacyLink, MetadataSnapshot

KIND = 'character_art'
VERSION = 2


def result(row, status, now=None):
    return catalogue.background_status(row, status, now)


async def cached_character_status(db, media_id):
    row = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider == 'tvdb',
        MetadataSnapshot.kind == KIND, MetadataSnapshot.external_id == str(media_id)))
    if row:
        state = catalogue.cached_refresh_status(row, datetime.now(timezone.utc).replace(tzinfo=None))
        if state=='cached' and (row.coverage or {}).get('version')!=VERSION:
            state=None
        return result(row, 'ready' if state == 'cached' else 'deferred' if state else 'pending')
    return {'state': 'pending', 'pending': True, 'error': None, 'retry_after': 0}


async def request_tvdb(providers, path):
    # All optional calls use the shared rate lane and bounded Retry-After policy.
    try:
        token = await tvdb._get_token(providers.tvdb_key)
    except Exception as error:
        import httpx
        if isinstance(error, httpx.HTTPStatusError):
            raise ProviderError('rate_limited' if error.response.status_code == 429 else 'unauthorized',
                                retry_after_seconds(error.response.headers.get('Retry-After', ''), 60)) from None
        raise ProviderError('unavailable', 60) from None
    raw = await providers.http.request('tvdb', 'GET', tvdb.TVDB_BASE + path,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
    return raw.get('data') if isinstance(raw, dict) else None


async def resolve_imdb_person(providers, imdb_id):
    raw = await providers.http.request('tmdb','GET','https://api.themoviedb.org/3/find/'+imdb_id,
        headers=tmdb.get_headers(providers.tmdb_key),params={'external_source':'imdb_id'})
    candidates = {r['id'] for r in (raw or {}).get('person_results') or []
                  if isinstance(r,dict) and type(r.get('id')) is int and r['id']>0}
    return next(iter(candidates)) if len(candidates)==1 else None


async def fill_character_art(db, media, user_id=None, *, providers=None, person_limit=8):
    work = await screen_work(db, media)
    if work is None:
        return {'state': 'unavailable', 'pending': False, 'retry_after': 0, 'error': None}
    if providers is None:
        providers = CatalogueProviders(ProviderHTTP(session_factory=AsyncSessionLocal),
            tvdb_key=await settings_store.get_user_tvdb_key(db, user_id),
            tmdb_key=await settings_store.get_user_tmdb_key(db, user_id))
    if not providers.tvdb_key:
        return {'state': 'unavailable', 'pending': False, 'retry_after': 0, 'error': 'missing_key'}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    token = uuid.uuid4().hex
    await catalogue.write_lock(db)
    await db.execute(insert(MetadataSnapshot).values(provider='tvdb', kind=KIND,
        external_id=str(media.id), entity_id=work.id).on_conflict_do_nothing())
    row = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider == 'tvdb',
        MetadataSnapshot.kind == KIND, MetadataSnapshot.external_id == str(media.id)).with_for_update())
    state = catalogue.cached_refresh_status(row, now)
    if state=='cached' and (row.coverage or {}).get('version')!=VERSION:
        state=None
    if state:
        await db.commit()
        return result(row, 'ready' if state == 'cached' else 'deferred', now)
    row.lease_token = token
    row.lease_until = now + timedelta(minutes=10)
    await db.commit()
    # Provider responses remain in the snapshot, not in canonical attributes.
    expired=row.expires_at and row.expires_at<=now
    payload = {} if expired or row.error_code=='identity_conflict' else dict(row.payload or {})
    try:
        ids = {i.namespace: i.external_id for i in await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id == work.id))}
        native = payload.get('native') or media.tvdb_id or ids.get('tvdb.' + work.kind)
        if not native and ids.get('imdb.title'):
            matches = await request_tvdb(providers, '/search/remoteid/' + ids['imdb.title'])
            candidates = {r[work.kind]['id'] for r in matches or [] if isinstance(r, dict)
                          and isinstance(r.get(work.kind), dict) and type(r[work.kind].get('id')) is int}
            if len(candidates) == 1:
                native = next(iter(candidates))
        raw = payload.get('title')
        if native and not raw:
            raw = await request_tvdb(providers, f'/{"movies" if work.kind == "movie" else "series"}/{native}/extended')
        if native:
            if not isinstance(raw, dict) or str(raw.get('id')) != str(native):
                raise ProviderError('identity_conflict')
            remotes = {r.get('sourceName'): str(r.get('id')) for r in raw.get('remoteIds') or []}
            if ids.get('imdb.title') and remotes.get('IMDB') != ids['imdb.title']:
                raise ProviderError('identity_conflict')
        payload.update(native=native, title=raw, people=dict(payload.get('people') or {}))
        cast = sorted((r for r in (raw or {}).get('characters') or [] if r.get('type') == 3 and r.get('image')),
                      key=lambda r: r.get('sort') or 0)
        actor_ids = set(await db.scalars(select(CatalogueCredit.contributor_id).where(
            CatalogueCredit.work_id == work.id, CatalogueCredit.role == 'actor')))
        cast_ids = {str(r.get('peopleId') or r.get('personId') or '') for r in cast}
        mappings = {i.external_id: i.entity_id for i in await db.scalars(select(CatalogueIdentity).where(
            CatalogueIdentity.namespace == 'tvdb.person', CatalogueIdentity.external_id.in_(cast_ids)))}
        pending, calls, transient_error = False, 0, None
        for member in cast:
            pid = str(member.get('peopleId') or member.get('personId') or '')
            if not pid.isdecimal() or pid in payload['people'] or mappings.get(pid) in actor_ids:
                continue
            if calls >= person_limit:
                pending = True
                continue
            try:
                person = await request_tvdb(providers, '/people/' + pid + '/extended')
                if not isinstance(person, dict) or str(person.get('id')) != pid:
                    raise ProviderError('identity_conflict')
                payload['people'][pid] = person
            except ProviderError as error:
                if error.code != 'not_found':
                    transient_error = error
                    pending = True
                    break
                payload['people'][pid] = {}
            calls += 1
        # Some TVDB performers have only an IMDb identity. Resolve that exact ID
        # through TMDB instead of guessing from the name or role spelling.
        for pid, person in list(payload['people'].items()):
            remotes=person.get('remoteIds') or []
            if person.get('_tmdb_resolved') or any(r.get('sourceName')=='TheMovieDB.com' for r in remotes):
                continue
            imdb=next((r.get('id') for r in remotes if r.get('sourceName')=='IMDB'),None)
            if not isinstance(imdb,str) or not re.fullmatch(r'nm\d+',imdb) or not getattr(providers,'tmdb_key',None):
                continue
            if transient_error or calls>=person_limit:
                pending=True
                continue
            calls+=1
            try:
                native_person=await resolve_imdb_person(providers,imdb)
                payload['people'][pid]={**person,'_tmdb_resolved':True,'remoteIds':[*remotes,
                    *([{'sourceName':'TheMovieDB.com','id':str(native_person)}] if native_person else [])]}
            except ProviderError as error:
                transient_error=error;pending=True
                break
        await catalogue.write_lock(db)
        await db.refresh(row, with_for_update=True)
        if row.lease_token != token:
            await db.commit()
            return result(row, 'deferred')
        count, unmatched = 0, 0
        async with db.begin_nested():
            if native:
                await catalogue.bind_identity(db, work.id, 'tvdb.' + work.kind, str(native), 'tvdb',
                    {'basis': 'verified_title_remote_ids', 'imdb': ids.get('imdb.title')})
            for credit in normalize_tvdb({**(raw or {}), '_people': payload['people']}, work.kind)['credits'] if raw else []:
                if credit['role'] != 'actor' or not credit.get('character_image_url'):
                    continue
                roots = {mapping.entity_id for identity in credit['contributor']['identities']
                         if (mapping := await catalogue.resolve_identity(db, identity['namespace'], identity['external_id']))}
                if len(roots) != 1 or not roots <= actor_ids:
                    unmatched+=1
                    continue
                actor_id = next(iter(roots))
                for identity in credit['contributor']['identities']:
                    await catalogue.bind_identity(db, actor_id, identity['namespace'], identity['external_id'], 'tvdb',
                        {'basis': 'verified_person_remote_ids'})
                old = await db.scalar(select(CatalogueCredit).where(CatalogueCredit.work_id == work.id,
                    CatalogueCredit.provider == 'tvdb', CatalogueCredit.source_key == credit['source_key']))
                if old and (old.contributor_id != actor_id or old.role != 'actor'):
                    raise ProviderError('identity_conflict')
                values = {k: credit[k] for k in ('role', 'role_label', 'character_label', 'character_image_url', 'source_key', 'position')}
                await catalogue.upsert(db, CatalogueCredit, {**values, 'work_id': work.id,
                    'contributor_id': actor_id, 'provider': 'tvdb'}, ['work_id', 'provider', 'source_key'], preserve_missing=True)
                count += 1
            await project_characters(db, work.id)
            row.payload = payload
            row.coverage = {'version': VERSION, 'pending': pending, 'images': count,
                'available_images':len(cast),'unmatched_images':unmatched,'matched_by': 'verified_actor_ids'}
            row.fetched_at = now
            row.expires_at = None if pending else now + timedelta(days=7)
            row.next_attempt_at = now + timedelta(seconds=3) if pending else None
            row.error_code = transient_error.code if transient_error else None
            row.attempts = row.attempts + 1 if transient_error else 0
            if transient_error:
                row.next_attempt_at = now + timedelta(seconds=max(60 * 2 ** min(row.attempts,8), transient_error.retry_after or 0))
        state = 'deferred' if transient_error else 'partial' if pending else 'ready'
    except Exception as error:
        await db.rollback()
        await db.refresh(media)
        await db.refresh(work)
        await catalogue.write_lock(db)
        await db.refresh(row, with_for_update=True)
        if row.lease_token != token:
            await db.commit()
            return result(row, 'deferred')
        # Keep progress/last-good credits and publish a durable, redacted retry.
        row.payload = payload
        row.attempts += 1
        row.error_code = error.code if isinstance(error, ProviderError) else 'unavailable'
        row.next_attempt_at = now + timedelta(seconds=max(min(21600, 60 * 2 ** min(row.attempts, 8)),
                                                        getattr(error, 'retry_after', 0) or 0))
        row.coverage = {**(row.coverage or {}), 'pending': True}
        row.expires_at = None
        state = 'deferred'
    row.lease_token = None
    row.lease_until = None
    await db.commit()
    return result(row, state)


async def backfill_character_art(db, providers, *, limit=10):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = list(await db.scalars(select(Media).outerjoin(MetadataSnapshot,
        (MetadataSnapshot.provider == 'tvdb') & (MetadataSnapshot.kind == KIND) &
        (MetadataSnapshot.external_id == Media.id.cast(String)))
        .where(Media.media_type.in_([MediaType.movie, MediaType.series]),
            select(CatalogueCredit.id).join(CatalogueLegacyLink, CatalogueLegacyLink.entity_id == CatalogueCredit.work_id)
                .where(CatalogueLegacyLink.media_id == Media.id, CatalogueCredit.role == 'actor').exists(),
            or_(MetadataSnapshot.id.is_(None), MetadataSnapshot.coverage['version'].as_integer().is_distinct_from(VERSION),
                MetadataSnapshot.expires_at.is_(None), MetadataSnapshot.expires_at <= now),
            or_(MetadataSnapshot.next_attempt_at.is_(None), MetadataSnapshot.next_attempt_at <= now),
            or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now))
        .order_by(Media.id.desc()).limit(limit)))
    results = {'examined': len(rows), 'ready': 0, 'deferred': 0, 'images': 0}
    for media in rows:
        value = await fill_character_art(db, media, providers=providers)
        results['ready'] += value['state'] == 'ready'
        results['deferred'] += value['state'] in ('deferred', 'partial')
        results['images'] += value.get('images', 0)
    return results
