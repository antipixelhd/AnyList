"""Paged discovery with matching local filters and private viewer list state."""

import asyncio
from datetime import date, timedelta

from sqlalchemy import Float, and_, func, or_, select, text

from core import tmdb, tracking_projection
from core.tracking_rules import effective_score
from models import Media
from models.base import MediaType
from models.tracking import TrackedEntry

GENRES = {
    28: "Action",
    12: "Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    14: "Fantasy",
    36: "History",
    27: "Horror",
    10402: "Music",
    9648: "Mystery",
    10749: "Romance",
    878: "Science Fiction",
    10770: "TV Movie",
    53: "Thriller",
    10752: "War",
    37: "Western",
    10759: "Action & Adventure",
    10762: "Kids",
    10763: "News",
    10764: "Reality",
    10765: "Sci-Fi & Fantasy",
    10766: "Soap",
    10767: "Talk",
    10768: "War & Politics",
}
TV_GENRES = {
    16,
    35,
    80,
    99,
    18,
    10751,
    9648,
    37,
    10759,
    10762,
    10763,
    10764,
    10765,
    10766,
    10767,
    10768,
}
MOVIE_GENRES = set(GENRES) - (TV_GENRES - {16, 35, 80, 99, 18, 10751, 9648, 37})
STATUSES = {
    "airing": ["Returning Series"],
    "finished": ["Ended"],
    "cancelled": ["Canceled"],
    "upcoming": ["Planned", "In Production", "Post Production"],
    "released": ["Released"],
}
TV_STATUS = {"airing": "0", "finished": "3", "cancelled": "4", "upcoming": "1|2|5"}
SORTS = {
    "trending": "popularity.desc",
    "popular": "popularity.desc",
    "score": "vote_average.desc",
    "newest": "primary_release_date.desc",
    "oldest": "primary_release_date.asc",
    "title": "title.asc",
}
PAGE_SIZE = 24


def section_specs(today=None):
    today = today or date.today()
    month = (today.month - 1) // 3 * 3 + 1
    start = date(today.year, month, 1)
    next_season = (
        date(today.year + 1, 1, 1) if month == 10 else date(today.year, month + 3, 1)
    )
    return [
        ("Trending now", {"sort": "trending"}),
        (
            "Popular this season",
            {
                "sort": "popular",
                "start": start.isoformat(),
                "end": (next_season - timedelta(days=1)).isoformat(),
            },
        ),
        ("All-time popular", {"sort": "popular"}),
        ("Highest rated", {"sort": "score"}),
        ("Upcoming", {"sort": "oldest", "status": "upcoming"}),
    ]


async def browse_sections(db, viewer, *, media_type, region, show_anime, key):
    specs = section_specs()
    filters = [
        {
            **dict(
                genres=[],
                tags=[],
                start=None,
                end=None,
                status="",
                provider=None,
                region=region,
            ),
            **values,
        }
        for _, values in specs
    ]
    # Fetch remote feeds concurrently, but use the shared database session sequentially.
    prefetched = (
        await asyncio.gather(
            *(remote_page("", media_type, key, values, 1) for values in filters),
            return_exceptions=True,
        )
        if key
        else [None] * len(specs)
    )
    sections, notices = [], []
    for (title, values), full_filters, remote in zip(specs, filters, prefetched):
        page = await browse_page(
            db,
            viewer,
            term="",
            media_type=media_type,
            page=1,
            source="",
            show_anime=show_anime,
            key=key,
            prefetched=remote,
            **full_filters,
        )
        sections.append(
            {"title": title, "filters": values, "results": page["results"][:6]}
        )
        if page.get("notice"):
            notices.append(page["notice"])
    return {"sections": sections, "notice": next(iter(notices), None)}


def keywords(data):
    value = data.get("keywords") or []
    if isinstance(value, dict):
        value = value.get("keywords", value.get("results", []))
    return [
        row
        for row in value
        if isinstance(row, dict) and row.get("id") and row.get("name")
    ]


def metadata_fields(data):
    """Stored in existing JSONB so imports and refreshes retain discovery fields."""
    return {
        "popularity": data.get("popularity"),
        "keywords": keywords(data),
        "watch_providers": (data.get("watch/providers") or {}).get("results", {}),
    }


