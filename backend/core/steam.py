"""Public-profile Steam reads. Personal responses never enter shared HTTP caches."""
import re
from datetime import datetime
from urllib.parse import urlsplit, unquote

from core.catalogue_providers import ProviderError, ProviderHTTP
from core.config import settings

API = 'https://api.steampowered.com/'


class SteamError(ValueError):
    pass


def valid_steam_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r'\d{17}', value)) and 76561197960265728 < int(value) <= 76561202255233023


def parse_profile(value):
    value = value.strip()
    if not value or len(value) > 300:
        raise SteamError('Enter a SteamID64, custom profile name, or Steam Community profile URL.')
    if '://' in value or '/' in value or value.lower().startswith(('steamcommunity.com', 'www.steamcommunity.com')):
        url = urlsplit(value if '://' in value else 'https://' + value)
        try:
            valid = (url.scheme in {'https', 'http'} and url.hostname in {'steamcommunity.com', 'www.steamcommunity.com'}
                     and not url.username and not url.password and not url.port)
        except ValueError:
            valid = False
        parts = url.path.strip('/').split('/')
        if not valid or len(parts) != 2 or parts[0] not in {'id', 'profiles'}:
            raise SteamError('Use a steamcommunity.com/id/name or steamcommunity.com/profiles/SteamID64 profile URL.')
        value = unquote(parts[1])
        if parts[0] == 'profiles' and not valid_steam_id(value):
            raise SteamError('Enter a valid SteamID64.')
    if valid_steam_id(value):
        return 'id', value
    if not re.fullmatch(r'[A-Za-z0-9_-]{2,100}', value) or value.isdigit():
        raise SteamError('Enter a valid SteamID64 or custom profile name.')
    return 'vanity', value


def achievement_data(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('playerstats'), dict):
        raise SteamError('Steam returned invalid achievement progress. Try again.')
    stats = raw.get('playerstats', {})
    if stats.get('success') is not True:
        # Privacy, unsupported stats and transient errors are unknown, never zero.
        return {'achievement_count': None, 'achievement_total': None}
    rows = stats.get('achievements')
    if not isinstance(rows, list) or len(rows) > 50000:
        return {'achievement_count': None, 'achievement_total': None}
    if any(not isinstance(row, dict) or row.get('achieved') not in (0, 1) for row in rows):
        raise SteamError('Steam returned invalid achievement progress. Try again.')
    return {'achievement_count': sum(row['achieved'] for row in rows), 'achievement_total': len(rows)}


