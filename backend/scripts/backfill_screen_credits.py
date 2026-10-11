"""Backfill verified cast/crew for all stored movies and series, in small batches."""
import asyncio
import json

from core import settings_store
from core.catalogue_providers import CatalogueProviders, ProviderHTTP
from core.media_credits import backfill_screen_credits
from db import AsyncSessionLocal


async def main():
    async with AsyncSessionLocal() as db:
        providers = CatalogueProviders(ProviderHTTP(session_factory=AsyncSessionLocal),
            tmdb_key=await settings_store.get_user_tmdb_key(db, None),
            tvdb_key=await settings_store.get_user_tvdb_key(db, None))
        cursor = 0
        while True:
            result = await backfill_screen_credits(db, providers, cursor=cursor)
            print(json.dumps(result), flush=True)
            cursor = result['cursor']
            if not result['has_more']:
                break


if __name__ == '__main__':
    asyncio.run(main())
