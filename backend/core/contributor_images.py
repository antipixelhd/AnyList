"""Optional person portraits: metadata only, shared provider cache, bounded retries."""
import asyncio
import hashlib
import re

from core import settings_store, tmdb
from core.contributor_details import resolve_contributor

_failures = tmdb._TTLCache(maxsize=256)


def portraits(raw):
    rows = sorted((row for row in raw.get('profiles', []) if isinstance(row, dict)),
                  key=lambda row: (row.get('vote_average') or 0, row.get('vote_count') or 0, row.get('width') or 0), reverse=True)
    images = []
    for row in rows:
        path = row.get('file_path')
        if not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_.-]+\.(jpg|png|webp)', path):
            continue
        if (row.get('aspect_ratio') or 0) > 1:
            continue
        url = f'{tmdb.TMDB_IMAGE_BASE}/original{path}'
        if url not in images:
            images.append(url)
        if len(images) == 5:
            break
    return images


async def contributor_images(db, viewer, *, kind, key):
    resolved = await resolve_contributor(db, kind, key)
    if resolved is None:
        return None
    _, native = resolved
    unavailable = {'posters': [], 'image_status': {'state': 'unavailable'}}
    if not native:
        return unavailable
    provider_key = await settings_store.get_user_tmdb_key(db, viewer.id if viewer else -1)
    if not provider_key:
        return unavailable
    # Failed credentials must not block another caller's valid provider access.
    identity = (native, hashlib.sha256(provider_key.encode()).digest())
    deferred = {'posters': [], 'image_status': {'state': 'deferred', 'error': 'unavailable', 'retry_after': 600}}
    if _failures.get(identity):
        return deferred
    try:
        async with asyncio.timeout(12):
            raw = await tmdb.get_person_images(native, api_key=provider_key)
        if raw.get('id') != native or not isinstance(raw.get('profiles'), list):
            raise ValueError('Invalid portrait identity')
        return {'posters': portraits(raw), 'image_status': {'state': 'ready'}}
    except Exception:
        _failures.set(identity, True, 600)
        return deferred
