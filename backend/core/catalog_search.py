"""Shared catalogue filters, release evidence and conservative remote typo recovery."""

import re
import math
import asyncio
from difflib import SequenceMatcher

from sqlalchemy import Float, func

EXCLUDABLE_CATEGORIES = {99, 10402, 10770, 10763, 10764, 10766, 10767}


async def result_filters(db, viewer):
    """Read all account result filters together; never partition provider caches."""
    from sqlalchemy import select
    from models.profile import UserProfileData
    defaults = dict(min_runtime=30, excluded_genres=[])
    if viewer is None:
        return defaults
    row = (await db.execute(select(UserProfileData.minimum_movie_runtime,
                                  UserProfileData.excluded_categories).where(UserProfileData.user_id == viewer.id))).first()
    if row is None:
        return defaults
    return dict(min_runtime=row.minimum_movie_runtime, excluded_genres=row.excluded_categories or [])


async def browse_preferences(db, viewer):
    """Browse-only requirements stored in user settings JSON."""
    from sqlalchemy import select
    from models.users import UserSettings
    defaults = {'require_release': False, 'require_poster': False}
    if viewer is None:
        return defaults
    saved = await db.scalar(select(UserSettings.preferences).where(UserSettings.user_id == viewer.id))
    return {name: saved.get(name, default) if isinstance(saved, dict) else default
            for name, default in defaults.items()}


def title_runtime(item):
    try:
        value = float(item.get('runtime') or 0)
        return value if value > 0 and math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def known_result_metadata(item, media):
    saved = media.tmdb_data or {}
    merged = {**saved, **item, 'runtime': title_runtime(item) or media.runtime or saved.get('runtime')}
    released = 'first_air_date' if media.media_type.value == 'series' else 'release_date'
    if not merged.get(released):
        merged[released] = media.release_date
    if item.get('genre_ids') is not None and 'genres' not in item:
        merged.pop('genres', None)
    return merged


def exact_title_match(term, item):
    """Identify exact titles when reserving search enrichment slots."""
    query = (term or '').strip().casefold()
    return bool(query) and any(
        (item.get(field) or '').strip().casefold() == query
        for field in ('title', 'name', 'original_title', 'original_name')
    )


def has_streaming_offer(item: dict, region: str | None = None) -> bool:
    providers = ((item.get('watch/providers') or {}).get('results')
                 if 'watch/providers' in item else item.get('watch_providers')) or {}
    if not isinstance(providers, dict):
        return False
    territories = [providers.get(region)] if region else providers.values()
    return any(isinstance(offers, dict) and any(offers.get(kind) for kind in ('flatrate', 'free', 'ads'))
               for offers in territories)


def matches_result_filters(item, media_type, *, min_runtime=0, excluded_genres=()):
    from core.browse import GENRES
    runtime = title_runtime(item)
    if media_type == 'movie' and runtime is not None and runtime < min_runtime:
        return False
    ids = set(item.get('genre_ids') or [])
    names = set()
    for genre in item.get('genres') or []:
        if isinstance(genre, dict):
            ids.add(genre.get('id'))
            genre = genre.get('name')
        if isinstance(genre, str):
            names.add(genre.casefold())
    return not any(native in ids or GENRES.get(native, '').casefold() in names for native in excluded_genres)


def apply_result_filters(query, data, media_type, *, min_runtime=0, excluded_genres=(), runtime=None):
    from sqlalchemy import or_
    from core.browse import GENRES
    if media_type == 'movie' and min_runtime:
        duration = func.nullif(func.coalesce(runtime, data['runtime'].astext.cast(Float)), 0)
        query = query.where(or_(duration.is_(None), duration >= min_runtime))
    for native in excluded_genres:
        excluded = or_(data['genre_ids'].contains([native]), data['genres'].contains([{'id': native}]),
                       data['genres'].contains([GENRES[native]]), data['genres'].contains([{'name': GENRES[native]}]))
        query = query.where(~func.coalesce(excluded, False))
    return query


def title_vote_count(item: dict) -> int:
    """Prefer IMDb votes when available, otherwise use the TMDB count."""
    votes = item.get("imdb_votes")
    if votes is None:
        votes = (item.get("imdb") or {}).get("votes")
    if votes is None:
        votes = item.get("vote_count")
    return int(votes or 0)


