"""Public game candidates, canonical IDs resolved in one bounded projection."""
import asyncio
from dataclasses import dataclass

from sqlalchemy import func, select

from core.catalogue_providers import CatalogueProviders, ProviderHTTP, ProviderError
from db import AsyncSessionLocal
from models.catalogue import CatalogueEntity, CatalogueIdentity

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

    source_rows = await candidates(query)
    excluded = {row['external_id'] for row in source_rows if hide_erotic_games and row.get('is_erotic')}
    rows = [row for row in source_rows if row['external_id'] not in excluded]
    by_native = {row['external_id']:row for row in rows}
    alias = CatalogueEntity.attributes[BUNDLE_ALIAS]
    mappings = {native:(owner, canonical, erotic) for native,owner,canonical,erotic in (await db.execute(select(
        CatalogueIdentity.external_id, func.coalesce(alias['work_id'].as_integer(), CatalogueIdentity.entity_id),
        alias['igdb_id'].astext, alias['is_erotic'].as_boolean())
        .join(CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id)
        .where(CatalogueIdentity.namespace == 'igdb.game', CatalogueIdentity.external_id.in_(by_native),
               CatalogueEntity.kind == 'game'))).all()} if rows else {}
    results = []
    seen = set()
    for row in rows:
        native = row['external_id']
        owner, canonical, erotic = mappings.get(native, (None, None, False))
        if canonical in excluded or hide_erotic_games and erotic:
            continue
        key = canonical or native
        if key in seen:
            continue
        seen.add(key)
        source = by_native.get(canonical, row)
        results.append(dict(id=owner, igdb_id=int(canonical or native), type='game', title=source['name'],
            poster=source.get('image_url'), year=(source.get('release_date') or '')[:4] or None,
            game_class='bundle' if canonical else source['game_class']))
        if len(results) == limit:
            break
    return {'results':results}
