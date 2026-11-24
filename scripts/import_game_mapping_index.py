"""Build public game reference indexes without running imports on ASGI threads."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from db import AsyncSessionLocal, engine
from core.game_mapping_import import imdb_subset, import_jsonl, refresh_wikidata


async def import_imdb(args):
    with tempfile.TemporaryDirectory(prefix='anylist-imdb-') as folder:
        target = Path(folder) / 'games.jsonl'
        await asyncio.to_thread(imdb_subset, target, basics=args.basics, akas=args.akas,
            principals=args.principals, names=args.names, include_credits=True)
        return await import_jsonl(AsyncSessionLocal, 'imdb', target)


async def refresh_imdb(args):
    from sqlalchemy import func, select
    from core.game_imdb_enrichment import now
    from models.game_mapping import GameMappingGeneration

    # The lock belongs to this process connection and is released if it exits.
    lock = 846732014
    async with engine.connect() as connection:
        if not await connection.scalar(select(func.pg_try_advisory_lock(lock))):
            return {'skipped': True}
        try:
            observed_at = await connection.scalar(select(GameMappingGeneration.observed_at).where(
                GameMappingGeneration.source == 'imdb', GameMappingGeneration.active.is_(True),
                GameMappingGeneration.coverage['principal_credits'].as_boolean().is_(True)))
            await connection.commit()
            if observed_at and observed_at.date() >= now().date():
                return {'skipped': True}
            return await import_imdb(args)
        finally:
            if connection.in_transaction():
                await connection.rollback()
            await connection.execute(select(func.pg_advisory_unlock(lock)))


async def main(args):
    if args.wikidata:
        if not args.observed_at:raise ValueError('--observed-at is required for an external Wikidata dump')
        return await import_jsonl(AsyncSessionLocal, 'wikidata', args.wikidata,observed_at=datetime.fromisoformat(args.observed_at))
    if args.refresh_wikidata:
        return await refresh_wikidata(AsyncSessionLocal)
    if args.imdb_subset:
        return await import_jsonl(AsyncSessionLocal, 'imdb', args.imdb_subset)
    if args.refresh_imdb:
        return await refresh_imdb(args)
    return await import_imdb(args)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--wikidata')
    group.add_argument('--refresh-wikidata', action='store_true')
    group.add_argument('--refresh-imdb', action='store_true', help='Import only when today has no complete IMDb snapshot')
    group.add_argument('--imdb-subset')
    parser.add_argument('--basics')
    parser.add_argument('--akas')
    parser.add_argument('--principals')
    parser.add_argument('--names')
    parser.add_argument('--observed-at',help='UTC ISO source snapshot time for an externally supplied dump')
    args = parser.parse_args()
    if hasattr(os, 'nice'):
        os.nice(15)
    print(json.dumps(asyncio.run(main(args))))
