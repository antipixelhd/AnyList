"""Repair missing screen credits without changing personal tracking facts."""
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from core import catalogue, settings_store
from core.catalogue_additive import fill_document
from core.catalogue_normalize import normalize_tmdb, normalize_tvdb
from core.catalogue_providers import CatalogueProviders, ProviderHTTP
from core.media_details import screen_work
from core.screen_metadata_policy import metadata_gaps, screen_sources
from models.base import MediaType
from models.media import Media
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueLegacyLink, MetadataSnapshot
from db import AsyncSessionLocal

logger = logging.getLogger(__name__)
VERSION = 3


async def _screen_providers(db, user_id):
    return CatalogueProviders(ProviderHTTP(session_factory=AsyncSessionLocal),
        tmdb_key=await settings_store.get_user_tmdb_key(db, user_id),
        tvdb_key=await settings_store.get_user_tvdb_key(db, user_id))


def _credits_projected(snapshot, work, groups):
    coverage = snapshot.coverage or {}
    projected = coverage.get('detail_credit_groups', ('cast', 'crew', 'organizations'))
    return (work is not None and snapshot.entity_id == work.id and
            coverage.get('detail_credits_version') == VERSION and groups.issubset(projected))


async def _publish_credits(db, media_id, work, snapshot, groups):
    link = await db.get(CatalogueLegacyLink, media_id, populate_existing=True)
    if link and link.entity_id != work.id:
        raise catalogue.IdentityConflict('Existing title bridge requires review')
    if link is None:
        await catalogue.upsert(db, CatalogueLegacyLink,
            {'media_id': media_id, 'entity_id': work.id}, ['media_id'])
    snapshot.coverage = {**(snapshot.coverage or {}), 'detail_credits_version': VERSION,
                         'detail_credit_groups': sorted(groups)}


