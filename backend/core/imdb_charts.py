"""IMDb chart ordering, cached independently of TMDB metadata and user scores."""

import asyncio
import json
import math
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import httpx

from core.config import settings

TTL = 6 * 60 * 60
MAX_STALE = 7 * 24 * 60 * 60
_cache = {}
_locks = {}
_retry_after = {}


class IMDbUnavailable(RuntimeError):
    pass


class _PageData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.capture = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.capture = False

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)


def parse_chart(html):
    parser = _PageData()
    parser.feed(html)
    try:
        data = json.loads("".join(parser.parts))
        edges = data["props"]["pageProps"]["pageData"]["chartTitles"]["edges"]
        rows, seen = [], set()
        for position, edge in enumerate(edges, 1):
            node = edge["node"]
            imdb_id = node.get("id", "")
            if not re.fullmatch(r"tt\d+", imdb_id) or imdb_id in seen:
                continue
            seen.add(imdb_id)
            ratings = node.get("ratingsSummary") or {}
            rank = edge.get("currentRank") or position
            votes = ratings.get("voteCount") or 0
            rating = ratings.get("aggregateRating")
            if (
                not isinstance(rank, int)
                or rank < 1
                or not isinstance(votes, int)
                or votes < 0
            ):
                raise ValueError("Invalid IMDb rank or vote count")
            if rating is not None and (
                not isinstance(rating, (int, float))
                or not math.isfinite(rating)
                or not 0 <= rating <= 10
            ):
                raise ValueError("Invalid IMDb rating")
            rows.append(
                {
                    "imdb_id": imdb_id,
                    "rank": rank,
                    "rating": rating,
                    "votes": votes,
                }
            )
        if not rows:
            raise ValueError("Empty chart")
        return sorted(rows, key=lambda row: row["rank"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IMDbUnavailable("IMDb chart data is unavailable") from exc


def _path(kind, chart):
    return Path(settings.data_dir) / "imdb-charts" / f"{kind}-{chart}.json"


def _cached(kind, chart):
    key = (kind, chart)
    if key not in _cache:
        try:
            data = json.loads(_path(kind, chart).read_text())
            stamp = float(data["fetched_at"])
            rows = data["rows"]
            valid = (
                isinstance(rows, list)
                and rows
                and all(
                    isinstance(row, dict)
                    and "rating" in row
                    and re.fullmatch(r"tt\d+", str(row.get("imdb_id", "")))
                    and isinstance(row.get("rank"), int)
                    and row["rank"] > 0
                    and isinstance(row.get("votes"), int)
                    and row["votes"] >= 0
                    and (
                        row.get("rating") is None
                        or isinstance(row["rating"], (int, float))
                        and math.isfinite(row["rating"])
                        and 0 <= row["rating"] <= 10
                    )
                    for row in rows
                )
            )
            if math.isfinite(stamp) and valid:
                _cache[key] = (stamp, data["rows"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    value = _cache.get(key)
    return value if value and 0 <= time.time() - value[0] < MAX_STALE else None


async def _chart(kind, chart):
    key = (kind, chart)
    async with _locks.setdefault(key, asyncio.Lock()):
        cached = _cached(kind, chart)
        if cached and time.time() - cached[0] < TTL:
            return cached[1], False
        if _retry_after.get(key, 0) > time.time():
            if cached:
                return cached[1], True
            raise IMDbUnavailable("IMDb charts are temporarily unavailable")
        slug = (
            ("top" if kind == "movie" else "toptv")
            if chart == "top"
            else ("moviemeter" if kind == "movie" else "tvmeter")
        )
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                response = await client.get(
                    f"https://www.imdb.com/chart/{slug}/",
                    headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                response.raise_for_status()
                rows = parse_chart(response.text)
        except (httpx.HTTPError, IMDbUnavailable) as exc:
            _retry_after[key] = time.time() + 60
            if cached:
                return cached[1], True
            raise IMDbUnavailable("IMDb charts are temporarily unavailable") from exc
        stamp = time.time()
        _cache[key] = (stamp, rows)
        try:
            path = _path(kind, chart)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"fetched_at": stamp, "rows": rows}))
            temporary.replace(path)
        except OSError:
            pass  # A read-only data directory still benefits from the memory cache.
        return rows, False


def _combine(popular, top):
    # All-time popularity uses IMDb vote counts, never TMDB popularity.
    rows = {row["imdb_id"]: row for row in [*popular, *top]}
    return sorted(rows.values(), key=lambda row: (-row["votes"], row["imdb_id"]))


def cached_rows(kind, sort):
    popular = _cached(kind, "popular")
    top = _cached(kind, "top")
    if sort == "trending":
        return popular[1] if popular else []
    if sort == "score":
        return top[1] if top else []
    return _combine(popular[1] if popular else [], top[1] if top else [])


async def ranked_rows(kind, sort):
    if sort in ("trending", "score"):
        return await _chart(kind, "top" if sort == "score" else "popular")
    results = await asyncio.gather(
        _chart(kind, "popular"), _chart(kind, "top"), return_exceptions=True
    )
    successful = [result for result in results if not isinstance(result, BaseException)]
    if not successful:
        raise IMDbUnavailable("IMDb charts are temporarily unavailable")
    rows = _combine(
        *(
            result[0] if not isinstance(result, BaseException) else []
            for result in results
        )
    )
    return rows, len(successful) < 2 or any(result[1] for result in successful)
