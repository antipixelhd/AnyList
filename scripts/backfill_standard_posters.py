"""Refresh standard TMDB posters for titles already present in tracking lists.

New catalogue imports receive the standard poster during normal enrichment. This
bounded maintenance command covers existing rows without making profile/list
requests fan out into one TMDB request per title.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core import tmdb  # noqa: E402
from db import AsyncSessionLocal, engine  # noqa: E402
from models.media import Media, MediaType  # noqa: E402
from models.tracking import TrackedEntry  # noqa: E402
from routers.media import get_user_tmdb_key  # noqa: E402


async def run(media_id: int | None, limit: int | None) -> tuple[int, int, int]:
    updated = unchanged = failed = 0
    async with AsyncSessionLocal() as db:
        statement = (
            select(Media, TrackedEntry.user_id)
            .join(TrackedEntry, TrackedEntry.media_id == Media.id)
            .where(
                Media.media_type.in_([MediaType.movie, MediaType.series]),
                Media.tmdb_id.is_not(None),
            )
            .distinct(Media.id)
            .order_by(Media.id)
        )
        if media_id is not None:
            statement = statement.where(Media.id == media_id)
        if limit is not None:
            statement = statement.limit(limit)
        rows = (await db.execute(statement)).all()

        for media, user_id in rows:
            try:
                key = await get_user_tmdb_key(db, user_id)
                if not key:
                    failed += 1
                    continue
                fetch = tmdb.get_movie if media.media_type == MediaType.movie else tmdb.get_show
                data = await fetch(media.tmdb_id, api_key=key, cache_ttl=None)
                poster = tmdb.poster_url(data.get("poster_path"))
                if poster and poster != media.poster_path:
                    media.poster_path = poster
                    updated += 1
                else:
                    unchanged += 1
            except Exception as exc:
                failed += 1
                print(f"media {media.id} ({media.tmdb_id}) failed: {exc}")
        await db.commit()
    await engine.dispose()
    return updated, unchanged, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore standard TMDB posters for existing tracked titles")
    parser.add_argument("--media-id", type=int, help="refresh one local Media row")
    parser.add_argument("--limit", type=int, help="bound the number of tracked titles refreshed")
    args = parser.parse_args()
    updated, unchanged, failed = asyncio.run(run(args.media_id, args.limit))
    print(f"updated={updated} unchanged={unchanged} failed={failed}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
