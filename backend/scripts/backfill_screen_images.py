"""Refresh existing gallery caches after an image policy change, without altering media metadata."""
import asyncio
import json

from sqlalchemy import or_, select

from core import settings_store
from core.media_images import IMAGE_POLICY_VERSION, fill_screen_images
from db import AsyncSessionLocal
from models import Media
from models.base import MediaType


async def main():
    async with AsyncSessionLocal() as db:
        key = await settings_store.get_user_tmdb_key(db, None)
        if not key:
            raise RuntimeError('A server TMDB key is required for the image backfill')
        cursor = 0
        version = Media.tmdb_data['_anylist_images']['version'].as_integer()
        while True:
            rows = list(await db.scalars(select(Media).where(
                Media.id > cursor, Media.tmdb_id.is_not(None),
                Media.media_type.in_([MediaType.movie, MediaType.series]),
                Media.tmdb_data.has_key('_anylist_images'),
                or_(version.is_(None), version != IMAGE_POLICY_VERSION),
            ).order_by(Media.id).limit(25)))
            if not rows:
                break
            refreshed, failed = 0, []
            for media in rows:
                await fill_screen_images(db, media, key)
                cache = (media.tmdb_data or {}).get('_anylist_images') or {}
                if cache.get('version') == IMAGE_POLICY_VERSION:
                    refreshed += 1
                else:
                    failed.append(media.id)
                cursor = media.id
            print(json.dumps({'cursor': cursor, 'refreshed': refreshed, 'failed': failed}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
