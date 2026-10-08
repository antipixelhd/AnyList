"""Shared, durable MDBList snapshots; filtering never spends MDBList quota.

Official MovieMeter is refreshed at most every six hours. Broad public IMDb
vote lists are refreshed daily and supply both popularity and rating sorts.
Lists are fetched completely with cursors, then sorted locally: MDBList's
live API currently ignores the documented descending sort direction.
"""

import asyncio
import hashlib
import json
import math
import time
from pathlib import Path

from core import mdblist
from core.config import settings

TRENDING_TTL = 6 * 60 * 60
CATALOGUE_TTL = 24 * 60 * 60
MAX_STALE = 7 * 24 * 60 * 60
# Public community catalogues, not user watchlists. Override if a list is retired.
_cache = {}
_locks = {}
_retry_after = {}


class DiscoveryUnavailable(RuntimeError):
    pass


def _feed(kind, sort):
    if sort == "trending":
        return "official/moviemeter", TRENDING_TTL
    identifier = (
        settings.mdblist_browse_movie_list_id
        if kind == "movie"
        else settings.mdblist_browse_series_list_id
    )
    return str(identifier), CATALOGUE_TTL


def _identity(feed, key):
    # Account caches remain separate; neither filenames nor payloads contain keys.
    return hashlib.sha256(f"{feed}:{key}".encode()).hexdigest()


def _path(identity):
    return Path(settings.data_dir) / "mdblist-discovery" / f"{identity}.json"


def _cached(identity):
    if identity not in _cache:
        try:
            data = json.loads(_path(identity).read_text(encoding="utf-8"))
            stamp, rows = float(data["fetched_at"]), data["rows"]
            if (
                math.isfinite(stamp)
                and isinstance(rows, list)
                and all(
                    isinstance(row, dict)
                    and isinstance(row.get("id"), int)
                    and row["id"] > 0
                    and row.get("type") in ("movie", "series")
                    for row in rows
                )
            ):
                _cache[identity] = stamp, rows
        except (OSError, ValueError, KeyError, TypeError):
            pass
    cached = _cache.get(identity)
    return cached if cached and 0 <= time.time() - cached[0] < MAX_STALE else None


def _number(value, maximum=None):
    try:
        value = float(value)
        return (
            value
            if math.isfinite(value)
            and value >= 0
            and (maximum is None or value <= maximum)
            else None
        )
    except (TypeError, ValueError):
        return None


def normalize(item, kind):
    from core.browse import GENRES, MOVIE_GENRES, TV_GENRES

    ids = item.get("ids") or {}
    identifier = ids.get("tmdb") or item.get("id")
    if (
        not isinstance(identifier, int)
        or isinstance(identifier, bool)
        or identifier <= 0
    ):
        return None
    ratings = {
        r.get("source"): r for r in item.get("ratings", []) if isinstance(r, dict)
    }
    imdb = ratings.get("imdb", {})
    tmdb_rating = _number(ratings.get("tmdb", {}).get("value"), 100)
    names = {
        (g.get("name", "") if isinstance(g, dict) else str(g)).lower()
        for g in item.get("genres", [])
    }
    aliases = {"science fiction": "sci-fi", "tv movie": "tv-movie"}
    genre_ids = [
        identifier
        for identifier, name in GENRES.items()
        if identifier in (MOVIE_GENRES if kind == "movie" else TV_GENRES)
        and (name.lower() in names or aliases.get(name.lower()) in names)
    ]
    if kind == "series":
        if names & {"action", "adventure"}:
            genre_ids.append(10759)
        if names & {"sci-fi", "science fiction", "fantasy"}:
            genre_ids.append(10765)
        if names & {"war", "politics"}:
            genre_ids.append(10768)
    return {
        "id": identifier,
        "type": kind,
        "imdb_id": ids.get("imdb") or item.get("imdb_id"),
        "title": item.get("title"),
        "poster_path": item.get("poster"),
        "release_date": item.get("release_date") or "",
        "status": item.get("status"),
        "original_language": item.get("language"),
        "origin_country": [str(item.get("country") or "").upper()],
        "adult": bool(item.get("adult")),
        "genre_ids": sorted(set(genre_ids)),
        "vote_average": tmdb_rating / 10 if tmdb_rating is not None else None,
        "imdb_score": _number(imdb.get("value"), 10),
        "imdb_votes": int(_number(imdb.get("votes")) or 0),
        "rank": int(_number(item.get("rank")) or 0),
    }


