"""TMDB movie financial amounts are whole US dollars; zero means unknown."""


def movie_financials(*sources):
    result = {}
    for source in sources:
        for key in ('budget', 'revenue'):
            value = (source or {}).get(key)
            if key not in result and isinstance(value, int) and not isinstance(value, bool) and value > 0:
                result[key] = value
    return result
