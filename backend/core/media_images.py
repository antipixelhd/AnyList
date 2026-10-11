"""Separate poster variants from landscape artwork; cache provider lookups."""
from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import func, literal, update
from sqlalchemy.dialects.postgresql import JSONB

from core.catalogue_normalize import image
from models import Media


def image_key(url):
    return re.sub(r'(https://image\.tmdb\.org/t/p/)[^/]+/', r'\1', url)


def unique_images(values):
    result, seen = [], set()
    for value in values:
        url = image(value)
        if url and image_key(url) not in seen:
            seen.add(image_key(url))
            result.append(url)
    return result[:60]


def provider_images(raw, provider='tmdb'):
    result = {'posters': [], 'artwork': []}
    if not isinstance(raw, dict):
        return result
    for field, target in [('posters', 'posters'), ('backdrops', 'artwork')]:
        for row in raw.get(field) or []:
            if not isinstance(row, dict):
                continue
            ratio = row.get('aspect_ratio')
            if ratio is None and all(isinstance(row.get(field), (int, float)) and row[field] > 0 for field in ('height', 'width')):
                ratio = row['width'] / row['height']
            if isinstance(ratio, (int, float)) and (ratio >= 1 if field == 'posters' else ratio <= 1):
                continue
            path = row.get('file_path')
            url = ('https://image.tmdb.org/t/p/original' + path
                   if provider == 'tmdb' and isinstance(path, str)
                   and re.fullmatch(r'/[A-Za-z0-9_.-]+\.(jpg|png|webp)', path) else image(path))
            if url:
                result[target].append(url)
    return {field: unique_images(values) for field, values in result.items()}


def screen_images(media, details):
    from core.tracking_projection import media_data
    primary = media_data(media)
    cache = (media.tmdb_data or {}).get('_anylist_images') or {}
    stored = provider_images((media.tmdb_data or {}).get('images'))
    posters = unique_images([primary.get('poster'), *details.get('posters', []),
                             *cache.get('posters', []), *stored['posters']])
    poster_keys = {image_key(url) for url in posters}
    artwork = unique_images([primary.get('backdrop'), *details.get('artwork', []),
                             *cache.get('artwork', []), *stored['artwork']])
    return {'posters': posters, 'artwork': [url for url in artwork if image_key(url) not in poster_keys]}


async def fill_screen_images(db, media, api_key):
    if not api_key or not media.tmdb_id:
        return
    cache = dict((media.tmdb_data or {}).get('_anylist_images') or {})
    now = datetime.now(timezone.utc)
    for field in ('retry_after', 'expires_at'):
        try:
            if datetime.fromisoformat(cache.get(field, '')) > now:
                return
        except (ValueError, TypeError):
            pass
    from core import tmdb
    try:
        raw = await tmdb.get_title_images(media.tmdb_id, media.media_type.value, api_key)
        if raw.get('id') != media.tmdb_id or not all(isinstance(raw.get(field), list) for field in ('posters', 'backdrops')):
            raise ValueError('Invalid image response')
        cache = {**provider_images(raw), 'expires_at': (now + timedelta(days=30)).isoformat()}
    except Exception:
        cache['retry_after'] = (now + timedelta(minutes=10)).isoformat()
    await db.execute(update(Media).where(Media.id == media.id).values(
        tmdb_data=func.coalesce(Media.tmdb_data, literal({}, type_=JSONB)).op('||')({'_anylist_images': cache})))
    await db.commit()
    await db.refresh(media)
