"""Backfill a verified person's credited works without creating tracked entries."""
import argparse
import asyncio
import json
from core import settings_store
from core.catalogue_providers import CatalogueProviders, ProviderHTTP
from core.person_works_backfill import backfill_person_works
from db import AsyncSessionLocal


async def main(args):
    async with AsyncSessionLocal() as db:
        key=await settings_store.get_user_tmdb_key(db,args.user_id)
        if not key:
            raise RuntimeError('Configure a TMDB provider key before running this backfill')
        providers=CatalogueProviders(ProviderHTTP(session_factory=AsyncSessionLocal),tmdb_key=key)
        print(json.dumps(await backfill_person_works(db,providers,args.person,kind=args.kind)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--person',type=int,required=True)
    parser.add_argument('--kind',choices=('actor','staff','all'),default='staff')
    parser.add_argument('--user-id',type=int)
    asyncio.run(main(parser.parse_args()))
