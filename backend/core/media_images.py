"""Separate poster variants from landscape artwork; cache provider lookups."""
from datetime import datetime, timedelta, timezone
import asyncio
import re
import httpx
import logging
from weakref import WeakValueDictionary

from sqlalchemy import func, literal, update
from sqlalchemy.dialects.postgresql import JSONB

from core.catalogue_normalize import image
from models import Media

IMAGE_POLICY_VERSION = 3
_fills = WeakValueDictionary()
logger = logging.getLogger(__name__)


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


def image_candidates(raw, provider='tmdb'):
    result = {'posters': [], 'artwork': []}
    if not isinstance(raw, dict):
        return result
    for field, target in [('posters', 'posters'), ('backdrops', 'artwork')]:
        for row in raw.get(field) or []:
            if not isinstance(row, dict):
                continue
            if row.get('iso_639_1') not in ('en', None):
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
                result[target].append({'url': url, 'language': row.get('iso_639_1'),
                    'width': row.get('width') or 0, 'votes': row.get('vote_count') or 0})
    for field, rows in result.items():
        seen = set()
        result[field] = [row for row in sorted(rows, key=lambda row: (row['language'] != 'en', -row['votes'], -row['width']))
            if not (image_key(row['url']) in seen or seen.add(image_key(row['url'])))][:60]
    return result


def provider_images(raw, provider='tmdb'):
    return {field: [row['url'] for row in rows] for field, rows in image_candidates(raw, provider).items()}


def screen_images(media, details):
    cache = (media.tmdb_data or {}).get('_anylist_images') or {}
    if cache.get('version') == IMAGE_POLICY_VERSION:
        return {'posters': unique_images(cache.get('posters') or []), 'artwork': unique_images(cache.get('artwork') or []),
                'image_replacements': cache.get('replacements') or {}}
    # Never paint the old unfiltered gallery while a new policy is being filled.
    return {'posters': [], 'artwork': [], 'image_replacements': {}}


async def fill_screen_images(db, media, api_key):
    lock = _fills.setdefault(media.id, asyncio.Lock())
    async with lock:
        await db.refresh(media)
        await _fill_screen_images(db, media, api_key)


async def _fill_screen_images(db, media, api_key):
    if not api_key or not media.tmdb_id:
        return
    cache = dict((media.tmdb_data or {}).get('_anylist_images') or {})
    now = datetime.now(timezone.utc)
    fields = ['expires_at'] if cache.get('version') == IMAGE_POLICY_VERSION else []
    if cache.get('retry_version') == IMAGE_POLICY_VERSION:
        fields.append('retry_after')
    for field in fields:
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
        from core.media_image_analysis import inspect_image, select_galleries
        candidates = image_candidates(raw)
        async with httpx.AsyncClient() as client:
            for rows in candidates.values():
                analyses = await asyncio.gather(*(inspect_image(row['url'], client) for row in rows))
                for row, analysis in zip(rows, analyses):
                    row['analysis'] = analysis
        selections = await select_galleries(candidates)
        selected = {field: selection['images'] for field, selection in selections.items()}
        selected['replacements'] = {source: target for selection in selections.values() for source, target in selection['replacements'].items()}
        cache = {**selected, 'version': IMAGE_POLICY_VERSION, 'expires_at': (now + timedelta(days=30)).isoformat()}
    except Exception as error:
        logger.warning('Image curation failed for media %s (%s)', media.id, type(error).__name__)
        cache['retry_after'] = (now + timedelta(minutes=10)).isoformat()
        cache['retry_version'] = IMAGE_POLICY_VERSION
        cache['last_error'] = type(error).__name__
    await db.execute(update(Media).where(Media.id == media.id).values(
        tmdb_data=func.coalesce(Media.tmdb_data, literal({}, type_=JSONB)).op('||')({'_anylist_images': cache})))
    await db.commit()
    await db.refresh(media)