async def _snapshot(feed, key, ttl):
    identity = _identity(feed, key)
    async with _locks.setdefault(identity, asyncio.Lock()):
        cached = _cached(identity)
        if cached and time.time() - cached[0] < ttl:
            return cached[1], False
        if _retry_after.get(identity, 0) > time.time():
            if cached:
                return cached[1], True
            raise DiscoveryUnavailable("MDBList discovery is temporarily unavailable")
        try:
            rows, seen, cursors = [], set(), set()
            params = {"limit": 1000, "append_to_response": "genres,poster,ratings"}
            # Bound malformed providers without silently publishing a partial list.
            for _ in range(50):
                data = await mdblist._request(
                    "GET",
                    f"/lists/{feed}/items",
                    key,
                    params=dict(params),
                    rate_limit_retries=2,
                    max_wait=5,
                )
                if not isinstance(data.get("pagination"), dict):
                    raise DiscoveryUnavailable("Invalid MDBList discovery response")
                for bucket, kind in (("movies", "movie"), ("shows", "series")):
                    items = data.get(bucket, [])
                    if not isinstance(items, list) or any(
                        not isinstance(item, dict) for item in items
                    ):
                        raise DiscoveryUnavailable("Invalid MDBList discovery items")
                    for item in items:
                        row = normalize(item, kind)
                        if row and (kind, row["id"]) not in seen:
                            seen.add((kind, row["id"]))
                            rows.append(row)
                pagination = data["pagination"]
                cursor = pagination.get("next_cursor")
                if not cursor:
                    if pagination.get("has_more"):
                        raise DiscoveryUnavailable(
                            "MDBList did not provide the next cursor"
                        )
                    break
                if cursor in cursors:
                    raise DiscoveryUnavailable("MDBList repeated a pagination cursor")
                cursors.add(cursor)
                params["cursor"] = cursor
            else:
                raise DiscoveryUnavailable(
                    "MDBList catalogue exceeded the fetch budget"
                )
            if not rows:
                raise DiscoveryUnavailable("MDBList returned an empty discovery feed")
        except (
            mdblist.MDBListAPIError,
            DiscoveryUnavailable,
            TypeError,
            ValueError,
        ) as exc:
            cooldown = (
                TRENDING_TTL
                if isinstance(exc, mdblist.MDBListDailyLimitError)
                else 15 * 60
            )
            _retry_after[identity] = time.time() + cooldown
            if cached:
                return cached[1], True
            raise DiscoveryUnavailable(
                "MDBList discovery is temporarily unavailable"
            ) from exc
        stamp = time.time()
        _cache[identity] = stamp, rows
        try:
            path = _path(identity)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"fetched_at": stamp, "rows": rows}), encoding="utf-8"
            )
            temporary.replace(path)
        except OSError:
            pass
        return rows, False


def _ordered(rows, kind, sort):
    rows = [row for row in rows if row["type"] == kind]
    if sort == "score":
        minimum = 25000 if kind == "movie" else 10000
        rows = [
            row for row in rows if row["imdb_score"] and row["imdb_votes"] >= minimum
        ]
        return sorted(rows, key=lambda r: (-r["imdb_score"], -r["imdb_votes"], r["id"]))
    if sort == "popular":
        return sorted(rows, key=lambda r: (-r["imdb_votes"], r["id"]))
    return sorted(rows, key=lambda r: (r["rank"] or math.inf, r["id"]))


async def ranked_rows(kind, sort, api_key):
    if not api_key:
        raise DiscoveryUnavailable("Add an MDBList key in Settings to discover titles")
    feed, ttl = _feed(kind, sort)
    rows, stale = await _snapshot(feed, api_key, ttl)
    return _ordered(rows, kind, sort), stale


def cached_rows(kind, sort, api_key=None):
    if not api_key:
        return []
    feed, _ = _feed(kind, sort)
    cached = _cached(_identity(feed, api_key))
    rows = _ordered(cached[1], kind, sort) if cached else []
    return [
        {**row, "rating": row["imdb_score"], "votes": row["imdb_votes"]} for row in rows
    ]
