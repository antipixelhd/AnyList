"""Preview projections for shareable content, independent of the viewing user."""
import asyncio
import hashlib
import ipaddress
import json
import re
import socket
from collections import OrderedDict
from datetime import date
from time import monotonic
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import HTTPException

from core.config import settings
from core.share_cards import render_card
from core.credit_roles import creator_role

_images = OrderedDict()
_pending = {}
_slots = asyncio.Semaphore(2)
MAX_IMAGES = 64
MAX_BYTES = 16 * 1024 * 1024


def target(path):
    """Accept identities, never arbitrary upstream URLs or viewer query state."""
    path = unquote(path)
    games = bool(re.fullmatch(r'/(actors|person|studio)/((catalogue|tmdb):)?[1-9][0-9]{0,9}/?\?media_type=game', path))
    if games:
        path = path[:-len('?media_type=game')]
    if match := re.fullmatch(r'/(title|games)/([1-9][0-9]{0,9})/?', path):
        if int(match[2]) <= 2147483647:
            return match[1], match[2]
    if match := re.fullmatch(r'/user/([^/?#\\]{1,100})(?:/(?:movies|series|games|list|social|stats|favorites|library))?/?', path):
        return 'profile', match[1]
    if match := re.fullmatch(r'/(actors|person|studio)/((?:catalogue|tmdb):[1-9][0-9]{0,9}|[1-9][0-9]{0,9})/?', path):
        key = match[2] if ':' in match[2] else 'tmdb:' + match[2]
        if int(key.split(':')[1]) <= 2147483647:
            kind = {'actors':'actor', 'person':'staff', 'studio':'studio'}[match[1]]
            return ('game_' + kind if games else kind), key
    raise HTTPException(404, 'Preview not found')


def age(birthday, deathday=None):
    try:
        born = date.fromisoformat(birthday)
        end = date.fromisoformat(deathday) if deathday else date.today()
        if not born <= end <= date.today():
            return None
        return end.year - born.year - ((end.month, end.day) < (born.month, born.day))
    except (ValueError, TypeError):
        return None


def media_card(data):
    kind = data['type']
    metadata = ['Expansion' if kind == 'game' and data.get('game_class') == 'expansion' else dict(movie='Movie', series='Series', game='Game', book='Book').get(kind), data.get('year')]
    if kind == 'movie' and data.get('runtime'):
        minutes = int(data['runtime'])
        metadata.append(f'{minutes // 60}h {minutes % 60}m' if minutes >= 60 else f'{minutes}m')
    if kind == 'series':
        for field, label in [('season_count', 'seasons'), ('episode_count', 'episodes')]:
            if data.get(field):
                metadata.append(f'{data[field]} {label}')
    creators = [p['name'] for p in data.get('people', []) if p.get('kind') != 'actor'
                and any(creator_role(role) for role in p.get('roles', []))]
    if not creators:
        creators = [p['name'] for p in data.get('creators', [])]
    score = data.get('imdb_score')
    return dict(kind='media', title=data['title'], eyebrow=' · '.join(str(v) for v in metadata if v),
                subtitle=', '.join(dict.fromkeys(creators))[:180], genres=data.get('genres', []),
                stats=[{'value':f'{float(score):.1f}', 'label':'IMDb'}] if score is not None else [],
                primary=data.get('poster'), background=data.get('backdrop'))


def game_preview_artwork(url, *, landscape=False):
    if not isinstance(url, str):
        return url
    parsed = urlsplit(url)
    if parsed.hostname != 'images.igdb.com' or parsed.scheme != 'https':
        return url
    bucket = '1080p' if landscape else 'cover_big_2x'
    return re.sub(r'/t_[^/]+/', f'/t_{bucket}/', url, count=1)


def profile_card(user, overview):
    formats = {row['key']:row['titles'] for row in (overview or {}).get('formats', [])}
    stats = [] if overview is None else [
        {'value':str(formats.get('movie', 0)), 'label':'Movies watched'},
        {'value':str(formats.get('series', 0)), 'label':'Series watched'},
        {'value':f"{overview['totals']['watch_days']:.1f}", 'label':'Days watched'},
    ]
    return dict(kind='profile', title=user.display_name or user.username, eyebrow=f'@{user.username}', subtitle='',
                stats=stats, primary=None, background=user.profile.background_url if user.profile else None)


