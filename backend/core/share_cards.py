"""Small, deterministic social images. Pillow work runs in a worker thread."""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

SIZE = (1200, 630)
FONT = Path(__file__).resolve().parents[1] / 'assets/fonts/Overpass.ttf'


def font(size, bold=False):
    value = ImageFont.truetype(str(FONT), size)
    value.set_variation_by_axes([700 if bold else 400])
    return value


def fitted(draw, text, face, width):
    text = ' '.join(str(text or '').split())
    if draw.textlength(text, font=face) <= width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if draw.textlength(text[:middle].rstrip() + '…', font=face) <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + '…'


def text_block(draw, text, xy, width, size=56, lines=2, fill='white', bold=True):
    face = font(size, bold)
    words = ' '.join(str(text or '').split()).split()
    rows = []
    while words and len(rows) < lines:
        row = words.pop(0)
        while words and draw.textlength(row + ' ' + words[0], font=face) <= width:
            row += ' ' + words.pop(0)
        if len(rows) == lines - 1 and words:
            row += ' ' + ' '.join(words)
            words = []
        rows.append(fitted(draw, row, face, width))
    for index, row in enumerate(rows):
        draw.text((xy[0], xy[1] + index * (size + 7)), row, font=face, fill=fill)
    return xy[1] + len(rows) * (size + 7)


def decoded(content):
    if not content:
        return None
    try:
        with Image.open(BytesIO(content)) as source:
            if source.width * source.height > 20_000_000:
                return None
            return ImageOps.exif_transpose(source).convert('RGBA')
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def render_card(card, primary=None, background=None):
    portrait, backdrop = decoded(primary), decoded(background)
    image = Image.new('RGB', SIZE, '#101d2c')
    if backdrop:
        backdrop = ImageOps.fit(backdrop, SIZE, method=Image.Resampling.LANCZOS)
        if card['kind'] != 'profile':
            backdrop = backdrop.filter(ImageFilter.GaussianBlur(18))
        image = ImageEnhance.Brightness(backdrop).enhance(.5 if card['kind'] == 'profile' else .38)
    shade = Image.new('RGBA', SIZE)
    gradient = ImageDraw.Draw(shade)
    for y in range(SIZE[1]):
        gradient.line((0, y, SIZE[0], y), fill=(8, 17, 29, int(35 + 170 * (y / SIZE[1]) ** 1.5)))
    image = Image.alpha_composite(image.convert('RGBA'), shade)
    draw = ImageDraw.Draw(image)
    draw.text((54, 34), 'AnyList', font=font(26, True), fill='#b6c7d9')
    profile = card['kind'] == 'profile'
    box = (54, 151, 264, 361) if profile else (856, 92, 1146, 527)
    if portrait:
        size = (box[2] - box[0], box[3] - box[1])
        tile = ImageOps.contain(portrait, size, method=Image.Resampling.LANCZOS) if card['kind'] == 'studio' else ImageOps.fit(portrait, size, method=Image.Resampling.LANCZOS)
        surface = Image.new('RGB', size, '#192b3d')
        surface.paste(tile, ((size[0] - tile.width) // 2, (size[1] - tile.height) // 2), tile)
        mask = Image.new('L', size)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, *size), radius=18, fill=255)
        image.paste(surface, box[:2], mask)
    elif profile:
        draw.rounded_rectangle(box, radius=18, fill='#23394e')
        draw.text((109, 191), card['title'][:1].upper(), font=font(92, True), fill='#d9e5ef')
    x, width = (306, 835) if profile else (54, 740)
    y = 157 if profile else 124
    if card.get('eyebrow'):
        y = text_block(draw, card['eyebrow'], (x, y), width, 25, 1, '#b9cbdc', False) + 18
    y = text_block(draw, card['title'], (x, y), width, 62 if profile else 58, 2) + 16
    if card.get('subtitle'):
        text_block(draw, card['subtitle'], (x, y), width, 25, 1, '#c9d6e2', False)
    if card.get('genres'):
        # Flow genres rather than shrinking them to unreadable metadata.
        gx, gy = 54, 453
        face = font(22)
        for genre in card['genres'][:6]:
            label = fitted(draw, genre, face, 250)
            w = int(draw.textlength(label, font=face)) + 28
            if gx + w > 793:
                gx, gy = 54, gy + 49
            if gy > 510:
                break
            draw.rounded_rectangle((gx, gy, gx + w, gy + 37), radius=10, fill='#2a3949')
            draw.text((gx + 14, gy + 3), label, font=face, fill='#e4edf4')
            gx += w + 10
    stats = card.get('stats', [])
    for index, stat in enumerate(stats[:3]):
        sx = 54 + index * (360 if profile else 250)
        draw.text((sx, 450 if profile else 365), str(stat['value']), font=font(47, True), fill='#f4cf76' if stat['label'] == 'IMDb' else '#d4e8f7')
        draw.text((sx, 511 if profile else 421), stat['label'], font=font(23), fill='#c5d3df')
    result = BytesIO()
    image.convert('RGB').save(result, format='JPEG', quality=88, optimize=True)
    return result.getvalue()
