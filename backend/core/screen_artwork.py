"""TVDB title artwork using verified provider IDs and cached metadata reads."""
import asyncio
from functools import lru_cache

import httpx
import pycountry

from core import tmdb, tvdb
from core.screen_metadata_policy import screen_sources

_slots = asyncio.Semaphore(2)
_artwork_request_timeout = 8


class ArtworkIdentityUnavailable(ValueError):
    """No verified provider bridge exists; this is not a provider outage."""


@lru_cache(maxsize=256)
def language_code(value: str) -> str | None:
    """Normalize TMDB, TVDB and regional language tags to ISO 639-3."""
    code = value.strip().lower().replace('_', '-').split('-', 1)[0]
    if len(code) == 2:
        language = pycountry.languages.get(alpha_2=code)
    elif len(code) == 3:
        language = pycountry.languages.get(alpha_3=code) or pycountry.languages.get(bibliographic=code)
    else:
        return None
    return language.alpha_3 if language else None


def original_artwork_language(media) -> str | None:
    value = (media.tmdb_data or {}).get('original_language')
    language = language_code(value) if isinstance(value, str) else None
    return None if language in ('eng', 'und', 'mul', 'zxx') else language


def artwork_candidates(raw, types, kind, *, original_language=None):
    taxonomy = {row['id']: row for row in types if isinstance(row, dict) and row.get('recordType') == kind}
    result = {'posters': [], 'backdrops': []}
    for row in raw.get('artworks') or []:
        info = taxonomy.get(row.get('type'), {})
        name = str(info.get('name') or '').casefold()
        if name not in ('poster', 'background'):
            continue
        language = row.get('language')
        preferred = language in (None, '', 'eng', 'en')
        background = name == 'background'
        fallback = (background and not preferred and original_language is not None
                    and isinstance(language, str) and language_code(language) == original_language)
        if (not preferred and not fallback) or (background and row.get('includesText') is not False):
            continue
        url = tvdb._image_url(row.get('image'))
        if not url or not url.startswith('https://artworks.thetvdb.com/'):
            continue
        result['backdrops' if background else 'posters'].append({
            'file_path': url, 'thumbnail': tvdb._image_url(row.get('thumbnail')),
            'iso_639_1': 'en' if not background and language in ('eng', 'en') else None,
            'original_language_fallback': fallback,
            'width': row.get('width') or info.get('width'), 'height': row.get('height') or info.get('height'),
            'vote_count': row.get('score') or 0})
    return result


async def _get_artwork(path, api_key):
    """Give a timed-out TVDB read one more chance before deferring the gallery."""
    try:
        async with asyncio.timeout(_artwork_request_timeout):
            return await tvdb._get(path, api_key)
    except (TimeoutError, httpx.TimeoutException):
        async with asyncio.timeout(_artwork_request_timeout):
            return await tvdb._get(path, api_key)


async def title_artwork(db, media, tvdb_key, tmdb_key=None):
    kind = media.media_type.value
    native = dict(await screen_sources(db, media)).get('tvdb')
    ids = (media.tmdb_data or {}).get('external_ids') or {}
    if not native and not ids and media.tmdb_id and tmdb_key:
        ids = await tmdb.get_external_ids(media.tmdb_id, kind, api_key=tmdb_key)
    native = native or (ids.get('tvdb_id') if kind == 'series' else None)
    imdb = media.imdb_id or ids.get('imdb_id')
    async with _slots:
        if not native and imdb:
            import re
            if re.fullmatch(r'tt[0-9]+', imdb):
                response = await _get_artwork('/search/remoteid/' + imdb, tvdb_key)
                candidates = {row[kind]['id'] for row in response.get('data') or []
                              if isinstance(row.get(kind), dict) and type(row[kind].get('id')) is int}
                native = next(iter(candidates)) if len(candidates) == 1 else None
        if not native:
            raise ArtworkIdentityUnavailable('No verified TVDB title identity')
        path = f'/series/{native}/artworks' if kind == 'series' else f'/movies/{native}/extended'
        artwork, taxonomy = await asyncio.gather(_get_artwork(path, tvdb_key), _get_artwork('/artwork/types', tvdb_key))
        raw = artwork.get('data') or {}
        if str(raw.get('id')) != str(native) or not isinstance(raw.get('artworks'), list):
            raise ValueError('Invalid TVDB artwork response')
        types = taxonomy.get('data') or []
    return artwork_candidates(raw, types, kind, original_language=original_artwork_language(media))
