"""Repair missing screen credits without changing personal tracking facts."""
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from core import catalogue, settings_store
from core.catalogue_additive import fill_document
from core.catalogue_normalize import normalize_tmdb, normalize_tvdb
from core.catalogue_providers import CatalogueProviders, ProviderHTTP
from core.media_details import screen_work
from models.base import MediaType
from models.media import Media
from models.catalogue import CatalogueEntity, CatalogueLegacyLink, MetadataSnapshot
from db import AsyncSessionLocal

logger = logging.getLogger(__name__)
VERSION = 2


async def fill_screen_credits(db, media, user_id=None, *, providers=None):
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
            await catalogue.upsert(db, CatalogueLegacyLink,
                {'media_id': media.id, 'entity_id': work.id}, ['media_id'])
            await db.commit()
            linked = True
    if providers is None:
        providers = CatalogueProviders(ProviderHTTP(session_factory=AsyncSessionLocal),
            tmdb_key=await settings_store.get_user_tmdb_key(db, user_id),
            tvdb_key=await settings_store.get_user_tvdb_key(db, user_id))
    sources = [('tmdb', media.tmdb_id), ('tvdb', media.tvdb_id)]
    result = 'unavailable'
    updated = linked
    for provider, native in sources:
        if not native:
            continue
        kind = media.media_type.value
        try:
            snapshot = await db.scalar(select(MetadataSnapshot).where(
                MetadataSnapshot.provider == provider, MetadataSnapshot.kind == kind,
                MetadataSnapshot.external_id == str(native)))
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            status = catalogue.cached_refresh_status(snapshot, now) if snapshot else None
            if status in ('refreshing', 'retry_pending', 'last_good'):
                result = 'deferred'
                continue
            if status == 'cached' and work and snapshot.entity_id == work.id and (
                    snapshot.coverage or {}).get('detail_credits_version') == VERSION:
                result = 'cached'
                continue
            if status == 'cached' and snapshot.payload:
                await catalogue.write_lock(db)
                await db.refresh(snapshot, with_for_update=True)
                if catalogue.cached_refresh_status(snapshot, datetime.now(timezone.utc).replace(tzinfo=None)) != 'cached':
                    await db.commit()
                    result = 'deferred'
                    continue
                doc = (normalize_tmdb if provider == 'tmdb' else normalize_tvdb)(snapshot.payload, kind)
                if not any(i['namespace'] == f'{provider}.{kind}' and i['external_id'] == str(native)
                           for i in doc['work']['identities']):
                    raise catalogue.IdentityConflict('Provider reply identity mismatch')
                async with db.begin_nested():
                    repaired = await fill_document(db, doc, provider)
                    if work and repaired.id != work.id:
                        raise catalogue.IdentityConflict('Existing title bridge requires review')
                    snapshot.entity_id = repaired.id
            elif getattr(providers, provider + '_key'):
                receipt = await catalogue.refresh_metadata(db, providers, provider, kind, str(native),
                    additive=True, expected_entity_id=work.id if work else None)
                if receipt['status'] != 'updated':
                    result = 'failed' if receipt.get('error_code') else 'deferred'
                    continue
                repaired = await db.get(CatalogueEntity, receipt['entity_id'])
                snapshot = await db.scalar(select(MetadataSnapshot).where(
                    MetadataSnapshot.provider == provider, MetadataSnapshot.kind == kind,
                    MetadataSnapshot.external_id == str(native)))
            else:
                continue
            await catalogue.write_lock(db)
            link = await db.get(CatalogueLegacyLink, media.id, populate_existing=True)
            if link and link.entity_id != repaired.id:
                raise catalogue.IdentityConflict('Existing title bridge requires review')
            if link is None:
                await catalogue.upsert(db, CatalogueLegacyLink,
                    {'media_id': media.id, 'entity_id': repaired.id}, ['media_id'])
            snapshot.coverage = {**(snapshot.coverage or {}), 'detail_credits_version': VERSION}
            await db.commit()
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
