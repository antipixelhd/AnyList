"""Populate season-card evidence for the existing TMDB series catalogue."""

import argparse
import asyncio
import json
from collections import Counter

from sqlalchemy import func, select

from core import settings_store
from core.catalogue_providers import CatalogueProviders, ProviderHTTP
from core.screen_appearance_backfill import backfill_screen_appearances
from db import AsyncSessionLocal


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, help='Expected name of the database to update')
    args = parser.parse_args()
    totals = Counter()
    async with ProviderHTTP.open_client() as client, AsyncSessionLocal() as db:
        if await db.scalar(select(func.current_database())) != args.database:
            raise SystemExit('Configured database does not match --database')
        key = settings_store.get_server_tmdb_key(await settings_store.get_global_settings(db))
        if not key:
            raise SystemExit('Configure a server TMDB key before running this backfill')
        providers = CatalogueProviders(ProviderHTTP(client, session_factory=AsyncSessionLocal), tmdb_key=key)
        while True:
            result = await backfill_screen_appearances(db, providers, limit=20)
            totals.update(result)
            print(json.dumps({'batch': result, 'total': totals}), flush=True)
            if result['examined'] < 20:
                break


if __name__ == '__main__':
    asyncio.run(main())
