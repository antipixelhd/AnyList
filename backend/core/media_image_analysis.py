"""Bounded thumbnail analysis; originals are never modified or downloaded."""
import asyncio
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
import uuid

import httpx
from PIL import Image

from core.config import settings

ANALYSIS_VERSION = 1
_downloads = asyncio.Semaphore(6)
_processing = asyncio.Semaphore(2)
_brands = re.compile(r'\b(?:netflix|hulu|peacock|prime\s*video|amazon\s*original|apple\s*(?:tv|original)|hbo(?:\s*max)?)\b|\b(?:paramount|disney|t\s*v)\s*\+', re.I)


def fingerprint(content, *, text='', branding_checked=False):
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
        'centering': round(abs(center_x - .5) + abs(center_y - .5), 4),
        'branded': bool(_brands.search(text)), 'branding_checked': branding_checked}


def same_composition(a, b):
    """Match translations of the central photograph, tolerating title overlays.

    Contrast-normalized comparison rejects unrelated similarly dark scenes.
    It deliberately favors false negatives over collapsing different artwork.
    """
    if not a or not b or len(a.get('gray', [])) != 768 or len(b.get('gray', [])) != 768:
        return False
    if sum(abs(x-y) for x, y in zip(a['color'], b['color'])) / 3 > 28:
        return False
    first = [a['gray'][y*32+x] for y in range(6, 18) for x in range(8, 24)]
    mean_a = sum(first) / len(first)
    centered = [value-mean_a for value in first]
    energy_a = sum(value*value for value in centered)
    if energy_a < 192 * 30:
        return False
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            second = [b['gray'][(y+dy)*32+x+dx] for y in range(6, 18) for x in range(8, 24)]
            mean_b = sum(second) / len(second)
            energy_b = sum((value-mean_b)**2 for value in second)
            if energy_b < 192 * 30:
                continue
            correlation = sum(x*(y-mean_b) for x, y in zip(centered, second)) / (energy_a*energy_b)**.5
            if correlation >= .965:
                return True
    return False


def _inspect(content):
    result = fingerprint(content)
    binary = shutil.which('tesseract')
    text = ''
    if binary:
        # OCR is used solely to identify provider branding, not to store copy.
        with tempfile.TemporaryDirectory(prefix='anylist-image-') as directory:
            path = Path(directory) / 'thumb.jpg'
            path.write_bytes(content)
            try:
                completed = subprocess.run([binary, str(path), 'stdout', '-l', 'eng', '--psm', '11'],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=4, check=False)
                text = (completed.stdout or '') if completed.returncode == 0 else ''
                checked = completed.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                checked = False
    else:
        checked = False
    result.update(branded=bool(_brands.search(text)), branding_checked=checked)
    return result


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
        if valid and cached.get('version') == ANALYSIS_VERSION and (cached.get('branding_checked') or not shutil.which('tesseract')):
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
            result = await asyncio.to_thread(_inspect, bytes(content))
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
    clean = [row for row in rows if not (row.get('analysis') or {}).get('branded')]
    rows = clean or rows  # Preserve an image when every available version is branded.
    def quality(row):
        analysis = row.get('analysis') or {}
        return (row.get('language') != 'en', analysis.get('centering', 1),
                -row.get('width', 0), -row.get('votes', 0))
    selected, replacements = [], {}
    for row in sorted(rows, key=quality):
        duplicate = next((other for other in selected if same_composition(row.get('analysis'), other.get('analysis'))), None)
        if duplicate:
            replacements[row['url']] = duplicate['url']
        else:
            selected.append(row)
    # All text-bearing English variants precede the selected textless variants.
    selected.sort(key=lambda row: (row.get('language') != 'en', -row.get('votes', 0), -row.get('width', 0)))
    return {'images': [row['url'] for row in selected], 'replacements': replacements}


def select_variants(rows):
    return variant_selection(rows)['images']


async def select_galleries(candidates):
    async with _processing:
        return await asyncio.to_thread(lambda: {field: variant_selection(rows) for field, rows in candidates.items()})
