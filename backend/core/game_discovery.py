"""Public game candidates, canonical IDs resolved in one bounded projection."""
import asyncio
from dataclasses import dataclass

from sqlalchemy import and_, func, select

from core.catalogue_providers import CatalogueProviders, ProviderHTTP, ProviderError
from db import AsyncSessionLocal
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueRelationship

_searches = {}
_slots = asyncio.Semaphore(2)


@dataclass
class SearchFlight:
    task: asyncio.Task | None = None
    waiters: int = 0
    started: bool = False


async def upstream(flight, query):
    # Queued searches hold no database connection/provider budget lane.
    async with _slots:
        flight.started = True
        return await CatalogueProviders(ProviderHTTP(session_factory=AsyncSessionLocal)).search('igdb', 'game', query, 1, 50)


async def candidates(query):
    # IGDB is a shared server credential. No viewer state enters this flight or
    # ProviderHTTP's bounded response cache. One cancelled dialog cannot cancel
    # another viewer's public request; unique concurrent work remains bounded.
    key = query.casefold()
    flight = _searches.get(key)
    if flight is None:
        if len(_searches) >= 32:
            raise ProviderError('rate_limited')
        flight = SearchFlight()
        task = flight.task = asyncio.create_task(upstream(flight, query))
        _searches[key] = flight
        def finished(done):
            if _searches.get(key) is flight:
                _searches.pop(key, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
    flight.waiters += 1
    try:
        return await asyncio.shield(flight.task)
    finally:
        flight.waiters -= 1
        if not flight.waiters and not flight.started and not flight.task.done():
            if _searches.get(key) is flight:
                _searches.pop(key, None)
            flight.task.cancel()


async def search(db, query, limit=8,*,hide_erotic_games=True):
    from core.game_edition_sources import BUNDLE_ALIAS
    from core.game_release_bundles import active_edge

    source_rows = await candidates(query)
    excluded = {row['external_id'] for row in source_rows if hide_erotic_games and row.get('is_erotic')}
    rows = [row for row in source_rows if row['external_id'] not in excluded]
    by_native = {row['external_id']:row for row in rows}
    alias = CatalogueEntity.attributes[BUNDLE_ALIAS]
    bundle_ids = [row['external_id'] for row in rows if row['game_class'] == 'bundle']
    identity_query = select(
        CatalogueIdentity.external_id, func.coalesce(alias['work_id'].as_integer(), CatalogueIdentity.entity_id),
        alias['igdb_id'].astext, alias['is_erotic'].as_boolean()
    ).join(CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace == 'igdb.game', CatalogueIdentity.external_id.in_(by_native),
        CatalogueEntity.kind == 'game')
    if bundle_ids:
        identity_query = identity_query.add_columns(
            CatalogueRelationship.target_id, CatalogueRelationship.attributes['original_card']).outerjoin(
            CatalogueRelationship, and_(CatalogueRelationship.source_id == CatalogueEntity.id,
                CatalogueIdentity.external_id.in_(bundle_ids), *active_edge(CatalogueRelationship)))
    mappings = {row[0]: row[1:] for row in (await db.execute(identity_query)).all()} if rows else {}
    results = []
    seen = set()
    for row in rows:
        native = row['external_id']
        owner, canonical, erotic, *release = mappings.get(native, (None, None, False))
        original = release[1] if release else None
        if original:
            owner, canonical, erotic = release[0], str(original['igdb_id']), original['is_erotic']
        if canonical in excluded or hide_erotic_games and erotic:
            continue
        key = canonical or native
        if key in seen:
            continue
        seen.add(key)
        source = by_native.get(canonical)
        if source is None:
            source = dict(name=original['title'], image_url=original['poster'],
                release_date=original['release_date'], game_class='game') if original else row
        results.append(dict(id=owner, igdb_id=int(canonical or native), type='game', title=source['name'],
            poster=source.get('image_url'), year=(source.get('release_date') or '')[:4] or None,
            game_class='bundle' if canonical and not original else source['game_class']))
        if len(results) == limit:
            break
    return {'results':results}
