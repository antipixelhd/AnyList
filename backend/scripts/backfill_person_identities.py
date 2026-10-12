"""Repair verified TVDB/TMDB person links without matching names or editing credits."""
import argparse
import asyncio
import json
from core.person_identity_backfill import backfill_person_identities
from core.settings_store import get_user_tvdb_key
from db import AsyncSessionLocal


async def main(args):
    for _ in range(args.batches):
        async with AsyncSessionLocal() as db:
            key = await get_user_tvdb_key(db, args.user_id)
            if not key:
                raise RuntimeError('Configure the server TVDB key before running this backfill')
            result = await backfill_person_identities(db, key, limit=args.limit, native=args.person)
            print(json.dumps(result), flush=True)
            if args.person or not result['examined']:
                break


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=50, choices=range(1,501), metavar='1..500')
    parser.add_argument('--batches', type=int, default=1, choices=range(1,1001), metavar='1..1000')
    parser.add_argument('--person', type=int)
    parser.add_argument('--user-id', type=int, help='Use an existing account provider key instead of the server default')
    asyncio.run(main(parser.parse_args()))