def fuzzy_remote_terms(term: str) -> list[str]:
    """Return a small, conservative set of useful typo corrections for TMDB.

    PostgreSQL's trigram search handles titles already in AnyList, but a title
    not imported yet has to be found by TMDB. TMDB treats a misspelling as a
    literal query, so recover the common cases without a wide edit-distance
    search against the remote API.
    """
    candidates: list[str] = []

    def add(value: str) -> None:
        value = value.strip()
        if value and value.casefold() != term.casefold() and value not in candidates:
            candidates.append(value)

    # Correct one repeated run at a time: “Thee Odyssey” should produce
    # “The Odyssey”, not also strip the legitimate double-s in “Odyssey”.
    for match in re.finditer(r"(.)\1+", term, flags=re.IGNORECASE):
        add(f"{term[: match.start()]}{match.group(1)}{term[match.end() :]}")
    # These reciprocal substitutions cover ordinary keyboard/vowel slips such
    # as Mutany -> Mutiny. Generated results are checked against the original
    # spelling before they reach the user.
    substitutions = (
        ("a", "i"),
        ("i", "a"),
        ("e", "a"),
        ("a", "e"),
        ("o", "u"),
        ("u", "o"),
        ("e", "i"),
        ("i", "e"),
    )
    for wrong, right in substitutions:
        for match in re.finditer(wrong, term, flags=re.IGNORECASE):
            add(f"{term[: match.start()]}{right}{term[match.end() :]}")
    return candidates[:8]


def is_close_title_match(query: str, title: str) -> bool:
    """Avoid showing unrelated results from a generated fallback query."""
    compact = lambda value: re.sub(r"[^\w]", "", value.casefold())
    return SequenceMatcher(None, compact(query), compact(title)).ratio() >= 0.6


def predictive_titles(rows, term):
    """Search the already cached public catalogue off the request event loop."""
    query = ' '.join(re.findall(r'\w+', term.casefold()))
    if not query:
        return []
    def grams(value):
        value = '  ' + value + ' '
        return {value[i:i+3] for i in range(len(value)-2)}
    query_grams = grams(query)
    matches = []
    for row in rows:
        title = ' '.join(re.findall(r'\w+', (row.get('title') or '').casefold()))
        original = ' '.join(re.findall(r'\w+', (row.get('original_title') or row.get('original_name') or '').casefold()))
        if not title:
            title = ' '.join(re.findall(r'\w+', (row.get('name') or '').casefold()))
        if query in title or query in original:
            matches.append(row)
        elif len(query) >= 5:
            # Cheap trigram candidate selection avoids edit-distance work over
            # the whole catalogue, and still tolerates ordinary typing slips.
            tokens = title.split() + original.split()
            if (len(query_grams & grams(title)) / max(1,len(query_grams | grams(title))) >= .3
                and SequenceMatcher(None,query,title).ratio() >= .7) or any(len(query_grams & grams(token)) / max(1, len(query_grams | grams(token))) >= .3
                   and SequenceMatcher(None, query, token).ratio() >= .7 for token in tokens):
                matches.append(row)
    return matches


def title_match_rank(term, title):
    query, value = term.casefold().strip(), (title or '').casefold()
    return (0 if query == value else 1 if value.startswith(query) else 2 if query in value else 3,
            -SequenceMatcher(None, query, value).ratio())


def search_rank(term: str, item: dict, *, streaming_available: bool = False,
                theatrical: bool = False) -> float:
    """One comparable search score for movies and series, using list metadata only."""
    query = term.strip().casefold()
    names = [str(item.get(field) or '').casefold() for field in
             ('title', 'name', 'original_title', 'original_name')]
    names = [name for name in names if name]
    if not names or not query:
        return 0.0
    if query in names:
        relevance = 40
    elif any(name.startswith(query) for name in names):
        relevance = 30
    elif any(re.search(r'(?<!\w)' + re.escape(query), name) for name in names):
        relevance = 20
    elif any(query in name for name in names):
        relevance = 12
    else:
        similarity = max(SequenceMatcher(None, query, name).ratio() for name in names)
        relevance = 15 * similarity if similarity >= .5 else -30
    votes = item.get('vote_count')
    if votes is None:
        votes = item.get('imdb_votes') or (item.get('imdb') or {}).get('votes')
    def logarithmic_signal(value, weight):
        try:
            number = float(value or 0)
            return weight * math.log10(max(0, number) + 1) if math.isfinite(number) else 0
        except (TypeError, ValueError):
            return 0
    vote_weight = logarithmic_signal(votes, 5)
    popularity_weight = logarithmic_signal(item.get('popularity'), 2)
    poster_weight = .5 if item.get('poster_path') or item.get('poster') else 0
    # These are alternative release signals. Stacking them gives movies a
    # second bonus that series can never earn in combined search results.
    available = streaming_available or theatrical
    return round(relevance + vote_weight + popularity_weight +
                 (15 if available else 0) + poster_weight, 3)


def media_search_rank(term: str, item: dict, media_type: str) -> float:
    from core.cinema_releases import theatrical_dates
    return search_rank(term, item,
        streaming_available=has_streaming_offer(item),
        theatrical=bool(theatrical_dates(item)) if media_type == 'movie' else False)