def remote_params(
    media_type, *, page, genres, tags, start, end, status, provider, region, sort
):
    params = {"page": page, "include_adult": "false", "sort_by": SORTS[sort]}
    if media_type == "series":
        params["sort_by"] = (
            params["sort_by"]
            .replace("primary_release_date", "first_air_date")
            .replace("title", "name")
        )
    if sort == "score":
        params["vote_count.gte"] = 100
    if genres:
        params["with_genres"] = ",".join(map(str, genres))
    if tags:
        params["with_keywords"] = ",".join(map(str, tags))
    field = "primary_release_date" if media_type == "movie" else "first_air_date"
    if start:
        params[f"{field}.gte"] = start
    if end:
        params[f"{field}.lte"] = end
    if media_type == "series" and status:
        params["with_status"] = TV_STATUS[status]
    if media_type == "movie" and status:
        if status == "upcoming":
            params[f"{field}.gte"] = max(
                start or "", (date.today() + timedelta(days=1)).isoformat()
            )
        else:
            params[f"{field}.lte"] = min(end or "9999-12-31", date.today().isoformat())
    if provider:
        params.update(
            with_watch_providers=provider,
            watch_region=region,
            with_watch_monetization_types="flatrate|free|ads",
        )
    return params


def local_query(
    media_type,
    *,
    term,
    genres,
    tags,
    start,
    end,
    status,
    provider,
    region,
    sort,
    show_anime,
):
    query = select(Media).where(
        Media.media_type == MediaType(media_type), Media.adult.is_(False)
    )
    data = Media.tmdb_data
    if not show_anime:
        animated = or_(
            data["genres"].contains(["Animation"]),
            data["genres"].contains([{"name": "Animation"}]),
        )
        japanese = or_(
            data["original_language"].astext == "ja",
            data["origin_country"].contains(["JP"]),
            data["production_countries"].contains([{"iso_3166_1": "JP"}]),
        )
        query = query.where(~func.coalesce(and_(animated, japanese), False))
    for genre in genres:
        query = query.where(
            or_(
                data["genres"].contains([GENRES[genre]]),
                data["genres"].contains([{"id": genre}]),
                data["genres"].contains([{"name": GENRES[genre]}]),
            )
        )
    for tag in tags:
        query = query.where(
            or_(
                data["keywords"].contains([{"id": tag}]),
                data["keywords"]["keywords"].contains([{"id": tag}]),
                data["keywords"]["results"].contains([{"id": tag}]),
            )
        )
    if start:
        query = query.where(Media.release_date >= start)
    if end:
        query = query.where(Media.release_date <= end)
    if status:
        if media_type == "movie":
            query = query.where(
                Media.release_date > date.today().isoformat()
                if status == "upcoming"
                else Media.release_date <= date.today().isoformat()
            )
        else:
            query = query.where(
                or_(
                    Media.status.in_(STATUSES[status]),
                    data["status"].astext.in_(STATUSES[status]),
                )
            )
    if provider:
        providers = data["watch_providers"][region]
        query = query.where(
            or_(
                *(
                    providers[k].contains([{"provider_id": provider}])
                    for k in ("flatrate", "free", "ads")
                )
            )
        )
    if term:
        similarity = func.greatest(
            func.similarity(Media.title, term),
            func.similarity(func.coalesce(Media.original_title, ""), term),
        )
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(
            or_(
                Media.title.ilike(f"%{escaped}%", escape="\\"),
                Media.original_title.ilike(f"%{escaped}%", escape="\\"),
                similarity >= 0.2,
            )
        )
        query = query.order_by(similarity.desc())
    ordering = {
        "trending": func.coalesce(data["popularity"].astext.cast(Float), 0).desc(),
        "popular": func.coalesce(data["popularity"].astext.cast(Float), 0).desc(),
        "score": Media.tmdb_rating.desc().nulls_last(),
        "newest": Media.release_date.desc().nulls_last(),
        "oldest": Media.release_date.asc().nulls_last(),
        "title": Media.title.asc(),
    }
    return query.order_by(ordering[sort], Media.id)


