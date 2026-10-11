"""Bounded, resumable TVDB character-image backfill; cooldowns are never bypassed."""
import argparse
import asyncio
import json

from core import settings_store
from core.catalogue_providers import CatalogueProviders, ProviderHTTP
from core.screen_character_art import backfill_character_art, fill_character_art
from db import AsyncSessionLocal
from models import Media


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--media-id', type=int)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('--limit must be between 1 and 100')
    async with AsyncSessionLocal() as db:
        key = await settings_store.get_user_tvdb_key(db, None)
        if not key:
            raise RuntimeError('A server TVDB key is required')
        providers = CatalogueProviders(ProviderHTTP(session_factory=AsyncSessionLocal), tvdb_key=key)
        if args.media_id is not None:
            media = await db.get(Media, args.media_id)
            if not media:
                parser.error('Media entry not found')
            value = await fill_character_art(db, media, providers=providers)
        else:
            value = await backfill_character_art(db, providers, limit=args.limit)
        print(json.dumps(value), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
