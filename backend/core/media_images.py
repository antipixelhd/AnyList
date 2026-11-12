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

IMAGE_POLICY_VERSION = 6
BACKGROUND_POLICY_VERSION = 7
_fills = WeakValueDictionary()
_backdrop_fills = WeakValueDictionary()
_preview_fills = WeakValueDictionary()
logger = logging.getLogger(__name__)


def image_key(url):
    value=re.sub(r'(https://image\.tmdb\.org/t/p/)[^/]+/', r'\1', url)
    return re.sub(r'(https://images\.igdb\.com/igdb/image/upload/)t_[^/]+/',r'\1',value)


def unique_images(values, *, limit=10):
    result, seen = [], set()
    for value in values:
        url = image(value)
        if url and image_key(url) not in seen:
            seen.add(image_key(url))
            result.append(url)
    return result if limit is None else result[:limit]


def tmdb_image_url(path):
    if isinstance(path, str) and re.fullmatch(r'/[A-Za-z0-9_.-]+\.(jpg|png|webp)', path):
        return 'https://image.tmdb.org/t/p/original' + path
    return image(path)


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
            # Only an explicit null language establishes textless artwork.
            if field == 'backdrops' and ('iso_639_1' not in row or row['iso_639_1'] is not None):
                continue
            ratio = row.get('aspect_ratio')
            if ratio is None and all(isinstance(row.get(field), (int, float)) and row[field] > 0 for field in ('height', 'width')):
                ratio = row['width'] / row['height']
            if isinstance(ratio, (int, float)) and (ratio >= 1 if field == 'posters' else ratio <= 1):
                continue
            path = row.get('file_path')
            url = tmdb_image_url(path) if provider == 'tmdb' else image(path)
            if url:
                result[target].append({'url': url, 'thumbnail': row.get('thumbnail'), 'language': row.get('iso_639_1'),
                    'width': row.get('width') or 0, 'votes': row.get('vote_count') or 0})
    for field, rows in result.items():
        if field == 'posters' and any(row['language'] == 'en' for row in rows):
            rows = [row for row in rows if row['language'] == 'en']
        seen = set()
        result[field] = [row for row in sorted(rows, key=lambda row: (row['language'] != 'en', -row['votes'], -row['width']))
            if not (image_key(row['url']) in seen or seen.add(image_key(row['url'])))][:60]
    return result


def provider_images(raw, provider='tmdb'):
    return {field: [row['url'] for row in rows] for field, rows in image_candidates(raw, provider).items()}


def metahub_backdrop(media):
    data=media.tmdb_data or {}
    kind=getattr(getattr(media,'media_type',None),'value',None)
    imdb=getattr(media,'imdb_id',None) or (data.get('external_ids') or {}).get('imdb_id') or data.get('imdb_id')
    if kind=='movie' and isinstance(imdb,str) and re.fullmatch(r'tt[0-9]+',imdb):
        return f'https://images.metahub.space/background/high/{imdb}/img'
    return None


def screen_backdrop(media):
    cache=(media.tmdb_data or {}).get('_anylist_backdrop') or {}
    direct=metahub_backdrop(media)
    fallbacks=[]
    if getattr(getattr(media,'media_type',None),'value',None)=='movie':
        gallery=(media.tmdb_data or {}).get('_anylist_images') or {}
        preview=(media.tmdb_data or {}).get('_anylist_artwork_preview') or {}
        previous=next((url for url in [preview.get('image'),*(gallery.get('artwork') or [])]
                       if isinstance(url,str) and url.startswith('https://artworks.thetvdb.com/')),None)
        fallbacks=unique_images([previous,tmdb_image_url(getattr(media,'backdrop_path',None))])
    fallback={'backdrop_fallback':next(iter(fallbacks),None),'backdrop_fallbacks':fallbacks}
    if direct:
        return {'backdrop':direct,**fallback,
                'background_status':{'state':'ready'}}
    retry=0
    try:
        if cache.get('version')==BACKGROUND_POLICY_VERSION:
            retry=max(0,int((datetime.fromisoformat(cache.get('retry_after',''))-datetime.now(timezone.utc)).total_seconds())+1)
    except (ValueError,TypeError):pass
    selected=image(cache.get('image')) if cache.get('version')==BACKGROUND_POLICY_VERSION else None
    return {'backdrop':selected or next(iter(fallbacks),None),**fallback,
        'background_status':{'state':'deferred' if retry else 'ready' if cache.get('version')==BACKGROUND_POLICY_VERSION else 'pending',
            'error':cache.get('last_error') if retry else None,'retry_after':retry}}


def best_tmdb_backdrop(raw):
    rows=[row for row in raw.get('backdrops') or [] if isinstance(row,dict)
          and 'iso_639_1' in row and row['iso_639_1'] is None
          and isinstance(row.get('file_path'),str)
          and re.fullmatch(r'/[A-Za-z0-9_.-]+\.(jpg|png|webp)',row['file_path'])
          and (row.get('aspect_ratio') or 2)>1]
    best=max(rows,key=lambda row:(row.get('vote_average') or 0,row.get('vote_count') or 0,row.get('width') or 0),default=None)
    return tmdb_image_url(best['file_path']) if best else None


