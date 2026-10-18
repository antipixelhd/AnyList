"""Release windows and event identity, independent of notification-day parsing."""
import calendar
import re
from datetime import date


def window(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', value):
        return None
    try:
        parts = list(map(int, value.split('-')))
        year, month, day = (*parts, 1, 1)[:3]
        start = date(year, month, day)
        end = date(year, 12, 31) if len(parts) == 1 else date(year, month, calendar.monthrange(year, month)[1]) if len(parts) == 2 else start
        return start, end
    except ValueError:
        return None


def future(value, *, upcoming=False, today=None):
    bounds = window(value)
    today = today or date.today()
    return bool(bounds and (bounds[0] > today or (bounds[1] >= today and upcoming)))


def events(item, media_type, *, upcoming_only=False, include_returning=True, country=None, cinema=False, include_rereleases=False):
    """Use explicit cached season identity. Never infer it from total season count."""
    title = item.get('title') or item.get('name') or ''
    original = item.get('first_air_date') if media_type == 'series' else item.get('release_date')
    if isinstance(original, int):
        original = str(original)
    candidates = []
    local = []
    primary_movie_date = original
    if cinema and media_type == 'movie':
        from core.cinema_releases import theatrical_dates
        dates = theatrical_dates(item, country)
        primary_movie_date = dates[0] if dates else None
        if not include_rereleases:
            dates = dates[:1]
        dates = [value for value in dates if future(value)] if upcoming_only else dates
        candidates.extend((value, None, False) for value in dates)
    else:
        if media_type == 'movie' and country:
            local = sorted({release['release_date'][:10]
                for territory in (item.get('release_dates') or {}).get('results', []) if territory.get('iso_3166_1') == country
                for release in territory.get('release_dates', [])
                if release.get('type') in (2, 3, 4, 5, 6) and window((release.get('release_date') or '')[:10])})
            if not local:
                from core.cinema_releases import theatrical_dates
                local = theatrical_dates(item, country)
        if local:
            original = local[0]
        primary_movie_date = original
        if media_type == 'movie' and country and include_rereleases:
            from core.cinema_releases import theatrical_dates
            candidates.extend((value, None, False) for value in theatrical_dates(item, country) if value != original)
        if window(original):
            candidates.append((original, None, item.get('status') in ('Planned', 'In Production', 'Post Production')))
    if media_type == 'series' and include_returning:
        seasons = list(item.get('seasons') or [])
        next_episode = item.get('next_episode_to_air') or {}
        if next_episode.get('episode_number') == 1:
            seasons.append(next_episode)
        for season in seasons:
            number = season.get('season_number')
            value = season.get('air_date') or season.get('year') or ''
            if isinstance(value, int):
                value = str(value)
            if isinstance(number, int) and number > 1 and window(value):
                candidates.append((value, number, season.get('status') in ('Planned', 'In Production', 'upcoming')))
    rows, seen = [], set()
    last = item.get('last_episode_to_air') or {}
    last_window = window(last.get('air_date'))
    for value, season, announced in sorted(candidates, key=lambda row: window(row[0])[0]):
        if upcoming_only and not future(value, upcoming=announced):
            continue
        if upcoming_only and media_type == 'series' and last_window and last_window[1] <= date.today():
            last_season = last.get('season_number')
            if season is None or (isinstance(last_season, int) and last_season >= season):
                continue  # Known aired episodes contradict an upcoming premiere.
        identity = f'{media_type}:{item.get("id")}:season:{season}' if season else f'{media_type}:{item.get("id")}:premiere'
        if media_type == 'movie':
            # A premiere remains the same event when its date is corrected or
            # worldwide fallback gains country evidence. Only optional later
            # theatrical releases need separate date-based identities.
            identity = f'movie:{item.get("id")}:{"theatrical" if cinema else "premiere"}:{country or "world"}'
            if value != primary_movie_date:
                identity += f':rerelease:{value}'
        if identity in seen:
            continue
        seen.add(identity)
        rows.append({**item, 'title': f'{title} · Season {season}' if season else title,
                     'release_date': value, 'first_air_date': value if media_type == 'series' else item.get('first_air_date'),
                     'release_event': identity, 'release_season': season,
                     'release_upcoming': future(value, upcoming=announced),
                     'release_country': country if cinema or (media_type == 'movie' and local) else None})
        if upcoming_only:
            break  # One nearest announced return per series in Upcoming.
    return rows