def matches(item, *, media_type, genres, tags, start, end, status, provider, region):
    if item.get("adult"):
        return False
    ids = set(item.get("genre_ids", [])) | {
        g["id"] for g in item.get("genres", []) if isinstance(g, dict) and g.get("id")
    }
    if not set(genres).issubset(ids) or not set(tags).issubset(
        {k["id"] for k in keywords(item)}
    ):
        return False
    released = item.get("release_date") or item.get("first_air_date") or ""
    if (start and released < start) or (end and (not released or released > end)):
        return False
    if status:
        if media_type == "movie":
            if not released or (released > date.today().isoformat()) != (
                status == "upcoming"
            ):
                return False
        elif item.get("status") not in STATUSES[status]:
            return False
    if provider:
        providers = (
            (item.get("watch/providers") or {}).get("results", {}).get(region, {})
        )
        if not any(
            p.get("provider_id") == provider
            for kind in ("flatrate", "free", "ads")
            for p in providers.get(kind, [])
        ):
            return False
    return True


async def remote_page(term, media_type, key, filters, page):
    # Imports reuse the same detail cache; at most six concurrent filtered-search lookups.
    from core.catalog_search import fuzzy_remote_terms, is_close_title_match

    kind = "movie" if media_type == "movie" else "tv"
    if not term and filters["sort"] != "trending":
        data = await tmdb._get(
            f"{tmdb.TMDB_BASE}/discover/{kind}",
            headers=tmdb.get_headers(key),
            params=remote_params(media_type, page=page, **filters),
        )
        return data, data.get("results", [])
    search = tmdb.search_movies if media_type == "movie" else tmdb.search_shows
    data = (
        await search(term, page=page, api_key=key)
        if term
        else await tmdb._get(
            f"{tmdb.TMDB_BASE}/trending/{kind}/week",
            headers=tmdb.get_headers(key),
            params={"page": page},
        )
    )
    items = data.get("results", [])
    if term and not items and page == 1:
        alternatives = await asyncio.gather(
            *(search(candidate, api_key=key) for candidate in fuzzy_remote_terms(term)),
            return_exceptions=True,
        )
        items = [
            item
            for response in alternatives
            if isinstance(response, dict)
            for item in response.get("results", [])
            if is_close_title_match(term, item.get("title") or item.get("name") or "")
        ]
        # Corrections form one finite result set, never repeat page one during scrolling.
        data = {"total_pages": 1}
    needs_details = filters["tags"] or filters["status"] or filters["provider"]
    if needs_details:
        semaphore = asyncio.Semaphore(6)
        get = tmdb.get_movie if media_type == "movie" else tmdb.get_show

        async def detail(item):
            async with semaphore:
                return await get(item["id"], api_key=key)

        items = await asyncio.gather(*(detail(item) for item in items))
    predicate = {k: v for k, v in filters.items() if k != "sort"}
    return data, [
        item for item in items if matches(item, media_type=media_type, **predicate)
    ]


