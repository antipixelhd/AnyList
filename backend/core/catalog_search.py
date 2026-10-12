"""Shared catalogue vote thresholds and conservative remote typo recovery."""

import re
import math
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


def title_runtime(item):
    try:
        value = float(item.get('runtime') or 0)
        return value if value > 0 and math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def known_result_metadata(item, media):
    saved = media.tmdb_data or {}
    merged = {**saved, **item, 'runtime': title_runtime(item) or media.runtime or saved.get('runtime')}
    if item.get('genre_ids') is not None and 'genres' not in item:
        merged.pop('genres', None)
    return merged


def matches_result_filters(item, media_type, *, min_votes=0, min_runtime=0, excluded_genres=()):
    from core.browse import GENRES
    if title_vote_count(item) < min_votes:
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


def apply_result_filters(query, data, media_type, *, min_votes=0, min_runtime=0, excluded_genres=(), runtime=None, imdb_votes=None):
    from sqlalchemy import or_
    from core.browse import GENRES
    if min_votes:
        query = query.where(stored_vote_count(data, imdb_votes=imdb_votes) >= min_votes)
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
