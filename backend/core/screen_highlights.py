"""Warm the public screen shelves using existing Browse evidence and ingestion."""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import BigInteger, String, case, func, select, tuple_, union
from sqlalchemy.orm import aliased

from core import browse, catalogue, chronological_discovery, settings_store
from core.catalog_search import result_filters
from core.catalogue_providers import CatalogueProviders, ProviderError, ProviderHTTP
from core.discovery_policy import viewer_policies
from core.game_work import Priority, work_context
from core.media_credits import credits_projected, fill_screen_credits
from db import AsyncSessionLocal
from models import Media
from models.catalogue import CatalogueIdentity, CatalogueLegacyLink, MetadataSnapshot
from models.tracking import TrackedEntry

HIGHLIGHT_COUNT = 10
INTERVAL = 15 * 60
log = logging.getLogger(__name__)


async def cached_highlights(db, *, key, mdblist_key, show_anime, region='US'):
    """Use the same default shelves without spending quota on a second feed."""
    settings = await result_filters(db, None)
    specs = browse.section_specs()
    sections = {}
    for kind in ('movie', 'series'):
        policies = await viewer_policies(db, None, kind, [values['sort'] for _, values in specs])
        for _, values in specs:
            sort = values['sort']
            policy = policies[sort]
            filters = browse.section_filters(region, 0, **settings, **values,
                min_votes=policy.min_votes, min_popularity=policy.min_popularity)
            if sort == 'newest':
                prefetched = await chronological_discovery.page(
                    kind, key, filters, show_anime=show_anime, size=browse.PAGE_SIZE, cached_only=True)
            else:
                prefetched = await browse.imdb_page(
                    kind, key, filters, 1, mdblist_key=mdblist_key, show_anime=show_anime, cached_only=True)
            page = await browse.browse_page(db, None, term='', media_type=kind, page=1,
                source='' if prefetched is not None else 'local', prefetched=prefetched,
                show_anime=show_anime, key=key, mdblist_key=mdblist_key, attach_context=False, **filters)
            sections[kind, sort] = page['results'][:HIGHLIGHT_COUNT]
            await db.commit()
    return sections


