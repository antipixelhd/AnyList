"""Bounded server-only metadata clients. No user-controlled endpoints/queries."""

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from core.config import settings
from models.catalogue import MetadataProviderBudget
from .catalogue_normalize import (
    normalize_hardcover,
    normalize_igdb,
    normalize_rawg,
    normalize_tmdb,
    normalize_tvdb,
)

ATTRIBUTION = {
    "tmdb": {
        "name": "TMDB",
        "url": "https://www.themoviedb.org",
        "notice": "This product uses the TMDB API but is not endorsed or certified by TMDB.",
    },
    "tvdb": {"name": "TheTVDB", "url": "https://thetvdb.com"},
    "igdb": {"name": "IGDB", "url": "https://www.igdb.com"},
    "hardcover": {"name": "Hardcover", "url": "https://hardcover.app"},
    "openlibrary": {"name": "Open Library", "url": "https://openlibrary.org"},
    "rawg": {"name": "RAWG", "url": "https://rawg.io"},
    "itad": {"name": "IsThereAnyDeal", "url": "https://isthereanydeal.com"},
}
INTERVALS = {
    "igdb": 0.3,
    "hardcover": 1.1,
    "openlibrary": 0.35,
    "rawg": 1.0,
    "itad": 0.5,
    "tmdb": 0.3,
    "tvdb": 0.5,
}
SUPPORT = {
    "tmdb": {"movie", "series"},
    "tvdb": {"series"},
    "igdb": {"game"},
    "rawg": {"game"},
    "hardcover": {"book"},
    "openlibrary": {"book"},
}


class ProviderError(Exception):
    """Only allowlisted codes cross logging/API boundaries, never HTTP bodies."""

    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class _PrivateRequests(logging.Filter):
    def filter(self, record):
        return not any(
            host in record.getMessage()
            for host in (
                "api.rawg.io",
                "api.isthereanydeal.com",
                "api.igdb.com",
                "id.twitch.tv",
                "api.hardcover.app",
            )
        )


for _logger in ("httpx", "httpcore"):
    logging.getLogger(_logger).addFilter(_PrivateRequests())


_local_locks = {}
_local_next = {}
_cache = OrderedDict()
_tokens = {}
_token_locks = {}
MAX_RESPONSE_BYTES = 4_000_000
MAX_CACHE_BYTES = 16_000_000


