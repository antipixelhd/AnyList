"""Cache studio logo banners, preserving manual artwork and media fallbacks."""
import argparse
import asyncio
import json

from core.settings_store import get_user_tmdb_key
from core.studio_artwork import backfill_studio_artwork
from db import AsyncSessionLocal


async def main(batches, limit):
    for _ in range(batches):
        async with AsyncSessionLocal() as db:
            key = await get_user_tmdb_key(db, -1)
            if not key:
                raise RuntimeError('Configure the server TMDB key before running this backfill')
            result = await backfill_studio_artwork(db, key, limit=limit)
            print(json.dumps(result), flush=True)
            if not result['examined'] or result['failed']:
                break


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=50, choices=range(1,101), metavar='1..100')
    parser.add_argument('--batches', type=int, default=1, choices=range(1,101), metavar='1..100')
    args = parser.parse_args()
    asyncio.run(main(args.batches, args.limit))
