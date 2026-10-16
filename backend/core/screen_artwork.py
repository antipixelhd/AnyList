"""TVDB title artwork using verified provider IDs and cached metadata reads."""
import asyncio
from core import tmdb, tvdb
from core.screen_metadata_policy import screen_sources

_slots = asyncio.Semaphore(2)


def artwork_candidates(raw, types, kind):
    taxonomy = {row['id']: row for row in types if isinstance(row, dict) and row.get('recordType') == kind}
    result = {'posters': [], 'backdrops': []}
    for row in raw.get('artworks') or []:
        info = taxonomy.get(row.get('type'), {})
        name = str(info.get('name') or '').casefold()
        if name not in ('poster', 'background'):
            continue
        language = row.get('language')
        if language not in (None, '', 'eng', 'en'):
            continue
        if name == 'background' and row.get('includesText') is not False:
            continue
        url = tvdb._image_url(row.get('image'))
        if not url or not url.startswith('https://artworks.thetvdb.com/'):
            continue
        result['posters' if name == 'poster' else 'backdrops'].append({
            'file_path': url, 'thumbnail': tvdb._image_url(row.get('thumbnail')),
            'iso_639_1': 'en' if name == 'poster' and language in ('en', 'eng') else None,
            'width': row.get('width') or info.get('width'), 'height': row.get('height') or info.get('height'),
            'vote_count': row.get('score') or 0})
    return result


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
                response = await tvdb._get('/search/remoteid/' + imdb, tvdb_key)
                candidates = {row[kind]['id'] for row in response.get('data') or []
                              if isinstance(row.get(kind), dict) and type(row[kind].get('id')) is int}
                native = next(iter(candidates)) if len(candidates) == 1 else None
        if not native:
            raise ValueError('No verified TVDB title identity')
        path = f'/series/{native}/artworks' if kind == 'series' else f'/movies/{native}/extended'
        raw = (await tvdb._get(path, tvdb_key)).get('data') or {}
        if str(raw.get('id')) != str(native) or not isinstance(raw.get('artworks'), list):
            raise ValueError('Invalid TVDB artwork response')
        types = (await tvdb._get('/artwork/types', tvdb_key)).get('data') or []
    return artwork_candidates(raw, types, kind)