def retry_after_seconds(value, fallback):
    try:
        seconds = float(value)
    except ValueError:
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            seconds = (deadline - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return fallback
    # Retry-After must not produce NaN/Infinity or negative sleep durations.
    return max(0, min(seconds, 86400)) if seconds >= 0 else fallback


class ProviderHTTP:
    def __init__(self, client=None, session_factory=None):
        self.client = client
        self.session_factory = session_factory

    @asynccontextmanager
    async def lane(self, provider):
        """Serialize provider requests across workers; retain RAWG monthly and Hardcover daily budgets.

        A dedicated session owns a transaction advisory lock through the HTTP
        call. Never use the caller's catalogue/history transaction for this.
        """
        interval = (
            1.1
            if provider == "openlibrary" and not settings.openlibrary_contact_email
            else INTERVALS[provider]
        )
        if self.session_factory:
            async with self.session_factory() as db:
                await db.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {
                        "key": int.from_bytes(
                            hashlib.sha256(
                                ("catalogue-provider:" + provider).encode()
                            ).digest()[:8],
                            "big",
                            signed=True,
                        )
                    },
                )
                await db.execute(
                    insert(MetadataProviderBudget)
                    .values(provider=provider)
                    .on_conflict_do_nothing()
                )
                row = (
                    await db.execute(
                        select(MetadataProviderBudget).where(
                            MetadataProviderBudget.provider == provider
                        )
                    )
                ).scalar_one()
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if row.next_request_at and row.next_request_at > now:
                    await asyncio.sleep(
                        min(2, (row.next_request_at - now).total_seconds())
                    )
                month = now.strftime("%Y-%m")
                if row.month != month:
                    row.month, row.request_count = month, 0
                if (
                    provider == "rawg"
                    and row.request_count >= settings.rawg_monthly_budget
                ):
                    raise ProviderError("monthly_budget")
                if provider == "hardcover":
                    day = now.strftime("%Y-%m-%d")
                    if row.day != day:
                        row.day, row.daily_request_count = day, 0
                    if row.daily_request_count >= settings.hardcover_daily_budget:
                        tomorrow = now.replace(
                            hour=0, minute=0, second=0, microsecond=0
                        ) + timedelta(days=1)
                        raise ProviderError(
                            "daily_budget", (tomorrow - now).total_seconds()
                        )
                    row.daily_request_count += 1
                row.request_count += 1
                # Persist request accounting even when HTTP fails.
                try:
                    yield
                finally:
                    row.next_request_at = datetime.now(timezone.utc).replace(
                        tzinfo=None
                    ) + timedelta(seconds=interval)
                    await db.commit()
        else:
            lock = _local_locks.setdefault(provider, asyncio.Lock())
            async with lock:
                await asyncio.sleep(
                    max(0, _local_next.get(provider, 0) - time.monotonic())
                )
                try:
                    yield
                finally:
                    _local_next[provider] = time.monotonic() + interval

    async def request(self, provider, method, url, *, cache=True, **kwargs):
        # Cache keys are digests, never raw credentials. Max 128 responses, 10m.
        key = hashlib.sha256(
            json.dumps(
                [provider, method, url, kwargs], sort_keys=True, default=str
            ).encode()
        ).hexdigest()
        hit = _cache.get(key)
        if cache and hit and hit[0] > time.monotonic():
            _cache.move_to_end(key)
            return json.loads(hit[1])
        owned = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=httpx.Timeout(15, connect=5),
            follow_redirects=False,
            headers={"User-Agent": "AnyList catalogue/1"},
        )
        try:
            for attempt in range(3):
                try:
                    async with self.lane(provider):
                        async with client.stream(method, url, **kwargs) as response:
                            status = response.status_code
                            headers = response.headers
                            body = bytearray()
                            if status == 200:
                                length = headers.get("Content-Length", "")
                                if (
                                    length.isdigit()
                                    and int(length) > MAX_RESPONSE_BYTES
                                ):
                                    raise ProviderError("payload_too_large")
                                async for chunk in response.aiter_bytes(
                                    chunk_size=65536
                                ):
                                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                                        raise ProviderError("payload_too_large")
                                    body.extend(chunk)
                    if status in (429, 500, 502, 503, 504):
                        delay = retry_after_seconds(
                            headers.get("Retry-After", ""), 2**attempt
                        )
                        if attempt == 2 or delay > 5:
                            raise ProviderError(
                                "rate_limited" if status == 429 else "unavailable",
                                delay,
                            )
                        await asyncio.sleep(max(0.1, delay))
                        continue
                    if status != 200:
                        if (
                            provider == "openlibrary"
                            and status in (301, 302, 303, 307, 308)
                            and url.startswith("https://openlibrary.org/isbn/")
                        ):
                            return {"_redirect": headers.get("Location", "")}
                        raise ProviderError(
                            {
                                401: "unauthorized",
                                403: "forbidden",
                                404: "not_found",
                            }.get(status, "bad_response")
                        )
                    try:
                        data = json.loads(body)
                    except ValueError:
                        raise ProviderError("invalid_json") from None
                    if isinstance(data, dict) and data.get("errors"):
                        raise ProviderError("graphql_error")
                    if cache:
                        _cache[key] = (
                            time.monotonic() + 600,
                            json.dumps(
                                data, ensure_ascii=False, separators=(",", ":")
                            ).encode("utf-8"),
                        )
                        while (
                            len(_cache) > 128
                            or sum(len(hit[1]) for hit in _cache.values())
                            > MAX_CACHE_BYTES
                        ):
                            _cache.popitem(last=False)
                    return data
                except (
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    httpx.RemoteProtocolError,
                ):
                    if attempt == 2:
                        raise ProviderError("unavailable") from None
                    await asyncio.sleep(2**attempt)
        finally:
            if owned:
                await client.aclose()


