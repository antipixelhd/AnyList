"""Genre breakdowns from the same canonical watched-title cohort as Overview."""

from collections import defaultdict
from core.browse import GENRES, MOVIE_GENRES, TV_GENRES
from core.catalogue_normalize import image

ALIASES = {"sci-fi": "Science Fiction", "science-fiction": "Science Fiction"}
NAMES = {name.casefold(): name for name in GENRES.values()}


def genre_names(data):
    result = set()
    for value in data.get("genres") or []:
        name = value if isinstance(value, str) else value.get("name") if isinstance(value, dict) else None
        if isinstance(name, str) and name.strip():
            name = name.strip()
            result.add(ALIASES.get(name.casefold(), NAMES.get(name.casefold(), name)))
    return result


def browse_filter(name, kind):
    allowed = MOVIE_GENRES if kind == "movie" else TV_GENRES
    direct = next((str(id) for id in allowed if GENRES[id] == name), None)
    equivalents = {("Action & Adventure", "movie"): "28,12", ("Sci-Fi & Fantasy", "movie"): "878,14",
                   ("War & Politics", "movie"): "10752", ("Action", "series"): "10759",
                   ("Adventure", "series"): "10759", ("Science Fiction", "series"): "10765",
                   ("Fantasy", "series"): "10765", ("War", "series"): "10768"}
    return direct or equivalents.get((name, kind)) or "name:" + name


def poster(value):
    if not isinstance(value, str):
        return None
    return image(value) or ("https://image.tmdb.org/t/p/w185" + value if value.startswith("/") and not value.startswith("//") else None)


def genre_groups(watched):
    buckets = defaultdict(list)
    for title in watched:
        for name in genre_names(title["data"]):
            buckets[name].append(title)
    result = []
    for name, titles in sorted(buckets.items()):
        rated = [t for t in titles if t["score"] is not None]
        best = sorted(rated, key=lambda t: (-t["score"], t["name"].casefold(), t["key"]))[:10]
        result.append({"key": name.casefold(), "label": name, "titles": len(titles),
                       "minutes": sum(t["minutes"] for t in titles),
                       "rated_titles": len(rated),
                       "runtime_missing_plays": sum(t["runtime_missing"] for t in titles),
                       "mean_score": sum(t["score"] for t in rated) / len(rated) if rated else None,
                       "browse_filters": {kind: browse_filter(name, kind) for kind in ("movie", "series")},
                       "top_titles": [{"key": t["key"], "title": t["name"], "poster": t["poster"],
                                       "href": f"/title/{t['detail_media_id']}", "score": t["score"]}
                                      for t in best if t["detail_media_id"] is not None]})
    return result
