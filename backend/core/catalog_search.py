"""Shared catalogue vote thresholds and conservative remote typo recovery."""

import re
import math
import asyncio
from datetime import date
from difflib import SequenceMatcher

from sqlalchemy import Float, func

DEFAULT_MIN_VOTES = 250
EXCLUDABLE_CATEGORIES = {99, 10402, 10770, 10763, 10764, 10766, 10767}


async def result_filters(db, viewer):
    """Read all account result filters together; never partition provider caches."""
    from sqlalchemy import select
    from models.profile import UserProfileData
    defaults = dict(min_votes=DEFAULT_MIN_VOTES, min_runtime=30, excluded_genres=[])
    if viewer is None:
        return defaults
    row = (await db.execute(select(UserProfileData.minimum_votes, UserProfileData.minimum_movie_runtime,
                                  UserProfileData.excluded_categories).where(UserProfileData.user_id == viewer.id))).first()
    if row is None:
        return defaults
    return dict(min_votes=row.minimum_votes, min_runtime=row.minimum_movie_runtime, excluded_genres=row.excluded_categories or [])


async def search_preferences(db, viewer):
    """Optional per-account ranking preferences stored in user settings JSON."""
    from sqlalchemy import select
    from models.users import UserSettings
    defaults = {'prefer_streaming_series': True, 'prefer_theatrical_movies': True}
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
    """An explicit title lookup should find its title even before votes accrue."""
    query = (term or '').strip().casefold()
    return bool(query) and any(
        (item.get(field) or '').strip().casefold() == query
        for field in ('title', 'name', 'original_title', 'original_name')
    )


def has_streaming_offer(item: dict, region: str) -> bool:
    providers = item.get('watch_providers') or (item.get('watch/providers') or {}).get('results') or {}
    if not isinstance(providers, dict):
        return False
    offers = providers.get(region) or {}
    return isinstance(offers, dict) and any(offers.get(kind) for kind in ('flatrate', 'free', 'ads'))


def matches_result_filters(item, media_type, *, min_votes=0, min_runtime=0, excluded_genres=(), search_term=''):
    from core.browse import GENRES
    upcoming = (item.get('release_date') or item.get('first_air_date') or '') > date.today().isoformat()
    if not upcoming and title_vote_count(item) < min_votes and not exact_title_match(search_term, item):
        return False
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


def apply_result_filters(query, data, media_type, *, min_votes=0, min_runtime=0, excluded_genres=(), runtime=None, imdb_votes=None, release_date=None, search_term='', title=None, original_title=None):
    from sqlalchemy import or_
    from core.browse import GENRES
    if min_votes:
        votes = stored_vote_count(data, imdb_votes=imdb_votes) >= min_votes
        allowed = [votes]
        if release_date is not None:
            allowed.append(release_date > date.today().isoformat())
        if search_term:
            if title is not None:
                allowed.append(func.lower(title) == search_term.casefold())
            if original_title is not None:
                allowed.append(func.lower(original_title) == search_term.casefold())
        query = query.where(or_(*allowed))
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


def stored_vote_count(data, *, imdb_votes=None):
    return func.coalesce(
        data["imdb"]["votes"].astext.cast(Float) if imdb_votes is None else imdb_votes,
        data["vote_count"].astext.cast(Float),
        0,
    )


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
                theatrical: bool = False, prefer_streaming: bool = True,
                prefer_theatrical: bool = False) -> float:
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
    available = (streaming_available and prefer_streaming) or (theatrical and prefer_theatrical)
    return round(relevance + vote_weight + popularity_weight +
                 (15 if available else 0) + poster_weight, 3)


def media_search_rank(term: str, item: dict, media_type: str, region: str,
                      preferences: dict) -> float:
    from core.cinema_releases import theatrical_dates
    return search_rank(term, item,
        streaming_available=has_streaming_offer(item, region),
        theatrical=bool(theatrical_dates(item, region)) if media_type == 'movie' else False,
        prefer_streaming=preferences.get('prefer_streaming_series', True) if media_type == 'series' else True,
        prefer_theatrical=preferences.get('prefer_theatrical_movies', True))


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
