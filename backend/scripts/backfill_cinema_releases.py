"""Backfill cinema evidence in paced batches, retaining durable failed retries."""
import asyncio,json
from core import settings_store
from core.cinema_releases import backfill_cinema_releases
from core.catalogue_providers import CatalogueProviders,ProviderHTTP
from db import AsyncSessionLocal


async def main():
    async with AsyncSessionLocal() as db:
        providers=CatalogueProviders(ProviderHTTP(session_factory=AsyncSessionLocal),
            tmdb_key=await settings_store.get_user_tmdb_key(db,None))
        if not providers.tmdb_key:raise RuntimeError('A server TMDB key is required')
        while True:
            result=await backfill_cinema_releases(db,providers,limit=25)
            print(json.dumps(result),flush=True)
            if not result['examined']:break


if __name__=='__main__':asyncio.run(main())
