"""Partial-title candidate retrieval rules, never cross-provider identity matching."""
import re


def broader_query(query):
    value = query.strip()
    if not re.fullmatch(r'[^\W_]+(?:\s+[^\W_]+)+', value):
        return None
    broader = value.rsplit(None, 1)[0]
    return broader if len(broader) >= 2 else None


def matches_partial(query, title):
    words = re.findall(r'[^\W_]+', query.casefold())
    parts = re.findall(r'[^\W_]+', title.casefold())
    return bool(words) and all(any(part.startswith(word) for part in parts) for word in words)