async def ingest_highlights(db, providers, sections, *, show_anime):
    # Round-robin by rank so a single shelf cannot fill a whole turn.
    titles = {}
    for rank in range(HIGHLIGHT_COUNT):
        for cards in sections.values():
            if rank < len(cards):
                row = cards[rank]
                if row.get('tmdb_id'):
                    titles.setdefault((row['type'], str(row['tmdb_id'])), row.get('id'))
    counts = dict(candidates=len(titles), listed=0, updated=0, cached=0, deferred=0)
    if not titles:
        return counts
    direct = select(Media.media_type.cast(String).label('kind'), Media.tmdb_id.cast(String).label('native')).where(
        tuple_(Media.media_type, Media.tmdb_id).in_([(kind, int(native)) for kind, native in titles]),
        select(TrackedEntry.id).where(TrackedEntry.media_id == Media.id).exists())
    identity = CatalogueIdentity
    title_keys = [(f'tmdb.{kind}', native) for kind, native in titles]
    matches_title = tuple_(identity.namespace, identity.external_id).in_(title_keys)
    catalogue_keys = select(func.replace(identity.namespace, 'tmdb.', '').label('kind'),
        identity.external_id.label('native')).where(matches_title)
    linked = catalogue_keys.join(CatalogueLegacyLink, CatalogueLegacyLink.entity_id == identity.entity_id).where(
        select(TrackedEntry.id).where(TrackedEntry.media_id == CatalogueLegacyLink.media_id).exists())
    tvdb = aliased(CatalogueIdentity)
    tvdb_id = case((tvdb.external_id.op('~')(r'^[0-9]{1,10}$'), tvdb.external_id.cast(BigInteger)))
    bridged = catalogue_keys.join(tvdb, (tvdb.entity_id == identity.entity_id) &
        (tvdb.namespace == func.replace(identity.namespace, 'tmdb.', 'tvdb.'))).join(Media,
        (Media.tvdb_id == tvdb_id) & (('tvdb.' + Media.media_type.cast(String)) == tvdb.namespace)).where(
        select(TrackedEntry.id).where(TrackedEntry.media_id == Media.id).exists())
    owned = union(direct, linked, bridged).subquery()
    list_owned = set((await db.execute(select(owned))).all())
    counts['listed'] = len(list_owned)
    titles = {key: media_id for key, media_id in titles.items() if key not in list_owned}
    if not titles:
        await db.commit()
        return counts
    snapshot = MetadataSnapshot
    headers = (await db.execute(select(snapshot.kind, snapshot.external_id, snapshot.entity_id, snapshot.fetched_at, snapshot.expires_at,
        snapshot.next_attempt_at, snapshot.lease_until, snapshot.coverage).where(snapshot.provider == 'tmdb',
        tuple_(snapshot.kind, snapshot.external_id).in_(titles)))).all()
    by_key = {(row.kind, row.external_id): row for row in headers}
    media_ids = {media_id for media_id in titles.values() if media_id}
    links = dict((await db.execute(select(CatalogueLegacyLink.media_id, CatalogueLegacyLink.entity_id).where(
        CatalogueLegacyLink.media_id.in_(media_ids)))).all()) if media_ids else {}
    await db.commit()

    async def validate_visibility(entity, published):
        payload = published.payload or {}
        if payload.get('adult') or not browse.anime_visible(payload, show_anime):
            raise ProviderError('content_unavailable')

    for (kind, native), media_id in titles.items():
        header = by_key.get((kind, native))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        status = catalogue.cached_refresh_status(header, now) if header else None
        if status == 'cached':
            if not media_id or credits_projected(header, links.get(media_id)):
                counts['cached' if header.entity_id else 'deferred'] += 1
                continue
            # A fresh payload can still fill gaps or link a legacy title.
        elif status:
            counts['deferred'] += 1
            continue
        media = await db.get(Media, media_id) if media_id else None
        if media:
            status = await fill_screen_credits(db, media, providers=providers)
        else:
            result = await catalogue.refresh_metadata(db, providers, 'tmdb', kind, native, additive=True,
                publish_in_transaction=validate_visibility)
            status = result['status']
        counts[status if status in ('updated', 'cached') else 'deferred'] += 1
        await db.commit()
    return counts


async def refresh_highlights(db, providers, *, mdblist_key, show_anime):
    from core.screen_appearance_backfill import backfill_screen_appearances, career_candidates

    sections = await cached_highlights(db, key=providers.tmdb_key, mdblist_key=mdblist_key, show_anime=show_anime)
    counts = await ingest_highlights(db, providers, sections, show_anime=show_anime)
    series = list(dict.fromkeys(card['tmdb_id'] for (kind, _), cards in sections.items()
                               for card in cards if kind == 'series' and card.get('tmdb_id')))
    appearances = await backfill_screen_appearances(db, providers, limit=3, priority_ids=series)
    careers = await backfill_screen_appearances(db, providers, limit=3,
        priority_ids=await career_candidates(db, show_anime=show_anime))
    return {'titles': counts, 'appearances': appearances, 'shelves': {
        f'{kind}:{sort}': len(cards) for (kind, sort), cards in sections.items()}, 'careers': careers}


async def scheduler():
    await asyncio.sleep(30)
    while True:
        try:
            async with AsyncSessionLocal() as db:
                settings = await settings_store.get_global_settings(db)
                key = settings_store.get_server_tmdb_key(settings)
                tvdb_key, pin = settings_store.get_server_tvdb_credentials(settings)
                if key:
                    from core import tvdb
                    if tvdb_key:
                        tvdb.set_subscriber_pin(tvdb_key, pin)
                    await db.commit()
                    with work_context(Priority.RETRY, 'screen-highlights'):
                        async with ProviderHTTP.open_client() as client:
                            providers = CatalogueProviders(
                                ProviderHTTP(client, session_factory=AsyncSessionLocal), tmdb_key=key, tvdb_key=tvdb_key)
                            counts = await refresh_highlights(db, providers,
                                mdblist_key=settings.mdblist_api_key if settings else None,
                                show_anime=settings.show_anime if settings else True)
                    log.info('Screen Browse warmup: %s', counts)
        except Exception:
            log.exception('Screen Browse warmup failed')
        await asyncio.sleep(INTERVAL)
