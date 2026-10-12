"""Bounded thumbnail analysis; originals are never modified or downloaded."""
import asyncio
from dataclasses import dataclass
from functools import cached_property
from io import BytesIO
import json
import math
from operator import mul
import re
from urllib.parse import urlsplit
import uuid

import httpx
from PIL import Image

from core.config import settings

ANALYSIS_VERSION = 2
_downloads = asyncio.Semaphore(6)
_processing = asyncio.Semaphore(2)


def fingerprint(content):
    with Image.open(BytesIO(content)) as source:
        if source.width * source.height > 4_000_000:
            raise ValueError('Oversized thumbnail')
        pixels = source.convert('RGB').resize((32, 24), Image.Resampling.LANCZOS)
    gray = pixels.convert('L')
    # Foreground highlights carry more weight than empty sky/edge padding.
    weights = [(x, y, max(gray.getpixel((x, y))-30, 0)**2) for y in range(2, 22) for x in range(2, 30)]
    total = sum(weight for _, _, weight in weights) or 1
    center_x = sum(x * weight for x, _, weight in weights) / total / 31
    center_y = sum(y * weight for _, y, weight in weights) / total / 23
    return {'version': ANALYSIS_VERSION, 'gray': list(gray.get_flattened_data()),
        'color': [round(sum(channel) / len(channel), 2) for channel in zip(*pixels.get_flattened_data())],
        'centering': round(abs(center_x - .5) + abs(center_y - .5), 4)}


def _fallback_sumprod(first, second):
    return sum(map(mul, first, second))


_sumprod = getattr(math, 'sumprod', _fallback_sumprod)


def _window_stats(pixels):
    total = sum(pixels)
    return pixels, total, _sumprod(pixels, pixels) - total * total / len(pixels)


@dataclass
class _Composition:
    gray: tuple
    color: list
    center: tuple

    @cached_property
    def windows(self):
        # Each selected thumbnail's translated windows are shared by every
        # comparison in this gallery, then released with the selection call.
        return [_window_stats(tuple(self.gray[(y+dy)*32+x+dx]
                    for y in range(6, 18) for x in range(8, 24)))
                for dy in range(-4, 5) for dx in range(-4, 5)]


def _prepare_composition(analysis):
    if not analysis or len(analysis.get('gray', [])) != 768:
        return None
    gray = tuple(analysis['gray'])
    return _Composition(gray, analysis['color'],
                        _window_stats(tuple(gray[y*32+x] for y in range(6, 18) for x in range(8, 24))))


def _same_composition(a, b):
    """Match translations of the central photograph, tolerating title overlays.

    Contrast-normalized comparison rejects unrelated similarly dark scenes.
    It deliberately favors false negatives over collapsing different artwork.
    """
    if not a or not b:
        return False
    if sum(abs(x-y) for x, y in zip(a.color, b.color)) / 3 > 28:
        return False
    first, total_a, energy_a = a.center
    if energy_a < 192 * 30:
        return False
    for second, total_b, energy_b in b.windows:
        if energy_b < 192 * 30:
            continue
        covariance = _sumprod(first, second) - total_a * total_b / len(first)
        if covariance / (energy_a*energy_b)**.5 >= .965:
            return True
    return False


def same_composition(a, b):
    return _same_composition(_prepare_composition(a), _prepare_composition(b))


async def inspect_image(url, client):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'image.tmdb.org' or not re.fullmatch(r'/t/p/original/[A-Za-z0-9_.-]+\.(jpg|png|webp)', parsed.path):
        return None
    cache_path = settings.data_dir / 'image_analysis' / (parsed.path.rsplit('/', 1)[-1] + '.json')
    try:
        cached = json.loads(await asyncio.to_thread(cache_path.read_text))
        valid = (isinstance(cached, dict) and isinstance(cached.get('gray'), list) and len(cached['gray']) == 768
                 and all(isinstance(value, int) and 0 <= value <= 255 for value in cached['gray'])
                 and isinstance(cached.get('color'), list) and len(cached['color']) == 3
                 and all(isinstance(value, (int, float)) for value in cached['color'])
                 and isinstance(cached.get('centering'), (int, float)))
        if valid and cached.get('version') == ANALYSIS_VERSION:
            return cached
    except (OSError, ValueError, TypeError):
        pass
    try:
        async with _downloads:
            content = bytearray()
            async with client.stream('GET', url.replace('/original/', '/w500/'), timeout=8, follow_redirects=False) as response:
                response.raise_for_status()
                if not response.headers.get('content-type', '').startswith('image/'):
                    return None
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 2_000_000:
                        return None
        async with _processing:
            result = await asyncio.to_thread(fingerprint, bytes(content))
        def save():
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_text(json.dumps(result))
                temporary.replace(cache_path)
            finally:
                temporary.unlink(missing_ok=True)
        try:
            await asyncio.to_thread(save)
        except OSError:
            pass  # Analysis still works when the optional disk cache is read-only.
        return result
    except (httpx.HTTPError, OSError, ValueError, Image.DecompressionBombError):
        return None


def variant_selection(rows):
    def quality(row):
        analysis = row.get('analysis') or {}
        return (not row.get('primary'), row.get('language') != 'en', analysis.get('centering', 1),
                -row.get('width', 0), -row.get('votes', 0))
    selected, replacements = [], {}
    compositions = {}
    def composition(row):
        analysis = row.get('analysis')
        key = id(analysis)
        if key not in compositions:
            compositions[key] = _prepare_composition(analysis)
        return compositions[key]
    for row in sorted(rows, key=quality):
        prepared = composition(row)
        duplicate = next((other for other in selected if _same_composition(prepared, composition(other))), None)
        if duplicate:
            replacements[row['url']] = duplicate['url']
        else:
            selected.append(row)
    selected.sort(key=lambda row: (not row.get('primary'), row.get('language') != 'en', -row.get('votes', 0), -row.get('width', 0)))
    return {'images': [row['url'] for row in selected][:10], 'replacements': replacements}


def select_variants(rows):
    return variant_selection(rows)['images']


async def select_galleries(candidates):
    async with _processing:
        return await asyncio.to_thread(lambda: {field: variant_selection(rows) for field, rows in candidates.items()})
