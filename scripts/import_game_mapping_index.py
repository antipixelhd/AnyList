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
from db import AsyncSessionLocal
from core.game_mapping_import import imdb_subset, import_jsonl, refresh_wikidata


async def main(args):
    if args.wikidata:
        if not args.observed_at:raise ValueError('--observed-at is required for an external Wikidata dump')
        return await import_jsonl(AsyncSessionLocal, 'wikidata', args.wikidata,observed_at=datetime.fromisoformat(args.observed_at))
    if args.refresh_wikidata:
        return await refresh_wikidata(AsyncSessionLocal)
    if args.imdb_subset:
        return await import_jsonl(AsyncSessionLocal, 'imdb', args.imdb_subset)
    with tempfile.TemporaryDirectory(prefix='anylist-imdb-') as folder:
        target = Path(folder) / 'games.jsonl'
        await asyncio.to_thread(imdb_subset, target, basics=args.basics, akas=args.akas)
        return await import_jsonl(AsyncSessionLocal, 'imdb', target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--wikidata')
    group.add_argument('--refresh-wikidata', action='store_true')
    group.add_argument('--imdb-subset')
    parser.add_argument('--basics')
    parser.add_argument('--akas')
    parser.add_argument('--observed-at',help='UTC ISO source snapshot time for an externally supplied dump')
    args = parser.parse_args()
    if hasattr(os, 'nice'):
        os.nice(15)
    print(json.dumps(asyncio.run(main(args))))
