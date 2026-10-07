"""Conservative remote typo recovery shared by catalogue search and browse."""

import re
from difflib import SequenceMatcher


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
