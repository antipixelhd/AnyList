import asyncio
import logging
import re
import uuid
from weakref import WeakValueDictionary
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from sqlalchemy import select, func, delete as sa_delete, update
from sqlalchemy.exc import IntegrityError

from db import AsyncSessionLocal
from core.config import settings
from models.image_cache import ImageCache
from models.media import Media
from models.show import Show as ShowModel
from models.collection import Collection
from models.tracking import TrackedEntry
from models.global_settings import GlobalSettings

logger = logging.getLogger(__name__)

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
TVDB_IMAGE_BASE = "https://artworks.thetvdb.com"

# Valid TMDB image sizes to prevent traversal / arbitrary requests
ALLOWED_SIZES = {"w92", "w154", "w185", "w300", "w342", "w500", "w780", "w1280", "original"}

# Synthetic "size" bucket for TheTVDB artwork, which has no size variants - the
# path alone identifies the image. Stored under image_cache/tvdb/<path>.
TVDB_SIZE = "tvdb"


def upstream_image_url(size: str, path: str) -> str:
    """Origin URL for a cached image, by size bucket."""
    if size == TVDB_SIZE:
        return f"{TVDB_IMAGE_BASE}{path}"
    return f"{TMDB_IMAGE_BASE}/{size}{path}"


_last_prune_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
_PRUNE_INTERVAL = timedelta(minutes=5)

# asyncpg hard limit is 32767 parameters per query; stay well under it
_MAX_IN_PARAMS = 30_000
_downloads = asyncio.Semaphore(3)
_fills = WeakValueDictionary()
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_ACCESS_INTERVAL = timedelta(hours=1)


def parse_image_url(url: str) -> tuple[Optional[str], Optional[str]]:
    """Parse a TMDB or TheTVDB image URL (or bare TMDB path) into a
    (size, path) tuple the cache is keyed by. TheTVDB artwork uses the
    synthetic ``tvdb`` size bucket. Returns (None, None) for anything else."""
    if not url:
        return None, None

    if "image.tmdb.org" in url:
        match = re.search(r"image\.tmdb\.org/t/p/([^/]+)(/.+)$", url)
        if match:
            return match.group(1), match.group(2)

    if "artworks.thetvdb.com" in url:
        match = re.search(r"artworks\.thetvdb\.com(/.+)$", url)
        if match:
            return TVDB_SIZE, match.group(1)

    if url.startswith("/"):
        return "w500", url

    return None, None


# Back-compat alias (was the only public name before TheTVDB support).
parse_tmdb_url = parse_image_url


async def download_and_cache_image(db, size: str, path: str, image_type: str = "ondemand") -> Optional[str]:
    lock = _fills.setdefault((size, path), asyncio.Lock())
    async with lock:
        return await _download_and_cache_image(db, size, path, image_type)