IGDB_FIELDS = (
    "id,name,summary,first_release_date,version_parent,parent_game.name,game_type.type,cover.url,genres.name,artworks.url,involved_companies.developer,involved_companies.publisher,involved_companies.supporting,involved_companies.porting,involved_companies.company.name,involved_companies.company.country,involved_companies.company.description,involved_companies.company.logo.url,external_games.uid,external_games.url,external_games.external_game_source.name,release_dates.date,release_dates.region,release_dates.platform.name,collections.name,"
    + ",".join(
        f"{field}.{projection}"
        for field in (
            "dlcs",
            "expansions",
            "standalone_expansions",
            "remakes",
            "remasters",
            "ports",
            "similar_games",
        )
        for projection in ("name", "version_parent.name")
    )
)


class CatalogueProviders:
    def __init__(self, http=None, tmdb_key=None, tvdb_key=None):
        self.http = http or ProviderHTTP()
        self.tmdb_key = tmdb_key or settings.tmdb_api_key
        self.tvdb_key = tvdb_key or settings.tvdb_api_key

    def validate(self, provider, kind):
        if kind not in SUPPORT.get(provider, set()):
            raise ProviderError("unsupported")
        required = {
            "tmdb": [self.tmdb_key],
            "tvdb": [self.tvdb_key],
            "igdb": [settings.igdb_client_id, settings.igdb_client_secret],
            "hardcover": [settings.hardcover_api_key],
            "rawg": [settings.rawg_api_key],
            "openlibrary": [],
        }
        if not all(required[provider]):
            raise ProviderError("not_configured")

    async def igdb(self, endpoint, query):
        token_key = hashlib.sha256(
            (
                (settings.igdb_client_id or "")
                + ":"
                + (settings.igdb_client_secret or "")
            ).encode()
        ).hexdigest()
        lock = _token_locks.setdefault(token_key, asyncio.Lock())
        async with lock:
            cached = _tokens.get(token_key)
            if not cached or time.monotonic() >= cached[1]:
                t = await self.http.request(
                    "igdb",
                    "POST",
                    "https://id.twitch.tv/oauth2/token",
                    cache=False,
                    data={
                        "client_id": settings.igdb_client_id,
                        "client_secret": settings.igdb_client_secret,
                        "grant_type": "client_credentials",
                    },
                )
                if not isinstance(t, dict) or not t.get("access_token"):
                    raise ProviderError("unauthorized")
                cached = (
                    t["access_token"],
                    time.monotonic() + max(1, int(t.get("expires_in", 0)) - 60),
                )
                _tokens[token_key] = cached
                while len(_tokens) > 8:
                    _tokens.pop(next(iter(_tokens)))
        token = cached[0]
        try:
            result = await self.http.request(
                "igdb",
                "POST",
                "https://api.igdb.com/v4/" + endpoint,
                headers={
                    "Client-ID": settings.igdb_client_id,
                    "Authorization": "Bearer " + token,
                },
                content=query,
            )
        except ProviderError as e:
            if e.code != "unauthorized":
                raise
            _tokens.pop(token_key, None)
            # One forced exchange only. Invalid credentials cannot recurse.
            t = await self.http.request(
                "igdb",
                "POST",
                "https://id.twitch.tv/oauth2/token",
                cache=False,
                data={
                    "client_id": settings.igdb_client_id,
                    "client_secret": settings.igdb_client_secret,
                    "grant_type": "client_credentials",
                },
            )
            token = t.get("access_token")
            if not token:
                raise ProviderError("unauthorized")
            _tokens[token_key] = (
                token,
                time.monotonic() + max(1, int(t.get("expires_in", 0)) - 60),
            )
            result = await self.http.request(
                "igdb",
                "POST",
                "https://api.igdb.com/v4/" + endpoint,
                headers={
                    "Client-ID": settings.igdb_client_id,
                    "Authorization": "Bearer " + token,
                },
                content=query,
            )
        if not isinstance(result, list):
            raise ProviderError("bad_response")
        return result

    async def hardcover(self, query, variables=None):
        token = (settings.hardcover_api_key or "").removeprefix("Bearer ")
        data = await self.http.request(
            "hardcover",
            "POST",
            "https://api.hardcover.app/v1/graphql",
            headers={"Authorization": "Bearer " + token},
            json={"query": query, "variables": variables or {}},
        )
        if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
            raise ProviderError("bad_response")
        return data["data"]

    async def search(self, provider, kind, query, page=1, limit=20):
        self.validate(provider, kind)
        if not 1 <= page <= 100 or not 1 <= limit <= 50 or not 1 <= len(query) <= 200:
            raise ProviderError("invalid_request")
        if provider == "openlibrary":
            from .openlibrary import search

            return await search(self.http, query, page, limit)
        if provider == "igdb":
            rows = await self.igdb(
                "games",
                f"search {json.dumps(query)}; fields id,name,cover.url,first_release_date; where version_parent = null; limit {limit}; offset {(page - 1) * limit};",
            )
            return [
                {
                    "external_id": str(r["id"]),
                    "name": r["name"],
                    "image_url": r.get("cover", {}).get("url"),
                }
                for r in rows
            ]
        if provider == "hardcover":
            d = await self.hardcover(
                'query($q:String!,$page:Int!,$limit:Int!){ search(query:$q,query_type:"Book",page:$page,per_page:$limit){results} }',
                {"q": query, "page": page, "limit": limit},
            )
            search = d.get("search") or {}
            results = search.get("results") or {}
            if isinstance(results, str):
                try:
                    results = json.loads(results)
                except ValueError:
                    raise ProviderError("bad_response") from None
            return [
                {
                    "external_id": str(h["document"]["id"]),
                    "name": h["document"]["title"],
                    "image_url": (h["document"].get("image") or {}).get("url"),
                }
                for h in results.get("hits", [])
            ][:limit]
        if provider == "rawg":
            d = await self.http.request(
                "rawg",
                "GET",
                "https://api.rawg.io/api/games",
                params={
                    "key": settings.rawg_api_key,
                    "search": query,
                    "page": page,
                    "page_size": min(limit, 40),
                },
            )
            return [
                {
                    "external_id": str(r["id"]),
                    "name": r["name"],
                    "image_url": r.get("background_image"),
                }
                for r in d.get("results", [])
            ][:limit]
        if provider == "tmdb":
            from core import tmdb

            async with self.http.lane("tmdb"):
                d = (
                    await tmdb.search_movies(query, api_key=self.tmdb_key, page=page)
                    if kind == "movie"
                    else await tmdb.search_shows(
                        query, api_key=self.tmdb_key, page=page
                    )
                )
            return [
                {"external_id": str(r["id"]), "name": r.get("title") or r.get("name")}
                for r in d.get("results", [])
            ][:limit]
        from core import tvdb

        async with self.http.lane("tvdb"):
            rows = await tvdb.search_series(query, self.tvdb_key)
        return [
            {
                "external_id": str(r["tvdb_id"]),
                "name": r["title"],
                "image_url": r.get("image_url"),
            }
            for r in rows[(page - 1) * limit : page * limit]
        ]

    async def detail(self, provider, kind, external_id):
        self.validate(provider, kind)
        if provider == "openlibrary":
            from .openlibrary import detail

            return await asyncio.wait_for(detail(self.http, external_id), timeout=120)
        if not str(external_id).isdigit() or int(external_id) <= 0:
            raise ProviderError("invalid_request")
        return await asyncio.wait_for(
            self._detail(provider, kind, int(external_id)), timeout=120
        )

    async def openlibrary_isbn(self, value):
        from .openlibrary import lookup_isbn

        return await lookup_isbn(self.http, value)

    async def openlibrary_editions_page(self, external_id, page):
        from .openlibrary import detail

        return await asyncio.wait_for(detail(self.http, external_id, page), timeout=120)

    async def _detail(self, provider, kind, id):
        if provider == "igdb":
            rows = await self.igdb(
                "games", f"fields {IGDB_FIELDS}; where id = {id}; limit 1;"
            )
            if not rows:
                raise ProviderError("not_found")
            raw = rows[0]
            version = None
            if raw.get("version_parent"):
                version = raw
                rows = await self.igdb(
                    "games",
                    f"fields {IGDB_FIELDS}; where id = {int(raw['version_parent'])}; limit 1;",
                )
                if not rows:
                    raise ProviderError("not_found")
                raw = rows[0]
                if raw.get("version_parent"):
                    raise ProviderError("identity_conflict")
            raw["_characters"], complete = await self._igdb_pages(
                "characters",
                f"fields id,name,description,mug_shot.url; where games = ({raw['id']}); sort id asc;",
            )
            raw["_coverage"] = {
                "characters_complete": complete,
                "release_dates": "provider_projection",
                "contributors": "companies_only",
            }
            doc = normalize_igdb(raw)
            if version:
                from .catalogue_normalize import entity

                doc["releases"].append(
                    {
                        "entity": entity("release", "igdb.game_version", version),
                        "platform_namespace": "igdb.version",
                        "platform_id": str(version["id"]),
                    }
                )
                # Provider search/detail IDs and verified Steam IDs of an
                # edition resolve to the work, while its release stays distinct.
                doc["work"]["identities"].extend(
                    x
                    for x in normalize_igdb(version)["work"]["identities"]
                    if x not in doc["work"]["identities"]
                )
            return doc, {"game": raw, "version": version}
        if provider == "hardcover":
            d = await self.hardcover(
                "query($id:Int!){books(where:{id:{_eq:$id}},limit:1){id canonical_id title description release_date pages cached_image}}",
                {"id": id},
            )
            if not d.get("books"):
                raise ProviderError("not_found")
            raw = d["books"][0]
            alias_id = None
            if raw.get("canonical_id") and raw["canonical_id"] != id:
                alias_id = id
                id = raw["canonical_id"]
                d = await self.hardcover(
                    "query($id:Int!){books(where:{id:{_eq:$id}},limit:1){id canonical_id title description release_date pages cached_image}}",
                    {"id": id},
                )
                if not d.get("books"):
                    raise ProviderError("not_found")
                raw = d["books"][0]
                if raw.get("canonical_id") and raw["canonical_id"] != id:
                    raise ProviderError("identity_conflict")
            coverage = {}
            queries = [
                (
                    "_contributions",
                    "contributions",
                    f'contributable_id:{{_eq:{id}}},contributable_type:{{_eq:"Book"}}',
                    "id contribution author {id name bio cached_image}",
                ),
                (
                    "_editions",
                    "editions",
                    f"book_id:{{_eq:{id}}}",
                    "id book_id title edition_format pages release_date isbn_10 isbn_13 cached_image publisher {id name} language {code2}",
                ),
                (
                    "_characters",
                    "book_characters",
                    f"book_id:{{_eq:{id}}}",
                    "id spoiler only_mentioned character {id name biography image_id}",
                ),
                (
                    "_series",
                    "book_series",
                    f"book_id:{{_eq:{id}}}",
                    "id position compilation series {id name description}",
                ),
            ]
            for key, table, where, fields in queries:
                (
                    raw[key],
                    coverage[key.removeprefix("_") + "_complete"],
                ) = await self._hc_pages(table, where, fields)
            edition_ids = [e["id"] for e in raw["_editions"]]
            if edition_ids:
                (
                    raw["_edition_contributions"],
                    coverage["edition_contributions_complete"],
                ) = await self._hc_pages(
                    "contributions",
                    "contributable_id:{_in:"
                    + json.dumps(edition_ids)
                    + '},contributable_type:{_eq:"Edition"}',
                    "id contributable_id contribution author {id name bio cached_image}",
                )
            image_ids = sorted(
                {
                    c["character"]["image_id"]
                    for c in raw["_characters"]
                    if c.get("character", {}).get("image_id")
                }
            )
            if image_ids:
                d = await self.hardcover(
                    "query {images(where:{id:{_in:"
                    + json.dumps(image_ids)
                    + "}},limit:250){id url}}"
                )
                images = {x["id"]: x for x in d.get("images", [])}
                for c in raw["_characters"]:
                    if c.get("character"):
                        c["character"]["_image"] = images.get(
                            c["character"].get("image_id")
                        )
            raw["_coverage"] = coverage
            doc = normalize_hardcover(raw)
            if alias_id:
                from .catalogue_normalize import identity

                doc["work"]["identities"].append(identity("hardcover.book", alias_id))
            return doc, raw
        if provider == "rawg":
            raw = await self.http.request(
                "rawg",
                "GET",
                f"https://api.rawg.io/api/games/{id}",
                params={"key": settings.rawg_api_key},
            )
            stores = []
            complete = False
            for page in range(1, 6):
                d = await self.http.request(
                    "rawg",
                    "GET",
                    f"https://api.rawg.io/api/games/{id}/stores",
                    params={
                        "key": settings.rawg_api_key,
                        "page": page,
                        "page_size": 40,
                    },
                )
                stores.extend(d.get("results", []))
                if not d.get("next"):
                    complete = True
                    break
            raw["_stores"] = stores
            raw["_stores_complete"] = complete
            return normalize_rawg(raw), raw
        if provider == "tmdb":
            from core import tmdb

            async with self.http.lane("tmdb"):
                raw = (
                    await tmdb.get_movie(id, self.tmdb_key)
                    if kind == "movie"
                    else await tmdb.get_show(id, self.tmdb_key)
                )
            if kind == "series":
                # Aggregate roles preserve each performance; regular credits do
                # not claim all season/episode appearances.
                async with self.http.lane("tmdb"):
                    raw = {
                        **raw,
                        "aggregate_credits": await tmdb.get_show_aggregate_credits(
                            id, self.tmdb_key
                        ),
                    }
            return normalize_tmdb(raw, kind), raw
        from core import tvdb

        async with self.http.lane("tvdb"):
            raw = await tvdb.get_series(id, self.tvdb_key)
        return normalize_tvdb(raw), raw

    async def book_editions_page(self, id, page):
        """Resume a book with more editions than the ordinary detail bound."""
        if not isinstance(id, int) or id <= 0 or not 1 <= page <= 10000:
            raise ProviderError("invalid_request")
        return await asyncio.wait_for(self._book_editions_page(id, page), timeout=120)

    async def _book_editions_page(self, id, page):
        d = await self.hardcover(
            "query($id:Int!){books(where:{id:{_eq:$id}},limit:1){id canonical_id title description release_date pages cached_image}}",
            {"id": id},
        )
        if not d.get("books"):
            raise ProviderError("not_found")
        raw = d["books"][0]
        alias_id = None
        if raw.get("canonical_id") and raw["canonical_id"] != id:
            alias_id = id
            id = raw["canonical_id"]
            d = await self.hardcover(
                "query($id:Int!){books(where:{id:{_eq:$id}},limit:1){id canonical_id title description release_date pages cached_image}}",
                {"id": id},
            )
            if not d.get("books"):
                raise ProviderError("not_found")
            raw = d["books"][0]
            if raw.get("canonical_id") and raw["canonical_id"] != id:
                raise ProviderError("identity_conflict")
        fields = "id book_id title edition_format pages release_date isbn_10 isbn_13 cached_image publisher {id name} language {code2}"
        d = await self.hardcover(
            "query {editions(where:{book_id:{_eq:"
            + str(id)
            + "}},limit:50,offset:"
            + str((page - 1) * 50)
            + ",order_by:{id:asc}){"
            + fields
            + "}}"
        )
        raw["_editions"] = d.get("editions", [])
        ids = [e["id"] for e in raw["_editions"]]
        if ids:
            raw["_edition_contributions"], complete = await self._hc_pages(
                "contributions",
                "contributable_id:{_in:"
                + json.dumps(ids)
                + '},contributable_type:{_eq:"Edition"}',
                "id contributable_id contribution author {id name bio cached_image}",
            )
        else:
            complete = True
        raw["_coverage"] = {
            "edition_page": page,
            "next_edition_page": page + 1 if len(raw["_editions"]) == 50 else None,
            "edition_contributions_complete": complete,
        }
        doc = normalize_hardcover(raw)
        if alias_id:
            from .catalogue_normalize import identity

            doc["work"]["identities"].append(identity("hardcover.book", alias_id))
        return doc, raw

    async def _igdb_pages(self, endpoint, query):
        out = []
        for page in range(5):
            rows = await self.igdb(endpoint, f"{query} limit 50; offset {page * 50};")
            out.extend(rows)
            if len(rows) < 50:
                return out, True
        return out, False

    async def _hc_pages(self, table, where, fields):
        out = []
        for page in range(5):
            query = (
                "query {"
                + table
                + "(where:{"
                + where
                + "},limit:50,offset:"
                + str(page * 50)
                + ",order_by:{id:asc}){"
                + fields
                + "}}"
            )
            d = await self.hardcover(query)
            rows = d.get(table, [])
            out.extend(rows)
            if len(rows) < 50:
                return out, True
        return out, False

    async def steam_prices(self, appid, country="DE"):
        return await asyncio.wait_for(self._steam_prices(appid, country), timeout=120)

    async def _steam_prices(self, appid, country):
        if not settings.itad_api_key:
            raise ProviderError("not_configured")
        if (
            not str(appid).isdigit()
            or int(appid) <= 0
            or len(country) != 2
            or not country.isalpha()
        ):
            raise ProviderError("invalid_request")
        params = {"key": settings.itad_api_key}
        lookup = await self.http.request(
            "itad",
            "GET",
            "https://api.isthereanydeal.com/games/lookup/v1",
            params={**params, "appid": str(appid)},
        )
        if not lookup.get("found") or not lookup.get("game"):
            raise ProviderError("not_found")
        gid = lookup["game"]["id"]
        # Steam is shop 61 in the current official catalogue, verified live.
        params.update(country=country.upper(), shops="61")
        prices = await self.http.request(
            "itad",
            "POST",
            "https://api.isthereanydeal.com/games/prices/v3",
            params=params,
            json=[gid],
        )
        lows = await self.http.request(
            "itad",
            "POST",
            "https://api.isthereanydeal.com/games/storelow/v2",
            params=params,
            json=[gid],
        )
        deals = [
            d
            for row in prices
            if row.get("id") == gid
            for d in row.get("deals", [])
            if d.get("shop", {}).get("id") == 61
        ]
        low = [
            d
            for row in lows
            if row.get("id") == gid
            for d in row.get("lows", [])
            if d.get("shop", {}).get("id") == 61
        ]
        current = deals[0] if deals else {}
        historical = low[0] if low else {}
        return {
            "itad_id": gid,
            "current": current.get("price"),
            "historical_low": historical.get("price"),
            "historical_low_at": historical.get("timestamp"),
            "url": current.get("url"),
            "payload": {"lookup": lookup, "prices": prices, "storelow": lows},
        }
