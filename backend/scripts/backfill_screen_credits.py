"""Backfill verified cast/crew for all stored movies and series, in small batches."""
import asyncio
import argparse
import json

from core import settings_store
from core.catalogue_providers import CatalogueProviders, ProviderHTTP
from core.media_credits import backfill_screen_credits
from db import AsyncSessionLocal


async def main(args):
    async with AsyncSessionLocal() as db:
        providers = CatalogueProviders(ProviderHTTP(session_factory=AsyncSessionLocal),
            tmdb_key=await settings_store.get_user_tmdb_key(db, args.user_id),
            tvdb_key=await settings_store.get_user_tvdb_key(db, args.user_id))
        cursor = args.cursor
        for _ in range(args.batches):
            result = await backfill_screen_credits(db, providers, cursor=cursor, limit=args.limit)
            print(json.dumps(result), flush=True)
            cursor = result['cursor']
            if not result['has_more']:
                break


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id',type=int)
    parser.add_argument('--cursor',type=int,default=0)
    parser.add_argument('--limit',type=int,default=25,choices=range(1,501))
    parser.add_argument('--batches',type=int,default=1000,choices=range(1,10001))
    asyncio.run(main(parser.parse_args()))
