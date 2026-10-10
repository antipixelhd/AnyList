"""Run resumable missing-profile repair using private server provider defaults."""
import argparse
import asyncio
import json

from core.contributor_backfill import backfill_contributors
from core.settings_store import get_user_tmdb_key
from db import AsyncSessionLocal


async def main(limit, batches):
    for _ in range(batches):
        async with AsyncSessionLocal() as db:
            key = await get_user_tmdb_key(db, -1)
            if not key:
                raise RuntimeError('Configure the server TMDB key before running this backfill')
            result = await backfill_contributors(db, key, limit=limit)
            print(json.dumps(result), flush=True)
            if not result['examined']:
                break


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=100, choices=range(1,501), metavar='1..500')
    parser.add_argument('--batches', type=int, default=1, choices=range(1,1001), metavar='1..1000')
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.batches))
