"""Bounded screen-title candidate adapter shared by quick and browse search.

Provider reads, correction budgets and eligibility are separate. Normal search
needs one provider page and no per-title release/provider evidence requests.
"""
import asyncio
import re

from core import tmdb
from core.catalog_search import fuzzy_remote_terms, is_close_title_match, search_rank

MAX_CORRECTIONS = 2
STRONG_MATCH_SCORE = 35


async def candidates(term, media_type, api_key, page=1):
    search = tmdb.search_movies if media_type == 'movie' else tmdb.search_shows
    if page > 500:
        return {'results': [], 'page': page, 'total_pages': 500}, []
    data = await search(term, page=page, api_key=api_key)
    # Provider matching includes aliases absent from the returned display names.
    rows = [{**row, '_provider_query': term} for row in data.get('results', [])]
    strong = any(search_rank(term, row) >= STRONG_MATCH_SCORE for row in rows)
    if page == 1 and len(term.strip()) >= 3 and not strong:
        # A retained distinctive token recovers deletions like avengrs dooms.
        tokens = re.findall(r'\w+', term)
        alternatives = []
        if len(tokens) > 1:
            alternatives.append(max(tokens, key=lambda token: (len(token) > 3, tokens.index(token))))
        alternatives.extend(fuzzy_remote_terms(term))
        alternatives = list(dict.fromkeys(value for value in alternatives if value.casefold() != term.casefold()))[:MAX_CORRECTIONS]
        corrected = await asyncio.gather(*(search(value, api_key=api_key) for value in alternatives), return_exceptions=True)
        for response in corrected:
            if not isinstance(response, dict):
                continue
            rows.extend(dict(row) for row in response.get('results', []) if any(
                is_close_title_match(term, row.get(field) or '')
                for field in ('title', 'name', 'original_title', 'original_name')))
    # Do not discard provider matches on alternative/translated names.
    unique = {}
    for row in rows:
        # The adapter is scoped to one media kind; preserve direct-query evidence
        # when a correction returns the same identity again.
        unique.setdefault(row['id'], row)
    return data, list(unique.values())