def has_release(item: dict, media_type: str) -> bool:
    """A streaming offer anywhere, or an actual movie theatrical release anywhere."""
    from core.cinema_releases import has_cinema_release
    return has_streaming_offer(item) or (media_type == 'movie' and has_cinema_release(item))


def release_clause(data, media_type):
    """SQL equivalent of has_release for stored public provider metadata."""
    from datetime import date, timedelta
    from sqlalchemy import or_, literal, cast
    from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
    offers = [func.jsonb_path_exists(source, cast(f'$.*.{kind}[*] ? (@ != null)', JSONPATH))
              for source in (data['watch_providers'], data['watch/providers']['results'])
              for kind in ('flatrate', 'free', 'ads')]
    clauses = [func.coalesce(offer, False) for offer in offers]
    if media_type == 'movie':
        today = date.today()
        clauses.extend([
            data['theatrical_release_dates'][0].astext <= today.isoformat(),
            func.jsonb_path_exists(data['theatrical_release_dates_by_country'], cast('$.*[*] ? (@ <= $today)', JSONPATH),
                                  literal({'today': today.isoformat()}, type_=JSONB)),
            func.jsonb_path_exists(data['release_dates'],
                cast('$.results[*].release_dates[*] ? ((@.type == 2 || @.type == 3) && @.release_date < $tomorrow)', JSONPATH),
                literal({'tomorrow': (today + timedelta(days=1)).isoformat()}, type_=JSONB)),
        ])
    return or_(*clauses)


async def enrich_release_evidence(items, media_type, api_key):
    """Read only missing public release evidence, bounded to six concurrent calls."""
    if not api_key:
        return
    from core import tmdb
    from core.cinema_releases import has_cinema_release
    semaphore = asyncio.Semaphore(6)

    async def load(item):
        async with semaphore:
            if has_release(item, media_type):
                return
            if 'watch/providers' not in item and 'watch_providers' not in item:
                provider = tmdb.get_movie_watch_providers if media_type == 'movie' else tmdb.get_show_watch_providers
                item['watch/providers'] = await provider(item['id'], api_key=api_key)
            if media_type == 'movie' and not has_streaming_offer(item) and not has_cinema_release(item):
                if not any(name in item for name in ('release_dates', 'theatrical_release_dates', 'theatrical_release_dates_by_country')):
                    item['release_dates'] = await tmdb.get_movie_release_dates(item['id'], api_key=api_key)

    await asyncio.gather(*(load(item) for item in items))


async def enrich_search_evidence(items: list[dict], term: str, media_type: str,
                                 api_key: str | None, region: str, *, theatrical=False,
                                 streaming=True, limit=8, timeout=.9) -> None:
    """Check only the leading search candidates; reuse TMDB's shared TTL cache."""
    if not api_key or len(term) < 3 or not (streaming or theatrical):
        return
    from core import tmdb
    def needs_evidence(item):
        return ((streaming and 'watch/providers' not in item and 'watch_providers' not in item)
                or (theatrical and media_type == 'movie' and 'release_dates' not in item
                    and 'theatrical_release_dates_by_country' not in item))

    unique = {item['id']: item for item in items if isinstance(item.get('id'), int) and needs_evidence(item)}
    ranked = sorted(unique.values(),
                    key=lambda item: search_rank(term, item), reverse=True)
    # A low-vote exact title can fall below popular partial matches before its
    # release evidence arrives. Reserve two slots without increasing the budget.
    exact = [item for item in ranked if exact_title_match(term, item)][:2]
    exact_ids = {item['id'] for item in exact}
    candidates = (exact + [item for item in ranked if item['id'] not in exact_ids])[:limit]
    semaphore = asyncio.Semaphore(4)

    async def fetch(provider, identifier):
        async with semaphore:
            return await provider(identifier, api_key=api_key)

    async def load(item):
        identifier = item['id']
        calls = []
        if streaming and 'watch/providers' not in item and 'watch_providers' not in item:
            provider = tmdb.get_movie_watch_providers if media_type == 'movie' else tmdb.get_show_watch_providers
            calls.append(('watch/providers', provider))
        if theatrical and media_type == 'movie' and 'release_dates' not in item and 'theatrical_release_dates_by_country' not in item:
            calls.append(('release_dates', tmdb.get_movie_release_dates))
        responses = await asyncio.gather(*(fetch(provider, identifier) for _, provider in calls), return_exceptions=True)
        for (name, _), response in zip(calls, responses):
            if isinstance(response, dict):
                item[name] = response

    try:
        await asyncio.wait_for(asyncio.gather(*(load(item) for item in candidates)), timeout)
    except TimeoutError:
        pass