async def card_data(db, path):
    from core.tracking_access import catalog_access, profile_access, anime_is_visible
    from core import tracking_projection
    from models.media import Media
    from models.base import MediaType
    from models.statistics import UserStatsSnapshot, UserStatsState
    from core.statistics_snapshots import CONTRACT_VERSION

    kind, key = target(path)
    game_contributor = kind.startswith('game_')
    if kind == 'games' or game_contributor or re.fullmatch(r'/user/[^/?#\\]{1,100}/games/?', unquote(path)):
        raise HTTPException(403, 'Games access requires approval')
    if game_contributor:
        kind = kind.removeprefix('game_')
    # This check deliberately runs before every cache lookup, even with a cookie.
    await catalog_access(db, None)
    avatar = None
    if kind == 'profile':
        user, _ = await profile_access(db, key, None)
        state = await db.get(UserStatsState, user.id)
        snapshot = await db.get(UserStatsSnapshot, state.active_snapshot_id) if state and state.active_snapshot_id else None
        overview = snapshot.payload.get('all') if snapshot and snapshot.contract_version == CONTRACT_VERSION else None
        card = profile_card(user, overview)
        if user.profile and user.profile.avatar_path:
            avatar = settings.data_dir / 'avatars' / user.profile.avatar_path
            if not avatar.resolve().is_relative_to((settings.data_dir / 'avatars').resolve()):
                avatar = None
        # The file revision changes the render key after an avatar replacement.
        if avatar:
            try:
                card['avatar_revision'] = avatar.stat().st_mtime_ns
            except OSError:
                avatar = None
    elif kind == 'title':
        media = await db.get(Media, int(key))
        if not media or media.media_type not in (MediaType.movie, MediaType.series):
            raise HTTPException(404, 'Title not found')
        if tracking_projection.is_anime(media) and not await anime_is_visible(db):
            raise HTTPException(404, 'Title not found')
        from core.media_details import screen_work, work_details
        details = await work_details(db, await screen_work(db, media), extras=False)
        card = media_card({**tracking_projection.media_data(media), **details})
    elif kind == 'games':
        from models.catalogue import CatalogueEntity
        from core.game_types import capabilities
        from core.media_details import work_details
        from core.catalogue_normalize import image
        from core.game_tracking import base_work_id
        work = await db.get(CatalogueEntity, int(key))
        if not work or work.kind != 'game' or not capabilities(work.attributes).searchable:
            raise HTTPException(404, 'Game not found')
        attrs = work.attributes or {}
        details = await work_details(db, work, extras=False)
        card = media_card({**details, 'type': 'game', 'game_class': attrs.get('game_class'),
            'title': work.name, 'year': str(attrs.get('release_date') or '')[:4],
            'poster': game_preview_artwork(image(work.image_url)),
            'backdrop': game_preview_artwork(next(iter(details.get('artwork') or []), None), landscape=True),
            'genres': [g if isinstance(g, str) else g['name'] for g in attrs.get('genres') or []
                       if isinstance(g, str) or isinstance(g, dict) and g.get('name')]})
        if capabilities(attrs).expansion_page:
            try:
                await base_work_id(db, work)
            except HTTPException:
                raise HTTPException(404, 'Verified base game not found') from None
    else:
        from core.contributor_details import contributor_page
        async with asyncio.timeout(6):
            data = await contributor_page(db, None, kind=kind, key=key, source='local', sharing=True,
                media_type='game' if game_contributor else 'all')
        if not data:
            raise HTTPException(404, 'Contributor not found')
        summary = data['share_summary']
        years = age(data.get('birthday'), data.get('deathday')) if kind != 'studio' else None
        stats = ([{'value':str(years), 'label':'Age at death' if data.get('deathday') else 'Age'}] if years is not None else [])
        stats += ([{'value':str(summary['games']), 'label':'Known games'}] if game_contributor else
            [{'value':str(summary['movies']), 'label':'Movies'}, {'value':str(summary['series']), 'label':'Series'}])
        card = dict(kind=kind, title=data['name'], eyebrow=' · '.join(data['roles'][:3]) or ('Studio' if kind == 'studio' else 'Actor' if kind == 'actor' else data.get('department') or 'Staff'),
                    subtitle='', stats=stats, primary=data.get('image'),
                    background=game_preview_artwork(summary['background'], landscape=True) if game_contributor else summary['background'])
    return card, avatar


def description(card):
    return ' · '.join(filter(None, [card.get('eyebrow'), card.get('subtitle'), ', '.join(card.get('genres', [])),
        *[f"{s['value']} {s['label']}" for s in card.get('stats', [])]]))[:350]


async def fetch_image(url):
    """Public HTTPS artwork only, pinned to a validated IP; no redirects/cookies."""
    if not url:
        return None
    if url.startswith('/') and not url.startswith('//'):
        url = 'https://image.tmdb.org/t/p/w780' + url
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        if parsed.hostname == 'image.tmdb.org':
            url = re.sub(r'(/t/p/)(?:w[0-9]+|original)/', r'\g<1>w780/', url)
            parsed = urlsplit(url)
        async with asyncio.timeout(3):
            addresses = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            ips = [address[4][0] for address in addresses]
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                return None
            ip = f'[{ips[0]}]' if ':' in ips[0] else ips[0]
            pinned = f'https://{ip}{parsed.path}' + (f'?{parsed.query}' if parsed.query else '')
            async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
                async with client.stream('GET', pinned, headers={'Host':parsed.hostname}, extensions={'sni_hostname':parsed.hostname}) as response:
                    if response.status_code != 200 or not response.headers.get('content-type', '').startswith('image/'):
                        return None
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 4_000_000:
                            return None
                    return bytes(content)
    except (OSError, ValueError, httpx.HTTPError, TimeoutError):
        return None


async def image_bytes(card, avatar=None):
    key = hashlib.sha256(json.dumps(card, sort_keys=True).encode()).hexdigest()
    cached = _images.get(key)
    if cached and monotonic() - cached[0] < 300:
        _images.move_to_end(key)
        return cached[1]
    if key in _pending:
        return await asyncio.shield(_pending[key])
    if len(_pending) >= 8:
        raise HTTPException(503, 'Preview renderer busy')

    async def build():
        async with _slots:
            primary, background = await asyncio.gather(fetch_image(card.get('primary')), fetch_image(card.get('background')))
            if avatar:
                try:
                    primary = await asyncio.to_thread(avatar.read_bytes)
                except OSError:
                    pass
            result = await asyncio.to_thread(render_card, card, primary, background)
            _images[key] = (monotonic(), result)
            while len(_images) > MAX_IMAGES or sum(len(row[1]) for row in _images.values()) > MAX_BYTES:
                _images.popitem(last=False)
            return result

    task = _pending[key] = asyncio.create_task(build())
    task.add_done_callback(lambda _: _pending.pop(key, None))
    return await asyncio.shield(task)