class SteamSource:
    def __init__(self, factory=None, *, http=None, key=None):
        self.http = http or ProviderHTTP(session_factory=factory)
        self.key = settings.steam_web_api_key if key is None else key

    async def request(self, method, **params):
        if not self.key:
            raise SteamError('Steam is not configured. Ask the administrator to set the Steam Web API key.')
        try:
            return await self.http.request('steam', 'GET', API + method, cache=False,
                                           params={'key': self.key, 'format': 'json', **params})
        except ProviderError as error:
            if error.code in {'unauthorized', 'forbidden'}:
                raise SteamError('Steam denied access. Check the server API key and make your profile and Game details public.') from None
            raise SteamError('Steam is temporarily unavailable or its request budget is exhausted. Try again later.') from None

    async def profile(self, value):
        kind, native = parse_profile(value)
        if kind == 'vanity':
            raw = await self.request('ISteamUser/ResolveVanityURL/v1/', vanityurl=native)
            native = raw.get('response', {}).get('steamid')
            if not valid_steam_id(native):
                raise SteamError('Steam profile not found. Check the profile name or SteamID64.')
        raw = await self.request('ISteamUser/GetPlayerSummaries/v2/', steamids=native)
        players = raw.get('response', {}).get('players') or []
        player = next((p for p in players if p.get('steamid') == native), None)
        if not player:
            raise SteamError('Steam profile not found.')
        if player.get('communityvisibilitystate') != 3:
            raise SteamError('Your Steam profile is private. Set Profile and Game details to Public, then try again.')
        return {'steam_id': native, 'name': str(player.get('personaname') or 'Steam')[:255]}

    async def library(self, native):
        raw = await self.request('IPlayerService/GetOwnedGames/v1/', steamid=native,
                                 include_appinfo=1, include_played_free_games=1, skip_unvetted_apps=0)
        data = raw.get('response', {})
        if type(data.get('game_count')) is not int:
            raise SteamError('Steam Game details are private or unavailable. Set Game details to Public and allow playtime to be visible, then try again.')
        games = data.get('games', [])
        if not isinstance(games, list) or len(games) != data['game_count']:
            raise SteamError('Steam returned an incomplete library. Try again.')
        result = []
        seen = set()
        for game in games:
            app = game.get('appid')
            minutes = game.get('playtime_forever')
            if type(app) is not int or not 0 < app <= 4294967295 or app in seen or type(minutes) is not int or minutes < 0:
                raise SteamError('Steam library or playtime is unavailable. Make Game details and playtime public, then try again.')
            seen.add(app)
            result.append({'app_id': str(app), 'name': str(game.get('name') or f'Steam {app}')[:500],
                           'playtime_minutes': minutes, 'last_played': game.get('rtime_last_played') if type(game.get('rtime_last_played')) is int and game['rtime_last_played'] > 0 else None,
                           'poster': f'https://cdn.akamai.steamstatic.com/steam/apps/{app}/library_600x900.jpg',
                           'release_date': None, 'achievement_count': None, 'achievement_total': None})
        return result

    async def achievements(self, native, app):
        # Games without player stats commonly return 400. Treat that as unknown.
        if not self.key:
            raise SteamError('Steam is not configured.')
        try:
            raw = await self.http.request('steam', 'GET', API + 'ISteamUserStats/GetPlayerAchievements/v1/',
                cache=False, attempts=1, timeout=6, params={'key': self.key, 'steamid': native, 'appid': app})
        except ProviderError as error:
            if error.code in {'bad_response', 'not_found', 'forbidden'}:
                return {'achievement_count': None, 'achievement_total': None}
            raise SteamError('Steam achievements are temporarily unavailable. Retry to finish loading them.') from None
        return achievement_data(raw)

    async def wishlist(self, native):
        """Public, private-cache-free snapshot, certified by Steam result and count."""
        try:
            raw = await self.http.request('steam', 'GET', API + 'IWishlistService/GetWishlist/v1/',
                cache=False, attempts=1, steam_result=True, params={'steamid': native}, timeout=15)
            count = await self.http.request('steam', 'GET', API + 'IWishlistService/GetWishlistItemCount/v1/',
                cache=False, attempts=1, steam_result=True, params={'steamid': native}, timeout=15)
        except ProviderError:
            raise SteamError('Steam wishlist is private or temporarily unavailable. Make it public and retry.') from None
        data = raw.get('response') if isinstance(raw, dict) else None
        header = count.get('response') if isinstance(count, dict) else None
        rows = data.get('items', []) if isinstance(data, dict) else None
        total = header.get('count', 0) if isinstance(header, dict) else None
        if (not isinstance(rows, list) or type(total) is not int or not 0 <= total <= 100000 or len(rows) != total):
            raise SteamError('Steam returned an incomplete wishlist. Retry before syncing changes.')
        result, seen = [], set()
        for row in rows:
            app = row.get('appid') if isinstance(row, dict) else None
            if type(app) is not int or not 0 < app <= 4294967295 or app in seen:
                raise SteamError('Steam returned an invalid wishlist. Retry before syncing changes.')
            seen.add(app)
            result.append({'app_id': str(app), 'name': f'Steam {app}', 'playtime_minutes': 0,
                'poster': f'https://cdn.akamai.steamstatic.com/steam/apps/{app}/library_600x900.jpg',
                'last_played': None, 'release_date': None, 'achievement_count': None, 'achievement_total': None})
        return result

    async def software(self):
        """Public software IDs, paginated independently of a player's library."""
        if not self.key:
            raise SteamError('Steam is not configured.')
        apps, cursor = set(), 0
        for _ in range(5):
            raw = await self.http.request('steam', 'GET', API + 'IStoreService/GetAppList/v1/',
                attempts=1, timeout=15, params={'key': self.key, 'include_games': 'false',
                    'include_dlc': 'false', 'include_software': 'true', 'include_videos': 'false',
                    'include_hardware': 'false', 'max_results': 50000, 'last_appid': cursor})
            response = raw.get('response', {}) if isinstance(raw, dict) else {}
            rows = response.get('apps')
            if not isinstance(rows, list) or len(rows) > 50000:
                raise ProviderError('bad_response')
            ids = [row.get('appid') for row in rows if isinstance(row, dict)]
            if len(ids) != len(rows) or any(type(app) is not int or not cursor < app <= 4294967295 for app in ids) or ids != sorted(set(ids)):
                raise ProviderError('bad_response')
            apps.update(str(app) for app in ids)
            more = response.get('have_more_results')
            if more is False or (more is None and len(rows) < 50000):
                return apps
            if more is not True or not ids or response.get('last_appid') != ids[-1]:
                raise ProviderError('bad_response')
            cursor = ids[-1]
        raise ProviderError('incomplete')

    async def metadata(self, app):
        """Public store details are shared and loaded only for exposed cards."""
        from core.game_store_source import store_data, store_request
        data = store_data(await store_request(self.http, app), app)
        raw = (data.get('release_date') or {}).get('date', '')
        for pattern in ('%d %b, %Y', '%b %d, %Y', '%d %B, %Y', '%B %d, %Y'):
            try:
                return {'release_date': datetime.strptime(raw, pattern).date().isoformat()}
            except (ValueError, TypeError):
                pass
        # Preserve partial precision instead of inventing a release day.
        year = re.search(r'\b(19\d{2}|20\d{2})\b', raw) if isinstance(raw, str) else None
        return {'release_date': year.group(0) if year else None}