async def fill_screen_backdrop(db,media,api_key):
    """Resolve only the hero: direct MetaHub movies or ranked textless TMDB series."""
    lock=_backdrop_fills.setdefault(media.id,asyncio.Lock())
    async with lock:
        await db.refresh(media)
        if metahub_backdrop(media):return
        if not api_key or not media.tmdb_id:return
        cache=dict((media.tmdb_data or {}).get('_anylist_backdrop') or {})
        if screen_backdrop(media)['background_status']['retry_after']:return
        now=datetime.now(timezone.utc)
        try:
            if cache.get('version')==BACKGROUND_POLICY_VERSION and datetime.fromisoformat(cache.get('expires_at',''))>now:return
        except (ValueError,TypeError):pass
        from core import tmdb
        try:
            if media.media_type.value=='movie':
                ids=await tmdb.get_external_ids(media.tmdb_id,'movie',api_key=api_key)
                imdb=ids.get('imdb_id')
                selected=f'https://images.metahub.space/background/high/{imdb}/img' if isinstance(imdb,str) and re.fullmatch(r'tt[0-9]+',imdb) else None
            else:
                raw=await tmdb.get_title_images(media.tmdb_id,'series',api_key=api_key)
                selected=best_tmdb_backdrop(raw)
            cache={'image':selected,'version':BACKGROUND_POLICY_VERSION,'fetched_at':now.isoformat(),'expires_at':(now+timedelta(days=30)).isoformat()}
        except Exception as error:
            logger.warning('Background artwork failed for media %s (%s)',media.id,type(error).__name__)
            delay=600
            if isinstance(error,httpx.HTTPStatusError) and error.response.status_code==429:
                from core.catalogue_providers import retry_after_seconds
                delay=max(delay,retry_after_seconds(error.response.headers.get('Retry-After',''),delay))
            if cache.get('version')!=BACKGROUND_POLICY_VERSION:cache={}
            cache.update(version=BACKGROUND_POLICY_VERSION,retry_after=(now+timedelta(seconds=delay)).isoformat(),last_error='rate_limited' if isinstance(error,httpx.HTTPStatusError) and error.response.status_code==429 else 'unavailable')
        await db.execute(update(Media).where(Media.id==media.id).values(
            tmdb_data=func.coalesce(Media.tmdb_data,literal({},type_=JSONB)).op('||')({'_anylist_backdrop':cache})))
        await db.commit();await db.refresh(media)


def screen_artwork_preview(media):
    cache=(media.tmdb_data or {}).get('_anylist_artwork_preview') or {}
    remaining=0
    try:remaining=max(0,int((datetime.fromisoformat(cache.get('expires_at',''))-datetime.now(timezone.utc)).total_seconds()))
    except (ValueError,TypeError):pass
    return {'artwork_preview':{'image':image(cache.get('image')),'thumbnail':image(cache.get('thumbnail'))},
            'preview_status':{'state':'deferred' if remaining and cache.get('last_error') else 'ready' if remaining else 'pending',
                              'retry_after':remaining if cache.get('last_error') else 0}}


async def fill_artwork_preview(db,media,api_key,tvdb_key):
    """One ranked, textless TVDB preview, without downloading or curating galleries."""
    if not tvdb_key:return
    lock=_preview_fills.setdefault(media.id,asyncio.Lock())
    async with lock:
        await db.refresh(media)
        cache=(media.tmdb_data or {}).get('_anylist_artwork_preview') or {}
        now=datetime.now(timezone.utc)
        try:
            if datetime.fromisoformat(cache.get('expires_at',''))>now:return
        except (ValueError,TypeError):pass
        from core.screen_artwork import title_artwork, ArtworkIdentityUnavailable
        try:
            raw=await title_artwork(db,media,tvdb_key,api_key)
            rows=image_candidates(raw,'tvdb')['artwork']
            first=next(iter(rows),{})
            cache={'image':first.get('url'),'thumbnail':image(first.get('thumbnail')),
                   'expires_at':(now+timedelta(days=30)).isoformat()}
        except ArtworkIdentityUnavailable:
            cache={'image':cache.get('image'),'thumbnail':cache.get('thumbnail'),
                   'expires_at':(now+timedelta(days=1)).isoformat()}
        except Exception as error:
            logger.warning('Artwork preview failed for media %s (%s)',media.id,type(error).__name__)
            # Preserve a usable preview and bound retries during provider outages.
            delay=600
            if isinstance(error,httpx.HTTPStatusError) and error.response.status_code==429:
                from core.catalogue_providers import retry_after_seconds
                delay=max(delay,retry_after_seconds(error.response.headers.get('Retry-After',''),delay))
            cache={**cache,'last_error':'unavailable','expires_at':(now+timedelta(seconds=delay)).isoformat()}
        await db.execute(update(Media).where(Media.id==media.id).values(
            tmdb_data=func.coalesce(Media.tmdb_data,literal({},type_=JSONB)).op('||')({'_anylist_artwork_preview':cache})))
        await db.commit();await db.refresh(media)