async def browse_page(
    db,
    viewer,
    *,
    term,
    media_type,
    page,
    source,
    show_anime,
    key,
    prefetched=None,
    **filters,
):
    query = local_query(media_type, term=term, show_anime=show_anime, **filters)
    notice = None
    remote = None
    if source == "remote" and not key:
        raise ValueError("The metadata key is no longer available")
    if source != "local" and key:
        try:
            if isinstance(prefetched, BaseException):
                raise prefetched
            remote, items = prefetched or await remote_page(
                term, media_type, key, filters, page
            )
        except Exception:
            # Once a remote stream starts, don't silently change its pagination source.
            if source == "remote" or page > 1:
                raise
            notice = "Discovery is unavailable. Showing the local catalogue."
    elif not key:
        notice = (
            "Showing the local catalogue. Add a TMDB key in Settings to discover more."
        )
    if remote is None:
        rows = (
            (
                await db.execute(
                    query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)
                )
            )
            .scalars()
            .all()
        )
        has_more = len(rows) > PAGE_SIZE
        results = [tracking_projection.media_data(m) for m in rows[:PAGE_SIZE]]
        source = "local"
    else:
        source = "remote"
        has_more = page < min(remote.get("total_pages", 1), 500)
        # Local fuzzy matches precede the remote results and are excluded on every page.
        local = (await db.execute(query.limit(80))).scalars().all() if term else []
        local_ids = {m.tmdb_id for m in local if m.tmdb_id}
        results = (
            [tracking_projection.media_data(m) for m in local] if page == 1 else []
        )
        seen = set(local_ids)
        for item in items:
            if (
                item.get("adult")
                or item["id"] in seen
                or (
                    not show_anime
                    and tracking_projection.is_anime(
                        type(
                            "Candidate",
                            (),
                            {
                                "tmdb_data": {
                                    **item,
                                    "genres": [
                                        {"name": GENRES.get(i, "")}
                                        for i in item.get("genre_ids", [])
                                    ]
                                    or item.get("genres", []),
                                }
                            },
                        )()
                    )
                )
            ):
                continue
            seen.add(item["id"])
            results.append(
                {
                    "id": None,
                    "tmdb_id": item["id"],
                    "type": media_type,
                    "title": item.get("title") or item.get("name"),
                    "poster": item.get("poster_path"),
                    "year": (
                        item.get("release_date") or item.get("first_air_date") or ""
                    )[:4],
                    "tmdb_score": item.get("vote_average"),
                    "genres": [GENRES.get(i, "") for i in item.get("genre_ids", [])],
                }
            )
        ids = [r["tmdb_id"] for r in results if r.get("tmdb_id")]
        known = (
            (
                await db.execute(
                    select(Media).where(
                        Media.media_type == MediaType(media_type),
                        Media.tmdb_id.in_(ids),
                    )
                )
            )
            .scalars()
            .all()
            if ids
            else []
        )
        mapping = {m.tmdb_id: m.id for m in known}
        for row in results:
            row["id"] = row.get("id") or mapping.get(row["tmdb_id"])
    # Only the authenticated viewer's state is returned, in a single query per page.
    entries = {}
    ids = [r["id"] for r in results if r.get("id")]
    if viewer and ids:
        entries = {
            e.media_id: e
            for e in (
                await db.execute(
                    select(TrackedEntry).where(
                        TrackedEntry.user_id == viewer.id,
                        TrackedEntry.media_id.in_(ids),
                    )
                )
            )
            .scalars()
            .all()
        }
    for row in results:
        entry = entries.get(row["id"])
        row["list_status"] = entry.status if entry else None
        row["score"] = (
            effective_score(entry.rating_mode, entry.manual_score, entry.season_scores)
            if entry
            else None
        )
        row["rating_mode"] = entry.rating_mode if entry else "manual"
    return {
        "results": results,
        "page": page,
        "has_more": has_more,
        "source": source,
        "notice": notice,
    }


async def local_providers(db, region):
    rows = await db.execute(
        text("""
        SELECT DISTINCT (provider->>'provider_id')::integer AS id, provider->>'provider_name' AS name
        FROM media, LATERAL jsonb_path_query(tmdb_data, CAST(:path AS jsonpath)) AS provider
        WHERE provider->>'provider_id' ~ '^[0-9]+$' AND provider->>'provider_name' IS NOT NULL
        ORDER BY name
    """),
        {
            "path": f"$.watch_providers.{region}.** ? (@.provider_id != null && @.provider_name != null)"
        },
    )
    return [dict(row) for row in rows.mappings()]


async def local_tags(db, term, selected):
    rows = await db.execute(
        text("""
        SELECT DISTINCT (tag->>'id')::integer AS id, tag->>'name' AS name
        FROM media, LATERAL jsonb_path_query(tmdb_data, '$.keywords.** ? (@.id != null && @.name != null)') AS tag
        WHERE tag->>'id' ~ '^[0-9]+$' AND
              (CASE WHEN :selected THEN (tag->>'id')::integer = ANY(CAST(:ids AS integer[]))
                    ELSE strpos(lower(tag->>'name'), lower(:term)) > 0 END)
        ORDER BY name LIMIT 20
    """),
        {"term": term, "selected": bool(selected), "ids": selected},
    )
    return [dict(row) for row in rows.mappings()]