async def fill_screen_credits(db, media, user_id=None, *, providers=None, authoritative_primary=False):
    """Fill either missing cast or crew, respecting shared cache/leases/backoff.

    Empty provider credits are valid. A successful projection is remembered so
    such titles are retried only after the provider snapshot expires. Cached
    payloads from before this repair can be projected without another API call.
    """
    if media.media_type not in (MediaType.movie, MediaType.series):
        return 'unavailable'
    work = await screen_work(db, media)
    linked = False
    if work:
        if await db.get(CatalogueLegacyLink, media.id) is None:
            await catalogue.write_lock(db)
            # Re-resolve after the write lock; never bridge by a title's name.
            verified = await screen_work(db, media)
            if verified is None or verified.id != work.id or verified.kind != media.media_type.value:
                return 'failed'
            if await db.get(CatalogueLegacyLink, media.id, populate_existing=True) is None:
                await catalogue.upsert(db, CatalogueLegacyLink,
                    {'media_id': media.id, 'entity_id': work.id}, ['media_id'])
                linked = True
            await db.commit()
    sources = await screen_sources(db, media)
    result = 'unavailable'
    updated = linked
    for index, (provider, native) in enumerate(sources):
        authoritative = authoritative_primary and index == 0
        groups = None
        if index:
            # A shared in-flight refresh/backoff is not evidence of missing
            # source data. Avoid starting a second provider during that wait.
            if result == 'deferred':
                break
            groups, missing_metadata = await metadata_gaps(db, work)
            if not groups and not missing_metadata:
                continue
            # Remote matching is useful only for a real gap, never for routine
            # dual-provider ingestion. Character art has its own lookup path.
            if provider == 'tvdb' and not native and media.media_type == MediaType.movie:
                from core import tvdb
                imdb_id = media.imdb_id
                if not imdb_id and work:
                    imdb_id = await db.scalar(select(CatalogueIdentity.external_id).where(
                        CatalogueIdentity.entity_id == work.id, CatalogueIdentity.namespace == 'imdb.title'))
                if imdb_id:
                    if providers is None:
                        providers = await _screen_providers(db, user_id)
                    if not providers.tvdb_key:
                        continue
                    try:
                        await db.commit()
                        async with providers.http.lane('tvdb'):
                            native = await tvdb.find_movie(imdb_id, providers.tvdb_key)
                    except Exception:
                        logger.warning('Secondary title lookup failed for media %s', media.id)
        if not native:
            continue
        kind = media.media_type.value
        requested = groups if groups is not None else {'cast', 'crew', 'organizations'}
        key = (MetadataSnapshot.provider == provider, MetadataSnapshot.kind == kind,
               MetadataSnapshot.external_id == str(native))
        try:
            header = (await db.execute(select(MetadataSnapshot.id, MetadataSnapshot.coverage,
                MetadataSnapshot.entity_id, MetadataSnapshot.fetched_at, MetadataSnapshot.expires_at,
                MetadataSnapshot.lease_until, MetadataSnapshot.next_attempt_at,
                MetadataSnapshot.payload.is_not(None).label('has_payload')).where(*key))).one_or_none()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            status = catalogue.cached_refresh_status(header, now) if header else None
            if status in ('refreshing', 'retry_pending', 'last_good'):
                result = 'deferred'
                continue
            if not authoritative and status == 'cached' and _credits_projected(header, work, requested):
                result = 'cached'
                continue
            snapshot = None
            if status == 'cached' and header.has_payload:
                await catalogue.write_lock(db)
                snapshot = await db.get(MetadataSnapshot, header.id, populate_existing=True, with_for_update=True)
                if snapshot is None or catalogue.cached_refresh_status(snapshot, datetime.now(timezone.utc).replace(tzinfo=None)) != 'cached':
                    await db.commit()
                    result = 'deferred'
                    continue
                if not authoritative and _credits_projected(snapshot, work, requested):
                    await db.commit()
                    result = 'cached'
                    continue
                if not snapshot.payload:
                    await db.commit()
                    snapshot = None
            if snapshot is not None:
                doc = (normalize_tmdb if provider == 'tmdb' else normalize_tvdb)(snapshot.payload, kind)
                if not any(i['namespace'] == f'{provider}.{kind}' and i['external_id'] == str(native)
                           for i in doc['work']['identities']):
                    raise catalogue.IdentityConflict('Provider reply identity mismatch')
                async with db.begin_nested():
                    repaired = (await catalogue.ingest_document(db, doc, provider) if authoritative else
                                await fill_document(db, doc, provider, credit_groups=groups))
                    if work and repaired.id != work.id:
                        raise catalogue.IdentityConflict('Existing title bridge requires review')
                    snapshot.entity_id = repaired.id
                    await _publish_credits(db, media.id, repaired, snapshot, requested)
                await db.commit()
            else:
                if providers is None:
                    providers = await _screen_providers(db, user_id)
                if not getattr(providers, provider + '_key'):
                    continue

                async def finish_projection(entity, published):
                    await _publish_credits(db, media.id, entity, published, requested)

                receipt = await catalogue.refresh_metadata(db, providers, provider, kind, str(native),
                    additive=not authoritative, expected_entity_id=work.id if work else None,
                    credit_groups=groups, on_publish=finish_projection)
                if receipt['status'] != 'updated':
                    result = 'failed' if receipt.get('error_code') else 'deferred'
                    continue
                repaired = await db.get(CatalogueEntity, receipt['entity_id'])
            work = repaired
            result = 'updated'
            updated = True
        except Exception as error:
            await db.rollback()
            await db.refresh(media)
            work = await screen_work(db, media)
            logger.warning('Credit repair failed for media %s (%s)', media.id, type(error).__name__)
            result = 'failed'
    return 'updated' if updated else result


async def backfill_screen_credits(db, providers, *, cursor=0, limit=25):
    """Scan every stored screen title, including titles not yet in a user list."""
    ids = list(await db.scalars(select(Media.id)
        .where(Media.id > cursor, Media.media_type.in_([MediaType.movie, MediaType.series]))
        .order_by(Media.id).limit(limit)))
    result = {'cursor': cursor, 'examined': len(ids), 'updated': 0, 'failed': 0, 'has_more': len(ids) == limit}
    for media_id in ids:
        media = await db.get(Media, media_id)
        result['cursor'] = media_id
        if media is None:
            continue
        status = await fill_screen_credits(db, media, providers=providers)
        result['updated'] += int(status == 'updated')
        result['failed'] += int(status == 'failed')
    return result
