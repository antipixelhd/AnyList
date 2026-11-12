"""Partial-title candidate retrieval rules, never cross-provider identity matching."""
import re
import unicodedata
from difflib import SequenceMatcher

from core.catalog_search import search_rank


def normalized(value):
    # Discovery spelling only. Never use this key to bind provider identities.
    folded = ''.join(char for char in unicodedata.normalize('NFKD', value.casefold())
                     if not unicodedata.combining(char))
    return ' '.join(re.findall(r'[^\W_]+', folded.replace("'", '').replace('’', '')))


def broader_query(query):
    value = query.strip()
    if not re.fullmatch(r"[^\W_]+(?:[’'][^\W_]+)*(?:\s+[^\W_]+(?:[’'][^\W_]+)*)*", value):
        return None
    words = normalized(value).split()
    # One stable bounded fallback stream, including for a single mistyped word.
    # Keeping its prefix permits minor suffix errors without provider fan-out.
    broader = value.rsplit(None, 1)[0] if len(words)>1 else words[0][:max(3, len(words[0])//2)] if len(words[0])>=5 else ''
    if len(words)>1 and len(words[0])>=6:
        broader=words[0][:max(3,len(words[0])//2)]
    return broader if len(broader) >= 2 else None


def matches_partial(query, title):
    words = normalized(query).split()
    parts = normalized(title).split()
    return bool(words) and all(any(part.startswith(word) or len(word)>=4 and
        SequenceMatcher(None, word, part).ratio()>=.72 for part in parts) for word in words)


def rank(query, title):
    return search_rank(normalized(query), {'name': normalized(title)})