async def _download_and_cache_image(db, size: str, path: str, image_type: str) -> Optional[str]:
    """Download an image from TMDB or TheTVDB and cache it locally."""
    if size != TVDB_SIZE and size not in ALLOWED_SIZES:
        return None
    if ".." in path or not path.startswith("/"):
        return None
    if len(path) > 255:  # ImageCache.path column width; skip caching, let the caller redirect
        return None

    stmt = select(ImageCache).where(ImageCache.path == path, ImageCache.size == size)
    cached = (await db.execute(stmt)).scalar_one_or_none()

    local_path = settings.data_dir / "image_cache" / size / path.lstrip("/")

    if cached:
        if await asyncio.to_thread(local_path.exists):
            now = datetime.now(timezone.utc)
            touched = cached.last_accessed.replace(tzinfo=timezone.utc) if cached.last_accessed.tzinfo is None else cached.last_accessed
            promote = image_type == 'collected' and cached.image_type != 'collected'
            if now - touched >= _ACCESS_INTERVAL or promote:
                cached.last_accessed = now
                if promote: cached.image_type = 'collected'
                await db.commit()
            return str(local_path)
        else:
            await db.delete(cached)
            await db.commit()

    # Fetch from the origin (TMDB or TheTVDB)
    # Resize variants of a warmed primary retain its eviction priority.
    if image_type == 'ondemand':
        primary = await db.scalar(select(ImageCache.id).where(ImageCache.path == path,
            ImageCache.image_type == 'collected',
            ImageCache.size == TVDB_SIZE if size == TVDB_SIZE else ImageCache.size != TVDB_SIZE).limit(1))
        if primary is not None: image_type = 'collected'
    url = upstream_image_url(size, path)
    try:
        async with _downloads, httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            async with client.stream('GET', url) as r:
                if r.status_code != 200 or not r.headers.get('content-type','').startswith('image/'):
                    return None
                content = bytearray()
                async for chunk in r.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_IMAGE_BYTES: return None
            def save():
                local_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = local_path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
                try:
                    temporary.write_bytes(content)
                    temporary.replace(local_path)
                finally: temporary.unlink(missing_ok=True)
            await asyncio.to_thread(save)

            cache_entry = ImageCache(
                path=path,
                size=size,
                image_type=image_type,
                file_size=len(content),
                last_accessed=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )
            try:
                db.add(cache_entry)
                await db.commit()
            except IntegrityError:
                # Another worker may already have inserted this image.
                await db.rollback()
                result = await db.execute(
                    select(ImageCache).where(ImageCache.path == path, ImageCache.size == size)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.last_accessed = datetime.now(timezone.utc)
                    await db.commit()
            return str(local_path)
    except Exception as e:
        logger.error(f"Error downloading image {url}: {e}")

    return None


async def _prune_type(db, image_type: str, total_size: int, limit_bytes: int) -> int:
    """Delete oldest images of the given type until total_size is within limit. Returns updated total_size."""
    rows = (await db.execute(
        select(ImageCache.id, ImageCache.size, ImageCache.path, ImageCache.file_size)
        .where(ImageCache.image_type == image_type)
        .order_by(ImageCache.last_accessed.asc())
    )).all()

    ids_to_delete = []
    for row in rows:
        if total_size <= limit_bytes:
            break
        local_path = settings.data_dir / "image_cache" / row.size / row.path.lstrip("/")
        try:
            if local_path.exists():
                local_path.unlink()
        except Exception as e:
            logger.error(f"Failed to delete cached image file {local_path}: {e}")
        total_size -= row.file_size
        ids_to_delete.append(row.id)

    for i in range(0, len(ids_to_delete), _MAX_IN_PARAMS):
        chunk = ids_to_delete[i : i + _MAX_IN_PARAMS]
        await db.execute(sa_delete(ImageCache).where(ImageCache.id.in_(chunk)))

    return total_size


async def prune_cache(db, limit_gb: float):
    """Evict images from cache when exceeding the limit. Prunes on-demand first, then collected."""
    if not limit_gb or limit_gb <= 0:
        return

    limit_bytes = limit_gb * 1024 * 1024 * 1024

    total_size = (await db.execute(select(func.sum(ImageCache.file_size)))).scalar() or 0
    if total_size <= limit_bytes:
        return

    logger.info(f"Cache size ({total_size} bytes) exceeds limit ({limit_bytes} bytes). Pruning...")

    total_size = await _prune_type(db, "ondemand", total_size, limit_bytes)
    if total_size > limit_bytes:
        await _prune_type(db, "collected", total_size, limit_bytes)

    await db.commit()


async def prune_cache_bg(limit_gb: float):
    """Throttled background prune: runs at most once every 5 minutes."""
    global _last_prune_at
    if not limit_gb or limit_gb <= 0:
        return
    now = datetime.now(timezone.utc)
    if now - _last_prune_at < _PRUNE_INTERVAL:
        return
    _last_prune_at = now
    async with AsyncSessionLocal() as db:
        await prune_cache(db, limit_gb)


async def pre_cache_all_collected(db):
    """Find all image paths in user collections and pre-cache them."""
    images_to_cache: set[tuple[str,str]] = set()
    def queue(url, preferred_size):
        size, path = parse_image_url(url)
        if size and path: images_to_cache.add((TVDB_SIZE if size == TVDB_SIZE else preferred_size, path))

    # Include current tracking entries as well as legacy collections. Read
    # only the small curated image fields, never whole provider documents.
    from types import SimpleNamespace
    from core.media_images import screen_backdrop, IMAGE_POLICY_VERSION
    for poster_path, backdrop_path, kind, imdb, external_ids, gallery, backdrop in (await db.execute(
        select(Media.poster_path, Media.backdrop_path, Media.media_type, Media.imdb_id, Media.tmdb_data['external_ids'],
            Media.tmdb_data['_anylist_images'], Media.tmdb_data['_anylist_backdrop'])
        .where(Media.id.in_(select(Collection.media_id)) | Media.id.in_(select(TrackedEntry.media_id)))
        .distinct()
    )).all():
        if gallery and gallery.get('version') == IMAGE_POLICY_VERSION:
            poster_path = gallery.get('primary_poster') or poster_path
        preferred = screen_backdrop(SimpleNamespace(media_type=kind,imdb_id=imdb,tmdb_data={
            'external_ids':external_ids or {}, '_anylist_images':gallery or {}, '_anylist_backdrop':backdrop or {}}))['backdrop']
        if poster_path: queue(poster_path, 'w342')
        if preferred or backdrop_path: queue(preferred or backdrop_path, 'w1280')

    # Shows with collected episodes — select only the columns we need
    for poster_path, backdrop_path, seasons in (await db.execute(
        select(ShowModel.poster_path, ShowModel.backdrop_path, ShowModel.tmdb_data['seasons'])
        .join(Media, Media.show_id == ShowModel.id)
        .join(Collection, Collection.media_id == Media.id)
        .distinct()
    )).all():
        if poster_path:
            queue(poster_path, 'w342')
        if backdrop_path:
            queue(backdrop_path, 'w1280')
        if isinstance(seasons, list):
            for season in seasons:
                sp = season.get("poster_path")
                if sp:
                    queue(sp, 'w342')

    parsed_images = sorted(images_to_cache, key=lambda item:(item[0]=='w1280',item))

    if not parsed_images:
        return

    # Promote already viewed responsive variants without fetching them again.
    for tvdb in (False, True):
        paths = [path for size, path in parsed_images if (size == TVDB_SIZE) == tvdb]
        for offset in range(0, len(paths), _MAX_IN_PARAMS):
            await db.execute(update(ImageCache).where(ImageCache.path.in_(paths[offset:offset+_MAX_IN_PARAMS]),
                ImageCache.image_type == 'ondemand',
                ImageCache.size == TVDB_SIZE if tvdb else ImageCache.size != TVDB_SIZE).values(image_type='collected'))
    await db.commit()

    # Filter out already-cached entries
    cached_set: set[tuple[str, str]] = set((await db.execute(select(ImageCache.path, ImageCache.size).where(ImageCache.image_type=='collected'))).all())
    to_download = [img for img in parsed_images if (img[1], img[0]) not in cached_set]

    if not to_download:
        return

    logger.info(f"Pre-caching {len(to_download)} collected images in the background...")

    for size, path in to_download:
        await download_and_cache_image(db, size, path, image_type="collected")
        await asyncio.sleep(0.1)  # Throttle to avoid hitting TMDB rate limits


async def pre_cache_all_collected_bg():
    """Run pre-caching in the background with a dedicated session."""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(GlobalSettings.image_cache_enabled, GlobalSettings.image_cache_limit_gb)
            .where(GlobalSettings.id == 1)
        )).one_or_none()
        if not row or not row.image_cache_enabled:
            return
        await pre_cache_all_collected(db)
        if row.image_cache_limit_gb:
            await prune_cache(db, row.image_cache_limit_gb)


async def clear_image_cache(db):
    """Safely delete all local image cache files and clear database cache metadata."""
    import shutil

    await db.execute(sa_delete(ImageCache))
    await db.commit()

    cache_dir = settings.data_dir / "image_cache"
    if cache_dir.exists():
        try:
            shutil.rmtree(cache_dir)
        except Exception as e:
            logger.error(f"Failed to remove image cache directory: {e}")

    cache_dir.mkdir(parents=True, exist_ok=True)
