"""Public game candidates, canonical IDs resolved in one bounded projection."""
import asyncio
from dataclasses import dataclass

from sqlalchemy import select

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


async def search(db, query, limit=8):
    rows = (await candidates(query))[:limit]
    ids = [row['external_id'] for row in rows]
    mappings = dict((await db.execute(select(CatalogueIdentity.external_id, CatalogueIdentity.entity_id)
        .join(CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id)
        .where(CatalogueIdentity.namespace == 'igdb.game', CatalogueIdentity.external_id.in_(ids),
               CatalogueEntity.kind == 'game'))).all()) if ids else {}
    return {'results': [dict(id=mappings.get(row['external_id']), igdb_id=int(row['external_id']),
        type='game', title=row['name'], poster=row.get('image_url'), year=(row.get('release_date') or '')[:4] or None,
        game_class=row['game_class']) for row in rows]}