def screen_images(media, details):
    cache = (media.tmdb_data or {}).get('_anylist_images') or {}
    retry = 0
    try:
        retry = max(0, int((datetime.fromisoformat(cache.get('retry_after', '')) - datetime.now(timezone.utc)).total_seconds()) + 1)
    except (TypeError, ValueError):
        pass
    status = {'state': 'deferred' if retry else 'unavailable' if cache.get('unavailable') and cache.get('version') == IMAGE_POLICY_VERSION else 'ready' if cache.get('version') == IMAGE_POLICY_VERSION else 'pending',
              'error': cache.get('last_error') if retry else None, 'retry_after': retry}
    primary = tmdb_image_url((media.tmdb_data or {}).get('poster_path') or media.poster_path)
    background={**screen_backdrop(media),**screen_artwork_preview(media)}
    if cache.get('version') == IMAGE_POLICY_VERSION:
        primary = image(cache.get('primary_poster')) or primary
        posters = unique_images([primary, *(cache.get('posters') or [])])
        artwork = unique_images(cache.get('artwork') or [])
        return {'poster': primary or next(iter(posters), None), **background,
                'posters': posters, 'artwork': artwork,
                'artwork_thumbnails':cache.get('thumbnails') or {},
                'image_replacements': cache.get('replacements') or {}, 'image_status': status}
    # Never paint the old unfiltered gallery while a new policy is being filled.
    return {'poster': primary, **background, 'posters': unique_images([primary]),
            'artwork': [], 'image_replacements': {}, 'image_status': status}


async def fill_screen_images(db, media, api_key, tvdb_key=None):
    lock = _fills.setdefault(media.id, asyncio.Lock())
    async with lock:
        await db.refresh(media)
        await _fill_screen_images(db, media, api_key, tvdb_key)


async def _fill_screen_images(db, media, api_key, tvdb_key=None):
    if not tvdb_key:
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
    from core.screen_artwork import title_artwork, ArtworkIdentityUnavailable
    try:
        fetch_primary = tmdb.get_movie_light if media.media_type.value == 'movie' else tmdb.get_show_light
        raw = await title_artwork(db, media, tvdb_key, api_key)
        defaults = await fetch_primary(media.tmdb_id, api_key, language='en-US') if api_key and media.tmdb_id else {}
        from core.media_image_analysis import inspect_image, select_galleries
        candidates = image_candidates(raw, 'tvdb')
        primary = tmdb_image_url(defaults.get('poster_path') or (media.tmdb_data or {}).get('poster_path') or media.poster_path)
        if primary:
            primary_row = next((row for row in candidates['posters'] if image_key(row['url']) == image_key(primary)),
                               {'url': primary, 'language': 'en', 'width': 0, 'votes': 0})
            primary_row['primary'] = True
            candidates['posters'] = [primary_row, *(row for row in candidates['posters'] if image_key(row['url']) != image_key(primary))]
        async with httpx.AsyncClient() as client:
            for rows in candidates.values():
                analyses = await asyncio.gather(*(inspect_image(row['url'], client, row.get('thumbnail')) for row in rows))
                for row, analysis in zip(rows, analyses):
                    row['analysis'] = analysis
        selections = await select_galleries(candidates)
        selected = {field: selection['images'] for field, selection in selections.items()}
        selected['replacements'] = {source: target for selection in selections.values() for source, target in selection['replacements'].items()}
        thumbnails={row['url']:image(row.get('thumbnail')) for row in candidates['artwork'] if row.get('thumbnail')}
        cache = {**selected, 'primary_poster': primary, 'thumbnails':thumbnails,
                 'version': IMAGE_POLICY_VERSION, 'expires_at': (now + timedelta(days=30)).isoformat()}
    except ArtworkIdentityUnavailable:
        # Keep usable images, and recheck the bridge tomorrow rather than
        # retrying a known missing match as though the provider were down.
        if cache.get('version') != IMAGE_POLICY_VERSION:
            cache = {}
        cache={name:value for name,value in cache.items()
               if name not in ('retry_after','retry_version','last_error')}
        cache.update(version=IMAGE_POLICY_VERSION,unavailable='unmatched_title',
                     expires_at=(now+timedelta(days=1)).isoformat())
    except Exception as error:
        logger.warning('Image curation failed for media %s (%s)', media.id, type(error).__name__)
        delay = 600
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
            from core.catalogue_providers import retry_after_seconds
            delay = max(delay, retry_after_seconds(error.response.headers.get('Retry-After', ''), delay))
        cache['retry_after'] = (now + timedelta(seconds=delay)).isoformat()
        cache['retry_version'] = IMAGE_POLICY_VERSION
        cache['last_error'] = 'rate_limited' if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429 else 'unavailable'
    await db.execute(update(Media).where(Media.id == media.id).values(
        tmdb_data=func.coalesce(Media.tmdb_data, literal({}, type_=JSONB)).op('||')({'_anylist_images': cache})))
    await db.commit()
    await db.refresh(media)
