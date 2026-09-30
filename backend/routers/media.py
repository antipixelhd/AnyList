from core import media_presentation
from core import arr_settings
from core import outbound_sync
from core import settings_store
import asyncio
import httpx
import logging
import re
import urllib.parse
from typing import Optional

logger = logging.getLogger(__name__)
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from fastapi.responses import Response
from starlette.background import BackgroundTask
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, delete, func, cast as sa_cast, Text, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from db import get_db
from models.media import Media
from models.collection import Collection, CollectionFile
from models.events import WatchEvent
from models.base import MediaType, CollectionSource
from models.lists import List as UserList, ListItem
from models.media_request import MediaRequest, RequestStatus
from core.episode_order import (
    canonical_pairs_for_display_season,
    normalize_order_key,
    is_aired_order,
)
from core import tmdb
from core.identity import find_media
from core.networks import search_curated_networks
from core.translations import (
    get_user_metadata_language,
    get_media_translations,
    upsert_media_translation,
    apply_translations,
)
from dependencies import get_current_user, get_current_user_or_api_key, get_optional_user_or_api_key, ANON_USER_ID, require_admin
from models.users import User, UserSettings
from models.show import Show as ShowModel
from models.global_settings import GlobalSettings

router = APIRouter()
_PLAYABLE_COLLECTION_SOURCES = {
    CollectionSource.jellyfin,
    CollectionSource.emby,
    CollectionSource.plex,
}


@router.get("")
async def list_media(
    type: MediaType | None = Query(None),
    sort: str = Query(default="created_at"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    genre: list[str] = Query(default=[]),
    year: list[int] = Query(default=[]),
    watched: list[str] = Query(default=[]),
    # in_list = in|out: membership of any of the user's lists (#255).
    in_list: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    offset = (page - 1) * page_size

    filters = [Collection.user_id == current_user.id]
    if in_list in ("in", "out"):
        in_list_exists = (
            select(ListItem.id)
            .join(UserList, UserList.id == ListItem.list_id)
            .where(ListItem.media_id == Media.id, UserList.user_id == current_user.id)
            .exists()
        )
        filters.append(in_list_exists if in_list == "in" else ~in_list_exists)
    if type:
        filters.append(Media.media_type == type)
    if genre:
        filters.append(or_(*[
            sa_cast(Media.tmdb_data["genres"], Text).contains(f'"{g}"') for g in genre
        ]))
    if year:
        filters.append(or_(*[Media.release_date.like(f'{y}%') for y in year]))
    watched_states = set(watched)
    if watched_states and watched_states != {"watched", "unwatched"}:
        watched_exists = (
            select(WatchEvent.id)
            .where(WatchEvent.media_id == Media.id, WatchEvent.user_id == current_user.id, WatchEvent.completed == True)
            .exists()
        )
        filters.append(watched_exists if "watched" in watched_states else ~watched_exists)

    base_query = (
        select(Media)
        .options(joinedload(Media.show))
        .join(Collection, Collection.media_id == Media.id)
        .where(*filters)
    )

    # Count total
    count_query = (
        select(func.count())
        .select_from(Media)
        .join(Collection, Collection.media_id == Media.id)
        .where(*filters)
    )
    total_result = await db.execute(count_query)
    total_count = total_result.scalar_one()
    total_pages = (total_count + page_size - 1) // page_size

    # Sort and Paginate
    # Every sort here is paginated, so each one needs a unique final key or rows
    # shift between pages and items get shown twice or skipped entirely.
    if sort == "last_watched":
        last_watched_sq = (
            select(WatchEvent.media_id, func.max(WatchEvent.watched_at).label("last_watched_at"))
            .where(WatchEvent.user_id == current_user.id)
            .group_by(WatchEvent.media_id)
            .subquery()
        )
        query = (
            base_query
            .outerjoin(last_watched_sq, last_watched_sq.c.media_id == Media.id)
            .order_by(last_watched_sq.c.last_watched_at.desc().nulls_last(), Media.id.desc())
            .offset(offset).limit(page_size)
        )
    else:
        sort_map = {
            "rating": Media.tmdb_rating.desc().nulls_last(),
            "release_date": Media.release_date.desc().nulls_last(),
            "title": func.lower(Media.title).asc(),
            "created_at": Collection.added_at.desc(),
        }
        order = sort_map.get(sort, Collection.added_at.desc())
        query = base_query.order_by(order, Media.id.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    results = [media_presentation.format_media(m) for m in items]
    await media_presentation.enrich_with_state(db, current_user.id, results)
    lang = await get_user_metadata_language(db, current_user.id)
    if lang:
        media_ids = [r["id"] for r in results if r.get("id")]
        translations = await get_media_translations(db, media_ids, lang)
        apply_translations(results, translations)
    return {
        "page": page,
        "page_size": page_size,
        "total_results": total_count,
        "total_pages": total_pages,
        "results": results,
    }


@router.get("/years")
async def list_media_years(
    type: MediaType = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    """Distinct release years present in the user's collection, for the year filter."""
    result = await db.execute(
        select(func.substr(Media.release_date, 1, 4))
        .join(Collection, Collection.media_id == Media.id)
        .where(
            Collection.user_id == current_user.id,
            Media.media_type == type,
            Media.release_date.isnot(None),
            Media.release_date != "",
        )
        .distinct()
    )
    years = sorted(
        {int(row[0]) for row in result.all() if row[0] and row[0].isdigit()},
        reverse=True,
    )
    return {"years": years}


@router.get("/find-by-imdb")
async def find_by_imdb(
    imdb_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    """Resolve an IMDB ID to a TMDB TV show via the TMDB /find endpoint."""
    imdb_id = imdb_id.strip()
    if not imdb_id.startswith("tt"):
        raise HTTPException(status_code=400, detail="Invalid IMDB ID — must start with 'tt'")
    tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)
    if not settings_store.check_tmdb_key(tmdb_key):
        raise HTTPException(status_code=400, detail="TMDB API key required")
    try:
        data = await tmdb.find_by_external_id(imdb_id, "imdb_id", api_key=tmdb_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TMDB lookup failed: {e}")
    return [
        {
            "tmdb_id": r["id"],
            "title": r.get("name") or r.get("original_name"),
            "first_air_date": r.get("first_air_date"),
            "poster_path": tmdb.poster_url(r.get("poster_path")),
        }
        for r in data.get("tv_results", [])
    ]


@router.get("/search-tvdb")
async def search_tvdb(
    q: str = Query(..., min_length=2),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    from core import tvdb as tvdb_client

    api_key = await settings_store.get_user_tvdb_key(db, current_user.id)
    if not api_key:
        raise HTTPException(status_code=400, detail="TVDB API key not configured")

    try:
        results = await tvdb_client.search_series(q, api_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TVDB search failed: {e}")
    return results


async def _apply_search_translations(db: AsyncSession, user_id: int, items: list[dict], lang: str | None) -> None:
    """Overlay the user's stored metadata-language translations onto library
    rows in search results, so a French profile doesn't see English titles (#417)."""
    if not lang:
        return
    media_ids = [i["id"] for i in items if i.get("id")]
    if media_ids:
        apply_translations(items, await get_media_translations(db, media_ids, lang))


@router.get("/search")
async def search_media(
    q: str = Query(..., min_length=2),
    type: str | None = Query(None),
    year: int | None = Query(None),
    page: int = Query(1, ge=1),
    in_library: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID
    lang = await get_user_metadata_language(db, effective_user_id)

    valid_types = {m.value for m in MediaType} | {"person", "collection", "network", "studio"}
    if type is not None and type not in valid_types:
        type = None

    # Collection search: TMDB only, no local DB
    if type == "collection":
        tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
        if not settings_store.check_tmdb_key(tmdb_key):
            return {"page": page, "total_pages": 1, "total_results": 0, "results": []}
        try:
            data = await tmdb.search_collection(q, page=page, api_key=tmdb_key)
            collections = [
                {
                    "id": None,
                    "tmdb_id": c.get("id"),
                    "type": "collection",
                    "title": c.get("name"),
                    "poster_path": tmdb.poster_url(c.get("poster_path")),
                    "backdrop_path": tmdb.poster_url(c.get("backdrop_path"), size="w1280"),
                    "overview": c.get("overview"),
                    "in_library": False,
                }
                for c in data.get("results", [])
            ]
        except Exception as e:
            print(f"TMDB collection search error: {e}")
            collections = []
            data = {}
        return {
            "page": page,
            "total_pages": data.get("total_pages", 1),
            "total_results": data.get("total_results", 0),
            "results": collections,
        }

    # People search: TMDB only, no local DB
    if type == "person":
        tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
        if not settings_store.check_tmdb_key(tmdb_key):
            return {"page": page, "total_pages": 1, "total_results": 0, "results": []}
        try:
            data = await tmdb.search_people(q, page=page, api_key=tmdb_key)
            people = [
                {
                    "id": None,
                    "tmdb_id": p.get("id"),
                    "type": "person",
                    "title": p.get("name"),
                    "poster_path": tmdb.poster_url(p.get("profile_path")),
                    "known_for_department": p.get("known_for_department"),
                    "in_library": False,
                }
                for p in data.get("results", [])
            ]
        except Exception as e:
            print(f"TMDB people search error: {e}")
            people = []
            data = {}
        return {
            "page": page,
            "total_pages": data.get("total_pages", 1),
            "total_results": data.get("total_results", 0),
            "results": people,
        }

    # Studio (production company) search: TMDB /search/company. Results link to
    # the /studio/{id} browse page.
    if type == "studio":
        tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
        if not settings_store.check_tmdb_key(tmdb_key):
            return {"page": page, "total_pages": 1, "total_results": 0, "results": []}
        try:
            data = await tmdb.search_company(q, page=page, api_key=tmdb_key)
        except Exception as e:
            print(f"TMDB company search error: {e}")
            data = {}
        studios = [
            {
                "id": None,
                "tmdb_id": c.get("id"),
                "type": "studio",
                "title": c.get("name"),
                "logo_path": tmdb.poster_url(c.get("logo_path")) if c.get("logo_path") else None,
                "origin_country": c.get("origin_country") or None,
                "in_library": False,
            }
            for c in data.get("results", [])
            if c.get("id")
        ]
        return {
            "page": page,
            "total_pages": data.get("total_pages", 1),
            "total_results": data.get("total_results", len(studios)),
            "results": studios,
        }

    # Network search: TMDB has no network-search endpoint, so match a curated
    # list of major networks (core/networks.py) unioned with the networks
    # actually attached to shows in the local DB. Results link to /network/{id}.
    if type == "network":
        db_rows = await db.execute(
            text(
                """
                SELECT (n->>'id')::int AS id,
                       max(n->>'name') AS name,
                       max(n->>'logo_path') AS logo_path,
                       max(n->>'origin_country') AS origin_country,
                       count(*) AS shows
                FROM shows, jsonb_array_elements(coalesce(tmdb_data->'networks', '[]'::jsonb)) n
                WHERE n->>'id' IS NOT NULL AND n->>'name' ILIKE :pat
                GROUP BY 1
                ORDER BY shows DESC
                LIMIT 60
                """
            ),
            {"pat": f"%{q}%"},
        )
        by_id: dict[int, dict] = {}
        for row in db_rows:
            logo = row.logo_path
            by_id[row.id] = {
                "id": None,
                "tmdb_id": row.id,
                "type": "network",
                "title": row.name,
                "logo_path": (logo if (logo or "").startswith("http") else tmdb.poster_url(logo)) if logo else None,
                "origin_country": row.origin_country or None,
                "in_library": False,
                "_shows": row.shows,
            }
        for cur in search_curated_networks(q):
            by_id.setdefault(
                cur["id"],
                {
                    "id": None,
                    "tmdb_id": cur["id"],
                    "type": "network",
                    "title": cur["name"],
                    "logo_path": None,
                    "origin_country": cur.get("origin_country") or None,
                    "in_library": False,
                    "_shows": 0,
                },
            )
        networks = sorted(by_id.values(), key=lambda n: (-n["_shows"], n["title"].lower()))
        for n in networks:
            n.pop("_shows", None)
        return {
            "page": 1,
            "total_pages": 1,
            "total_results": len(networks),
            "results": networks,
        }

    # Episode search: local DB only (TMDB has no episode search endpoint)
    if type == MediaType.episode:
        db_query = (
            select(Media)
            .options(joinedload(Media.show))
            .where(or_(Media.title.ilike(f"%{q}%"), Media.original_title.ilike(f"%{q}%")))
            .where(Media.media_type == MediaType.episode)
            .limit(50)
        )
        result = await db.execute(db_query)
        items = result.scalars().all()
        formatted = [media_presentation.format_media(m) for m in items]
        for item in formatted:
            item["in_library"] = True
        await _apply_search_translations(db, effective_user_id, formatted, lang)
        return {"page": 1, "total_pages": 1, "total_results": len(formatted), "results": formatted}

    # Collection-only filter: search local DB, skip TMDB entirely
    if in_library:
        PAGE_SIZE = 24
        lib_q = (
            select(Media)
            .options(joinedload(Media.show))
            .join(Collection, Collection.media_id == Media.id)
            .where(
                Collection.user_id == effective_user_id,
                or_(Media.title.ilike(f"%{q}%"), Media.original_title.ilike(f"%{q}%")),
            )
        )
        if type and type in {m.value for m in MediaType}:
            lib_q = lib_q.where(Media.media_type == type)
        else:
            lib_q = lib_q.where(Media.media_type != MediaType.episode)
        count_result = await db.execute(select(func.count()).select_from(lib_q.subquery()))
        total = count_result.scalar_one()
        lib_q = lib_q.order_by(Media.title).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        items_result = await db.execute(lib_q)
        items = items_result.scalars().all()
        formatted = [media_presentation.format_media(m) for m in items]
        for item in formatted:
            item["in_library"] = True
        await _apply_search_translations(db, effective_user_id, formatted, lang)
        return {
            "page": page,
            "total_pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
            "total_results": total,
            "results": formatted,
        }

    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)

    # No TMDB key: fall back to local title search
    if not settings_store.check_tmdb_key(tmdb_key):
        db_query = (
            select(Media)
            .options(joinedload(Media.show))
            .where(or_(Media.title.ilike(f"%{q}%"), Media.original_title.ilike(f"%{q}%")))
            .limit(30)
        )
        if type:
            db_query = db_query.where(Media.media_type == type)
        else:
            db_query = db_query.where(Media.media_type != MediaType.episode)
        result = await db.execute(db_query)
        items = result.scalars().all()
        formatted = [media_presentation.format_media(m) for m in items]
        for item in formatted:
            item["in_library"] = True
        await _apply_search_translations(db, effective_user_id, formatted, lang)
        return {"page": 1, "total_pages": 1, "total_results": len(formatted), "results": formatted}

    # 1. Search TMDB (primary source for ordering)
    raw_results = []
    total_pages = 1
    total_results = 0
    try:
        if type == MediaType.movie:
            data = await tmdb.search_movies(q, page=page, year=year, api_key=tmdb_key, language=lang)
            raw_results = data.get("results", [])
            for res in raw_results:
                res["media_type"] = "movie"
            total_pages = data.get("total_pages", 1)
            total_results = data.get("total_results", 0)
        elif type == MediaType.series:
            data = await tmdb.search_shows(q, page=page, year=year, api_key=tmdb_key, language=lang)
            raw_results = data.get("results", [])
            for res in raw_results:
                res["media_type"] = "tv"
            total_pages = data.get("total_pages", 1)
            total_results = data.get("total_results", 0)
        else:
            # "All": movies + shows + people, interleaved by TMDB popularity score
            movie_data, show_data, people_data = await asyncio.gather(
                tmdb.search_movies(q, page=page, api_key=tmdb_key, language=lang),
                tmdb.search_shows(q, page=page, api_key=tmdb_key, language=lang),
                tmdb.search_people(q, page=page, api_key=tmdb_key),
            )
            movie_results = movie_data.get("results", [])
            for res in movie_results:
                res["media_type"] = "movie"
            show_results = show_data.get("results", [])
            for res in show_results:
                res["media_type"] = "tv"
            people_results = people_data.get("results", [])
            for res in people_results:
                res["media_type"] = "person"
            # Interleave by popularity so relevance is preserved across all three lists
            raw_results = sorted(
                movie_results + show_results + people_results,
                key=lambda x: x.get("popularity", 0),
                reverse=True,
            )
            total_pages = max(
                movie_data.get("total_pages", 1),
                show_data.get("total_pages", 1),
                people_data.get("total_pages", 1),
            )
            total_results = (
                movie_data.get("total_results", 0)
                + show_data.get("total_results", 0)
                + people_data.get("total_results", 0)
            )
    except Exception as e:
        print(f"TMDB search error: {e}")

    # 2. Check which TMDB results are in the local library.
    # Must filter by media_type: TMDB movie/show IDs are in separate namespaces but the
    # integers can collide with episode tmdb_ids in the local DB, corrupting the map.
    tmdb_ids_on_page = [res.get("id") for res in raw_results if res.get("id")]
    local_map: dict[tuple[int, str], Media] = {}
    if tmdb_ids_on_page:
        local_q = (
            select(Media)
            .options(joinedload(Media.show))
            .where(Media.tmdb_id.in_(tmdb_ids_on_page))
        )
        if type == MediaType.movie:
            local_q = local_q.where(Media.media_type == MediaType.movie)
        elif type == MediaType.series:
            local_q = local_q.where(Media.media_type == MediaType.series)
        else:
            # "All" search: only movies and series — episodes have their own separate tab
            local_q = local_q.where(Media.media_type.in_([MediaType.movie, MediaType.series]))
        local_result = await db.execute(local_q)
        local_map = {(m.tmdb_id, m.media_type.value): m for m in local_result.scalars().all()}

    # 3. Build enriched list preserving TMDB relevance order
    enriched = []
    seen_tmdb_ids = set()
    for res in raw_results:
        tmdb_id = res.get("id")
        media_type = res.get("media_type")
        if media_type == "tv":
            media_type = "series"
        if media_type not in ("movie", "series", "person"):
            continue

        seen_tmdb_ids.add(tmdb_id)

        if media_type == "person":
            enriched.append({
                "id": None,
                "tmdb_id": tmdb_id,
                "type": "person",
                "title": res.get("name"),
                "poster_path": tmdb.poster_url(res.get("profile_path")),
                "known_for_department": res.get("known_for_department"),
                "in_library": False,
            })
            continue

        local = local_map.get((tmdb_id, media_type))
        if local:
            item = media_presentation.format_media(local)
            item["type"] = media_type  # TMDB source of truth; local row may differ
            item["in_library"] = True
            if lang:
                # Library rows carry the default-language title; the search result
                # is already in the user's language (#417). A stored translation
                # still wins - it's overlaid below.
                item["title"] = res.get("title") or res.get("name") or item.get("title")
                item["overview"] = res.get("overview") or item.get("overview")
            # Fill in missing display fields from TMDB search result
            if not item.get("poster_path"):
                item["poster_path"] = tmdb.poster_url(res.get("poster_path"))
            if not item.get("release_date"):
                item["release_date"] = res.get("release_date") or res.get("first_air_date")
            if not item.get("title"):
                item["title"] = res.get("title") or res.get("name")
        else:
            item = {
                "id": None,
                "tmdb_id": tmdb_id,
                "type": media_type,
                "title": res.get("title") or res.get("name"),
                "original_title": res.get("original_title") or res.get("original_name"),
                "overview": res.get("overview"),
                "poster_path": tmdb.poster_url(res.get("poster_path")),
                "backdrop_path": tmdb.poster_url(res.get("backdrop_path"), size="w1280"),
                "release_date": res.get("release_date") or res.get("first_air_date"),
                "tmdb_rating": res.get("vote_average"),
                "in_library": False,
                "adult": res.get("adult", False),
            }
        enriched.append(item)

    # 4. On page 1, append local library items that TMDB didn't return
    if page == 1:
        fallback_q = (
            select(Media)
            .options(joinedload(Media.show))
            .where(or_(Media.title.ilike(f"%{q}%"), Media.original_title.ilike(f"%{q}%")))
            .where(Media.tmdb_id.notin_(seen_tmdb_ids))
            .limit(10)
        )
        if type:
            fallback_q = fallback_q.where(Media.media_type == type)
        else:
            fallback_q = fallback_q.where(Media.media_type != MediaType.episode)
        fallback_result = await db.execute(fallback_q)
        for m in fallback_result.scalars().all():
            item = media_presentation.format_media(m)
            item["in_library"] = True
            enriched.append(item)

    await media_presentation.enrich_with_state(db, effective_user_id, enriched)
    await _apply_search_translations(db, effective_user_id, enriched, lang)
    return {
        "page": page,
        "total_pages": total_pages,
        "total_results": total_results,
        "results": enriched,
    }


# Point-in-time snapshot of trending titles, used as the poster-wall fallback
# for instances with no TMDB key configured (or when a live fetch fails), so
# the logged-out landing page still shows real posters instead of abstract
# tiles. Will look dated over time - that's an acceptable tradeoff since it's
# only ever a fallback.


# in_list = in|out: membership of any of the user's lists (#255).


from sqlalchemy import delete as sa_delete
from pydantic import BaseModel as PydanticModel


class CollectRequest(PydanticModel):
    # Any one of media_id / tmdb_id / tvdb_id identifies the item (a
    # TVDB-only episode has no tmdb_id - see core/identity.py). Creating a
    # row that doesn't exist yet still needs tmdb_id.
    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    media_id: Optional[int] = None
    media_type: MediaType
    # Episode context — required when collecting an episode that doesn't exist in the DB yet
    series_tmdb_id: Optional[int] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None


class CollectSeasonRequest(PydanticModel):
    series_tmdb_id: int
    season_number: int
    episode_order: Optional[str] = None


# TMDB watch provider IDs for reference:
# Netflix=8, Amazon Prime=9, Apple TV+=350, Disney+=337, Max=1899, Hulu=15


@router.post("/collect")
async def manually_collect(
    body: CollectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually add a movie to the user's collection."""
    tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)
    if not (body.tmdb_id or body.tvdb_id or body.media_id):
        raise HTTPException(status_code=400, detail="One of tmdb_id, tvdb_id or media_id is required")

    # Find or create media record
    media = await find_media(
        db, body.media_type, media_id=body.media_id, tmdb_id=body.tmdb_id, tvdb_id=body.tvdb_id,
    )
    if not media and not body.tmdb_id:
        raise HTTPException(status_code=404, detail="Media not found")

    # If the episode row exists but is missing its show_id, link it now so season/show
    # collection percentages and season-page "collected" indicators stay consistent.
    if media and body.media_type == MediaType.episode and not media.show_id and body.series_tmdb_id:
        from models.show import Show as ShowModel
        show_link_q = await db.execute(select(ShowModel).where(ShowModel.tmdb_id == body.series_tmdb_id))
        show_link = show_link_q.scalar_one_or_none()
        if show_link:
            media.show_id = show_link.id

    if not media:
        if not settings_store.check_tmdb_key(tmdb_key):
            raise HTTPException(status_code=404, detail="Media not found and no TMDB key configured")
        try:
            from core.enrichment import enrich_media, create_media_safely, enrich_media_safely
            if body.media_type == MediaType.movie:
                data = await tmdb.get_movie(body.tmdb_id, api_key=tmdb_key)
                title = data.get("title", "")
                media, _created = await create_media_safely(db, body.tmdb_id, body.media_type, title=title)
                await enrich_media(media, api_key=tmdb_key)
            elif body.media_type == MediaType.episode:
                if not body.series_tmdb_id or body.season_number is None or body.episode_number is None:
                    raise HTTPException(
                        status_code=400,
                        detail="series_tmdb_id, season_number, and episode_number are required to collect a new episode",
                    )

                # Link to parent show
                from models.show import Show as ShowModel
                show_q = await db.execute(
                    select(ShowModel).where(ShowModel.tmdb_id == body.series_tmdb_id)
                )
                show = show_q.scalar_one_or_none()
                if not show:
                    # If show doesn't exist locally, create it first so the episode has a show_id
                    show_data = await tmdb.get_show(body.series_tmdb_id, api_key=tmdb_key)
                    show = ShowModel(
                        tmdb_id=body.series_tmdb_id,
                        title=show_data.get("name", ""),
                        poster_path=tmdb.poster_url(show_data.get("poster_path")),
                        backdrop_path=tmdb.poster_url(show_data.get("backdrop_path"), size="w1280"),
                        tmdb_rating=show_data.get("vote_average"),
                        status=show_data.get("status"),
                        first_air_date=show_data.get("first_air_date"),
                        last_air_date=show_data.get("last_air_date"),
                        tmdb_data={
                            "genres": [g["name"] for g in show_data.get("genres", [])],
                            "seasons": [
                                {
                                    "season_number": s["season_number"],
                                    "poster_path": tmdb.poster_url(s.get("poster_path")),
                                    "episode_count": s["episode_count"],
                                    "name": s["name"],
                                }
                                for s in show_data.get("seasons", [])
                            ],
                            "networks": [
                                {
                                    "id": n.get("id"),
                                    "name": n.get("name"),
                                    "logo_path": n.get("logo_path"),
                                    "origin_country": n.get("origin_country"),
                                }
                                for n in show_data.get("networks", [])
                            ],
                        }
                    )
                    db.add(show)
                    await db.flush()

                ep_data = await tmdb.get_episode(
                    body.series_tmdb_id, body.season_number, body.episode_number, api_key=tmdb_key
                )
                media, _created = await create_media_safely(
                    db,
                    body.tmdb_id,
                    MediaType.episode,
                    title=ep_data.get("name", ""),
                    season_number=body.season_number,
                    episode_number=body.episode_number,
                    show_id=show.id,
                )
                media = await enrich_media_safely(db, media, api_key=tmdb_key, series_tmdb_id=body.series_tmdb_id)
            else:
                raise HTTPException(status_code=400, detail=f"Manual collection not supported for type: {body.media_type}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"TMDB lookup failed: {e}")

    # Check for existing collection entry
    existing_q = await db.execute(
        select(Collection).where(
            Collection.user_id == current_user.id,
            Collection.media_id == media.id,
        )
    )
    if existing_q.scalars().first():
        return {"status": "ok", "message": "Already in collection"}

    from sqlalchemy.dialects.postgresql import insert as pg_insert
    coll_stmt = pg_insert(Collection).values(user_id=current_user.id, media_id=media.id)
    coll_stmt = coll_stmt.on_conflict_do_nothing(constraint="uq_collection_user_media")
    await db.execute(coll_stmt)
    await db.flush()
    coll_q = await db.execute(
        select(Collection).where(Collection.user_id == current_user.id, Collection.media_id == media.id)
    )
    coll = coll_q.scalar_one()
    db.add(CollectionFile(
        collection_id=coll.id,
        source=CollectionSource.manual,
        source_id=str(body.tmdb_id),
    ))
    await db.commit()
    await _push_collection_change(db, current_user.id, {media.id}, added=True)
    return {"status": "ok", "message": "Added to collection"}


async def _push_collection_change(
    db: AsyncSession,
    user_id: int,
    media_ids: set[int],
    *,
    added: bool,
) -> None:
    """Fan out a local collection mutation to enabled push targets."""
    if not media_ids:
        return
    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    settings = settings_result.scalar_one_or_none()

    await outbound_sync.fan_out_changes(
        db,
        user_id,
        None,
        set(),
        {},
        settings=settings,
        new_collected_ids=media_ids if added else set(),
        removed_collected_ids=set() if added else media_ids,
    )


@router.delete("/collect/all")
async def clear_collection(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    media_ids_q = await db.execute(
        select(Collection.media_id).where(Collection.user_id == current_user.id)
    )
    media_ids = {row[0] for row in media_ids_q.all()}

    await db.execute(delete(Collection).where(Collection.user_id == current_user.id))
    await db.commit()

    await _push_collection_change(db, current_user.id, media_ids, added=False)
    return {"status": "ok"}


@router.delete("/collect")
async def manually_uncollect(
    tmdb_id: int | None = Query(None),
    tvdb_id: int | None = Query(None),
    media_id: int | None = Query(None, alias="id"),
    media_type: MediaType = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a manually-added item from the user's collection."""
    if not (tmdb_id or tvdb_id or media_id):
        raise HTTPException(status_code=400, detail="One of tmdb_id, tvdb_id or id is required")

    media = await find_media(db, media_type, media_id=media_id, tmdb_id=tmdb_id, tvdb_id=tvdb_id)
    if not media:
        return {"status": "ok"}

    await db.execute(
        sa_delete(Collection).where(
            Collection.user_id == current_user.id,
            Collection.media_id == media.id,
        )
    )
    await db.commit()

    await _push_collection_change(db, current_user.id, {media.id}, added=False)
    return {"status": "ok", "message": "Removed from collection"}


async def _resolve_season_episodes(
    db: AsyncSession, show: "ShowModel", series_tmdb_id: int, season_number: int, tmdb_key: str | None
) -> list:
    """Return all Media rows for a season, creating or adopting rows as needed.

    Always uses TMDB as the authoritative episode list so that:
    - shows with no Media rows yet get them created
    - orphaned episodes (show_id=NULL) get adopted
    - already-linked episodes are returned as-is
    """
    if not settings_store.check_tmdb_key(tmdb_key):
        q = await db.execute(
            select(Media).where(
                Media.show_id == show.id,
                Media.media_type == MediaType.episode,
                Media.season_number == season_number,
            )
        )
        return q.scalars().all()

    try:
        season_data = await tmdb.get_season(series_tmdb_id, season_number, api_key=tmdb_key)
    except Exception:
        q = await db.execute(
            select(Media).where(
                Media.show_id == show.id,
                Media.media_type == MediaType.episode,
                Media.season_number == season_number,
            )
        )
        return q.scalars().all()

    tmdb_episodes = season_data.get("episodes", [])
    if not tmdb_episodes:
        return []

    tmdb_ids = [ep["id"] for ep in tmdb_episodes if ep.get("id")]
    existing_q = await db.execute(
        select(Media).where(
            Media.tmdb_id.in_(tmdb_ids),
            Media.media_type == MediaType.episode,
        )
    )
    existing_by_tmdb: dict[int, Media] = {m.tmdb_id: m for m in existing_q.scalars().all()}

    result: list[Media] = []
    for ep in tmdb_episodes:
        tid = ep.get("id")
        if not tid:
            continue
        media = existing_by_tmdb.get(tid)
        if media:
            if not media.show_id:
                media.show_id = show.id
        else:
            from core.enrichment import create_media_safely
            media, _created = await create_media_safely(
                db,
                tid,
                MediaType.episode,
                title=ep.get("name", ""),
                season_number=season_number,
                episode_number=ep.get("episode_number"),
                show_id=show.id,
                overview=ep.get("overview"),
                release_date=ep.get("air_date"),
                tmdb_rating=ep.get("vote_average"),
                poster_path=tmdb.poster_url(ep.get("still_path"), size="w500"),
            )
        result.append(media)

    await db.flush()
    return result


async def _resolve_season_episodes_from_tvdb(
    db: AsyncSession, show: "ShowModel", tvdb_id: int, season_number: int, tvdb_key: str,
    language: str | None = None,
) -> list:
    """TVDB equivalent of _resolve_season_episodes, for a season TMDB doesn't
    have at all (see #101). Enriches new rows via enrich_episode_from_tvdb,
    which stores the TVDB episode id as a disguised tmdb_id — see
    core/enrichment.py for why that's safe."""
    import core.tvdb as tvdb_client
    from core.enrichment import enrich_episode_from_tvdb

    try:
        raw_eps = await tvdb_client.get_series_episodes(tvdb_id, season_number, tvdb_key, language=language)
    except Exception:
        q = await db.execute(
            select(Media).where(
                Media.show_id == show.id,
                Media.media_type == MediaType.episode,
                Media.season_number == season_number,
            )
        )
        return q.scalars().all()

    tvdb_eps = [tvdb_client.format_episode(e) for e in raw_eps]
    if not tvdb_eps:
        return []

    existing_q = await db.execute(
        select(Media).where(
            Media.show_id == show.id,
            Media.media_type == MediaType.episode,
            Media.season_number == season_number,
        )
    )
    existing_by_episode = {m.episode_number: m for m in existing_q.scalars().all()}

    result: list[Media] = []
    for ep in tvdb_eps:
        ep_num = ep.get("episode_number")
        if ep_num is None:
            continue
        media = existing_by_episode.get(ep_num)
        if not media:
            media = Media(
                media_type=MediaType.episode,
                season_number=season_number,
                episode_number=ep_num,
                show_id=show.id,
            )
            # tmdb_id isn't known until enrich_episode_from_tvdb resolves it, so
            # this can't go through create_media_safely up front - flushed
            # explicitly here instead, inside a savepoint, so a conflict with a
            # concurrently-created row for this exact episode is caught right
            # here instead of failing the whole batch's flush at the end.
            await enrich_episode_from_tvdb(media, ep)
            try:
                async with db.begin_nested():
                    db.add(media)
                    await db.flush()
            except IntegrityError:
                existing_result = await db.execute(
                    select(Media)
                    .where(Media.tmdb_id == media.tmdb_id, Media.media_type == MediaType.episode)
                    .order_by(Media.id)
                )
                existing = existing_result.scalars().first()
                if not existing:
                    raise
                media = existing
        result.append(media)

    await db.flush()
    return result


async def _collect_episodes(db: AsyncSession, user_id: int, episodes: list) -> int:
    """Insert Collection + CollectionFile(manual) for each episode, skipping existing ones."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    added = 0
    for ep in episodes:
        coll_stmt = pg_insert(Collection).values(user_id=user_id, media_id=ep.id)
        coll_stmt = coll_stmt.on_conflict_do_nothing(constraint="uq_collection_user_media")
        await db.execute(coll_stmt)
        await db.flush()
        coll_q = await db.execute(
            select(Collection).where(Collection.user_id == user_id, Collection.media_id == ep.id)
        )
        coll = coll_q.scalar_one_or_none()
        if not coll:
            continue
        existing_file_q = await db.execute(
            select(CollectionFile).where(
                CollectionFile.collection_id == coll.id,
                CollectionFile.source == CollectionSource.manual,
            )
        )
        if not existing_file_q.scalars().first():
            db.add(CollectionFile(
                collection_id=coll.id,
                source=CollectionSource.manual,
                source_id=str(ep.tmdb_id or ep.id),
            ))
            added += 1
    return added


@router.post("/collect-season")
async def collect_season(
    body: CollectSeasonRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually add all episodes in a season to the user's collection."""

    tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)

    show_q = await db.execute(select(ShowModel).where(ShowModel.tmdb_id == body.series_tmdb_id))
    show = show_q.scalar_one_or_none()
    if not show:
        if not settings_store.check_tmdb_key(tmdb_key):
            raise HTTPException(status_code=404, detail="Show not found and no TMDB key configured")
        show_data = await tmdb.get_show(body.series_tmdb_id, api_key=tmdb_key)
        show = ShowModel(
            tmdb_id=body.series_tmdb_id,
            title=show_data.get("name", ""),
            poster_path=tmdb.poster_url(show_data.get("poster_path")),
            backdrop_path=tmdb.poster_url(show_data.get("backdrop_path"), size="w1280"),
            tmdb_rating=show_data.get("vote_average"),
            status=show_data.get("status"),
            first_air_date=show_data.get("first_air_date"),
            last_air_date=show_data.get("last_air_date"),
            tmdb_data={
                "genres": [g["name"] for g in show_data.get("genres", [])],
                "seasons": [
                    {
                        "season_number": s["season_number"],
                        "poster_path": tmdb.poster_url(s.get("poster_path")),
                        "episode_count": s["episode_count"],
                        "name": s["name"],
                    }
                    for s in show_data.get("seasons", [])
                ],
                "networks": [
                    {
                        "id": n.get("id"),
                        "name": n.get("name"),
                        "logo_path": n.get("logo_path"),
                        "origin_country": n.get("origin_country"),
                    }
                    for n in show_data.get("networks", [])
                ],
            },
        )
        db.add(show)
        await db.flush()

    _collect_order = normalize_order_key(body.episode_order)
    if not is_aired_order(_collect_order):
        target_positions = await canonical_pairs_for_display_season(
            db, body.series_tmdb_id, _collect_order, body.season_number
        )
        if not target_positions:
            # tvdb:official can still resolve straight from TVDB when the
            # mapping hasn't been computed and TMDB lacks the season (#101);
            # every other order must be built on the show page first.
            season_on_tmdb = any(
                s.get("season_number") == body.season_number
                for s in (show.tmdb_data or {}).get("seasons", [])
            )
            if _collect_order != "tvdb:official" or season_on_tmdb or not show.tvdb_id:
                raise HTTPException(status_code=400, detail="This episode order is not available for this show")
            import core.tvdb as tvdb_client

            tvdb_key = await settings_store.get_user_tvdb_key(db, current_user.id)
            if not tvdb_key:
                raise HTTPException(status_code=400, detail="TVDB API key not configured")
            tvdb_lang = tvdb_client.tvdb_language(await get_user_metadata_language(db, current_user.id))
            episodes = await _resolve_season_episodes_from_tvdb(
                db, show, show.tvdb_id, body.season_number, tvdb_key, language=tvdb_lang
            )
        else:
            episodes = []
            for canonical_season in sorted({season for season, _ in target_positions}):
                resolved = await _resolve_season_episodes(
                    db,
                    show,
                    body.series_tmdb_id,
                    canonical_season,
                    tmdb_key,
                )
                episodes.extend(
                    episode
                    for episode in resolved
                    if (episode.season_number, episode.episode_number) in target_positions
                )
    else:
        episodes = await _resolve_season_episodes(
            db,
            show,
            body.series_tmdb_id,
            body.season_number,
            tmdb_key,
        )
    if not episodes:
        return {"status": "ok", "count": 0}

    added = await _collect_episodes(db, current_user.id, episodes)
    await db.commit()
    await _push_collection_change(
        db,
        current_user.id,
        {episode.id for episode in episodes},
        added=True,
    )
    return {"status": "ok", "count": added}


@router.post("/collect-show")
async def collect_show(
    body: CollectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually collect all aired seasons/episodes for a show."""
    tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)
    if not settings_store.check_tmdb_key(tmdb_key):
        raise HTTPException(status_code=400, detail="TMDB key required to collect a show")

    show_q = await db.execute(select(ShowModel).where(ShowModel.tmdb_id == body.tmdb_id))
    show = show_q.scalar_one_or_none()
    if not show:
        show_data = await tmdb.get_show(body.tmdb_id, api_key=tmdb_key)
        show = ShowModel(
            tmdb_id=body.tmdb_id,
            title=show_data.get("name", ""),
            poster_path=tmdb.poster_url(show_data.get("poster_path")),
            backdrop_path=tmdb.poster_url(show_data.get("backdrop_path"), size="w1280"),
            tmdb_rating=show_data.get("vote_average"),
            status=show_data.get("status"),
            first_air_date=show_data.get("first_air_date"),
            last_air_date=show_data.get("last_air_date"),
            tmdb_data={
                "genres": [g["name"] for g in show_data.get("genres", [])],
                "seasons": [
                    {
                        "season_number": s["season_number"],
                        "poster_path": tmdb.poster_url(s.get("poster_path")),
                        "episode_count": s["episode_count"],
                        "name": s["name"],
                    }
                    for s in show_data.get("seasons", [])
                ],
                "networks": [
                    {
                        "id": n.get("id"),
                        "name": n.get("name"),
                        "logo_path": n.get("logo_path"),
                        "origin_country": n.get("origin_country"),
                    }
                    for n in show_data.get("networks", [])
                ],
            },
        )
        db.add(show)
        await db.flush()

    season_numbers = [
        s["season_number"]
        for s in (show.tmdb_data or {}).get("seasons", [])
        if s.get("season_number", 0) != 0
    ]

    total_added = 0
    collected_media_ids: set[int] = set()
    for sn in season_numbers:
        episodes = await _resolve_season_episodes(db, show, body.tmdb_id, sn, tmdb_key)
        total_added += await _collect_episodes(db, current_user.id, episodes)
        collected_media_ids.update(episode.id for episode in episodes)

    # Seasons TVDB has but TMDB doesn't (see #101) — only reachable once this
    # show is linked to a TVDB id (set once the user visits its TVDB page).
    if show.tvdb_id:
        import core.tvdb as tvdb_client

        tvdb_key = await settings_store.get_user_tvdb_key(db, current_user.id)
        if tvdb_key:
            tvdb_lang = tvdb_client.tvdb_language(await get_user_metadata_language(db, current_user.id))
            tmdb_season_numbers = set(season_numbers)
            try:
                tvdb_show_data = tvdb_client.format_series(await tvdb_client.get_series(show.tvdb_id, tvdb_key), language=tvdb_lang)
            except Exception:
                tvdb_show_data = None

            if tvdb_show_data:
                tvdb_only_seasons = [
                    s["season_number"] for s in tvdb_show_data.get("seasons", [])
                    if s.get("season_number") and s["season_number"] > 0 and s["season_number"] not in tmdb_season_numbers
                ]
                for sn in tvdb_only_seasons:
                    episodes = await _resolve_season_episodes_from_tvdb(db, show, show.tvdb_id, sn, tvdb_key, language=tvdb_lang)
                    total_added += await _collect_episodes(db, current_user.id, episodes)
                    collected_media_ids.update(episode.id for episode in episodes)

    await db.commit()
    await _push_collection_change(db, current_user.id, collected_media_ids, added=True)
    return {"status": "ok", "count": total_added}


@router.delete("/collect-show")
async def uncollect_show(
    tmdb_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove all collection entries for every episode in a show."""
    show_q = await db.execute(select(ShowModel).where(ShowModel.tmdb_id == tmdb_id))
    show = show_q.scalar_one_or_none()
    if not show:
        return {"status": "ok"}

    episode_ids_q = await db.execute(
        select(Media.id).where(
            Media.show_id == show.id,
            Media.media_type == MediaType.episode,
        )
    )
    episode_ids = [r[0] for r in episode_ids_q.all()]
    if episode_ids:
        await db.execute(
            sa_delete(Collection).where(
                Collection.user_id == current_user.id,
                Collection.media_id.in_(episode_ids),
            )
        )
        await db.commit()
        await _push_collection_change(db, current_user.id, set(episode_ids), added=False)
    return {"status": "ok"}


@router.delete("/collect-season")
async def uncollect_season(
    series_tmdb_id: int = Query(...),
    season_number: int = Query(...),
    episode_order: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove all collection entries for all episodes in a season."""
    from models.show import Show as ShowModel

    show_q = await db.execute(select(ShowModel).where(ShowModel.tmdb_id == series_tmdb_id))
    show = show_q.scalar_one_or_none()
    if not show:
        return {"status": "ok"}

    media_filters = [
        Media.show_id == show.id,
        Media.media_type == MediaType.episode,
    ]
    _uncollect_order = normalize_order_key(episode_order)
    if not is_aired_order(_uncollect_order):
        pairs = await canonical_pairs_for_display_season(
            db, series_tmdb_id, _uncollect_order, season_number
        )
        if not pairs:
            # No positions. tvdb:official with a TVDB-only season means the
            # episodes were tracked under raw TVDB numbers (#101) - match those.
            season_on_tmdb = any(
                s.get("season_number") == season_number
                for s in (show.tmdb_data or {}).get("seasons", [])
            )
            if season_on_tmdb or _uncollect_order != "tvdb:official":
                return {"status": "ok"}
            media_filters.append(Media.season_number == season_number)
        else:
            media_filters.append(or_(*[
                and_(Media.season_number == cs, Media.episode_number == ce)
                for cs, ce in pairs
            ]))
    else:
        media_filters.append(Media.season_number == season_number)
    episodes_q = await db.execute(select(Media.id).where(*media_filters))
    episode_ids = [r[0] for r in episodes_q.all()]
    if not episode_ids:
        return {"status": "ok"}

    await db.execute(
        sa_delete(Collection).where(
            Collection.user_id == current_user.id,
            Collection.media_id.in_(episode_ids),
        )
    )
    await db.commit()

    await _push_collection_change(db, current_user.id, set(episode_ids), added=False)
    return {"status": "ok"}


@router.get("/request-status")
async def get_request_status(
    tmdb_id: int = Query(...),
    media_type: MediaType = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    """Check whether a movie/series is already monitored in Radarr/Sonarr."""
    settings_q = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = settings_q.scalar_one_or_none()
    gs = await settings_store.get_global_settings(db)

    monitored = False

    try:
        if media_type == MediaType.movie:
            radarr_cfg = arr_settings._effective_radarr(settings, gs)
            if not radarr_cfg:
                raise HTTPException(status_code=503, detail="Radarr not configured")
            url = radarr_cfg.radarr_url.rstrip("/")
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(
                    f"{url}/api/v3/movie/lookup",
                    params={"apiKey": radarr_cfg.radarr_token, "term": f"tmdb:{tmdb_id}"},
                )
                if res.status_code == 200:
                    for entry in res.json():
                        if entry.get("id"):
                            monitored = True
                            break

        elif media_type == MediaType.series:
            sonarr_cfg = arr_settings._effective_sonarr(settings, gs)
            if not sonarr_cfg:
                raise HTTPException(status_code=503, detail="Sonarr not configured")
            tvdb_id: int | None = None
            show_q = await db.execute(select(ShowModel).where(ShowModel.tmdb_id == tmdb_id))
            show_row = show_q.scalar_one_or_none()
            if show_row and show_row.tmdb_data:
                tvdb_id = (show_row.tmdb_data.get("external_ids") or {}).get("tvdb_id")
            if not tvdb_id:
                from core import tmdb as tmdb_core
                tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)
                ext_ids = await tmdb_core.get_external_ids(tmdb_id, "tv", api_key=tmdb_key)
                tvdb_id = ext_ids.get("tvdb_id")
            if tvdb_id:
                url = sonarr_cfg.sonarr_url.rstrip("/")
                async with httpx.AsyncClient(timeout=5.0) as client:
                    res = await client.get(
                        f"{url}/api/v3/series/lookup",
                        params={"apiKey": sonarr_cfg.sonarr_token, "term": f"tvdb:{tvdb_id}"},
                    )
                    if res.status_code == 200:
                        for entry in res.json():
                            if entry.get("id"):
                                monitored = True
                                break

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Service unavailable")

    return {"monitored": monitored}


@router.get("/{type}/customize-options")
async def get_customize_options(
    type: MediaType,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Live root folders/quality profiles/tags for the customize-on-add
    popup, plus the connection's current defaults to pre-select. Admin-only,
    same as the popup itself - resolves the effective Radarr/Sonarr config
    server-side so the token never reaches the client."""
    settings_q = await db.execute(
        select(UserSettings).where(UserSettings.user_id == current_user.id)
    )
    settings = settings_q.scalar_one_or_none()
    gs = await settings_store.get_global_settings(db)

    if type == MediaType.movie:
        radarr_cfg = arr_settings._effective_radarr(settings, gs)
        if not radarr_cfg:
            raise HTTPException(status_code=400, detail="Radarr not configured in settings")
        from core import radarr
        root_folders, quality_profiles, tags = await asyncio.gather(
            radarr.get_root_folders(radarr_cfg.radarr_url, radarr_cfg.radarr_token),
            radarr.get_quality_profiles(radarr_cfg.radarr_url, radarr_cfg.radarr_token),
            radarr.get_tags(radarr_cfg.radarr_url, radarr_cfg.radarr_token),
        )
        return {
            "root_folders": root_folders,
            "quality_profiles": quality_profiles,
            "tags": tags,
            "current": {
                "root_folder": radarr_cfg.radarr_root_folder,
                "quality_profile": radarr_cfg.radarr_quality_profile,
                "tags": radarr_cfg.radarr_tags or [],
            },
        }

    elif type == MediaType.series:
        sonarr_cfg = arr_settings._effective_sonarr(settings, gs)
        if not sonarr_cfg:
            raise HTTPException(status_code=400, detail="Sonarr not configured in settings")
        from core import sonarr
        root_folders, quality_profiles, tags = await asyncio.gather(
            sonarr.get_root_folders(sonarr_cfg.sonarr_url, sonarr_cfg.sonarr_token),
            sonarr.get_quality_profiles(sonarr_cfg.sonarr_url, sonarr_cfg.sonarr_token),
            sonarr.get_tags(sonarr_cfg.sonarr_url, sonarr_cfg.sonarr_token),
        )
        return {
            "root_folders": root_folders,
            "quality_profiles": quality_profiles,
            "tags": tags,
            "current": {
                "root_folder": sonarr_cfg.sonarr_root_folder,
                "quality_profile": sonarr_cfg.sonarr_quality_profile,
                "tags": sonarr_cfg.sonarr_tags or [],
                "season_folder": sonarr_cfg.sonarr_season_folder if sonarr_cfg.sonarr_season_folder is not None else True,
            },
        }

    else:
        raise HTTPException(status_code=400, detail="Can only fetch options for movies or series")


class RequestOverrides(BaseModel):
    """Per-item root folder/quality profile/tags/season-folder override for
    an admin-initiated add - see radarr_customize_on_add/sonarr_customize_on_add.
    Only ever honored for admins (checked server-side below), regardless of
    what a client sends - the *_customize_on_add flags just control whether
    the frontend ever shows the picker that produces this body."""
    root_folder: str | None = None
    quality_profile: int | None = None
    tags: list[int] | None = None
    season_folder: bool | None = None


def _resolve_add_overrides(overrides: RequestOverrides | None, is_admin: bool) -> RequestOverrides | None:
    """Only an admin's own overrides are ever honored - a non-admin client
    sending this body just gets ignored, same as if it sent nothing. Pulled
    out as its own function so this rule is unit-testable without a full
    request/DB round-trip."""
    return overrides if (overrides and is_admin) else None


@router.post("/{type}/{tmdb_id}/request")
async def request_media(
    type: MediaType,
    tmdb_id: int,
    overrides: RequestOverrides | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Request a movie (Radarr) or series (Sonarr)."""
    settings_q = await db.execute(
        select(UserSettings).where(UserSettings.user_id == current_user.id)
    )
    settings = settings_q.scalar_one_or_none()
    gs = await settings_store.get_global_settings(db)

    async def _upsert_request(media_type_str: str, title: str, poster_path: str | None) -> dict:
        """Create or update a pending media request, return 202 response."""
        existing_q = await db.execute(
            select(MediaRequest).where(
                MediaRequest.user_id == current_user.id,
                MediaRequest.tmdb_id == tmdb_id,
                MediaRequest.media_type == media_type_str,
            )
        )
        existing = existing_q.scalar_one_or_none()
        if existing:
            if existing.status == RequestStatus.approved:
                raise HTTPException(status_code=409, detail="Already approved and added")
            existing.status = RequestStatus.pending
            existing.updated_at = func.now()
        else:
            db.add(MediaRequest(
                user_id=current_user.id,
                tmdb_id=tmdb_id,
                media_type=media_type_str,
                title=title,
                poster_path=poster_path,
                status=RequestStatus.pending,
            ))
        await db.commit()
        return {"status": "pending_approval", "message": "Request submitted for admin approval"}

    if type == MediaType.movie:
        radarr_cfg = arr_settings._effective_radarr(settings, gs)
        if not radarr_cfg:
            raise HTTPException(status_code=400, detail="Radarr not configured in settings")

        uses_global = gs and radarr_cfg is gs and not current_user.is_admin
        if uses_global and gs.radarr_require_approval:
            tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)
            title, poster = "", None
            try:
                from core import tmdb as tmdb_core
                movie_data = await tmdb_core.get_movie(tmdb_id, api_key=tmdb_key)
                title = movie_data.get("title") or ""
                poster = tmdb_core.poster_url(movie_data.get("poster_path")) if movie_data.get("poster_path") else None
            except Exception: pass
            return await _upsert_request("movie", title, poster)

        ov = _resolve_add_overrides(overrides, current_user.is_admin)

        from core import radarr
        try:
            res = await radarr.add_movie(
                url=radarr_cfg.radarr_url,
                token=radarr_cfg.radarr_token,
                tmdb_id=tmdb_id,
                title="",
                root_folder=(ov.root_folder if ov and ov.root_folder is not None else radarr_cfg.radarr_root_folder),
                quality_profile_id=(ov.quality_profile if ov and ov.quality_profile is not None else radarr_cfg.radarr_quality_profile),
                tags=(ov.tags if ov and ov.tags is not None else radarr_cfg.radarr_tags),
            )
            return res
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Radarr error: {e}")

    elif type == MediaType.series:
        sonarr_cfg = arr_settings._effective_sonarr(settings, gs)
        if not sonarr_cfg:
            raise HTTPException(status_code=400, detail="Sonarr not configured in settings")

        uses_global = gs and sonarr_cfg is gs and not current_user.is_admin
        if uses_global and gs.sonarr_require_approval:
            tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)
            title, poster = "", None
            try:
                from core import tmdb as tmdb_core
                show_data = await tmdb_core.get_show(tmdb_id, api_key=tmdb_key)
                title = show_data.get("name") or ""
                poster = tmdb_core.poster_url(show_data.get("poster_path")) if show_data.get("poster_path") else None
            except Exception: pass
            return await _upsert_request("series", title, poster)

        ov = _resolve_add_overrides(overrides, current_user.is_admin)

        from core import sonarr, tmdb
        try:
            tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)
            ext_ids = await tmdb.get_external_ids(tmdb_id, "tv", api_key=tmdb_key)
            tvdb_id = ext_ids.get("tvdb_id")

            if not tvdb_id:
                raise HTTPException(status_code=400, detail="Could not find TVDB ID for this show")

            default_season_folder = sonarr_cfg.sonarr_season_folder if sonarr_cfg.sonarr_season_folder is not None else True
            res = await sonarr.add_series(
                url=sonarr_cfg.sonarr_url,
                token=sonarr_cfg.sonarr_token,
                tvdb_id=tvdb_id,
                root_folder=(ov.root_folder if ov and ov.root_folder is not None else sonarr_cfg.sonarr_root_folder),
                quality_profile_id=(ov.quality_profile if ov and ov.quality_profile is not None else sonarr_cfg.sonarr_quality_profile),
                tags=(ov.tags if ov and ov.tags is not None else sonarr_cfg.sonarr_tags),
                season_folder=(ov.season_folder if ov and ov.season_folder is not None else default_season_folder),
            )
            return res
        except Exception as e:
            if isinstance(e, HTTPException): raise e
            raise HTTPException(status_code=500, detail=f"Sonarr error: {e}")

    else:
        raise HTTPException(status_code=400, detail="Can only request movies or series")


@router.post("/movie/{tmdb_id}/refresh")
async def refresh_movie_metadata(
    tmdb_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-fetch TMDB metadata for a movie the user has in their library."""
    from core.enrichment import enrich_media

    result = await db.execute(
        select(Media).where(Media.tmdb_id == tmdb_id, Media.media_type == MediaType.movie)
    )
    media = result.scalar_one_or_none()
    if not media:
        raise HTTPException(status_code=404, detail="Movie not found")

    coll_result = await db.execute(
        select(Collection).where(Collection.user_id == current_user.id, Collection.media_id == media.id)
    )
    if not coll_result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Movie not in your library")

    tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)
    # bypass_cache: this is the user explicitly asking for fresh data - see
    # the matching comment on enrich_media's bypass_cache parameter.
    await enrich_media(media, api_key=tmdb_key, bypass_cache=True)

    await media_presentation.refresh_technical_data(db, [media.id], current_user.id)

    await db.commit()
    return {"message": "Metadata refreshed successfully"}


@router.get("/{type}/{tmdb_id}")
async def get_media_details(
    type: MediaType,
    tmdb_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    """Unified endpoint for Movies and Episodes details."""
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID

    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        raise HTTPException(status_code=404, detail="TMDB API Key not configured")
    metadata_lang = await get_user_metadata_language(db, effective_user_id)

    detail_settings_q = await db.execute(select(UserSettings).where(UserSettings.user_id == effective_user_id))
    detail_settings = detail_settings_q.scalar_one_or_none()
    dropped_movie_ids = set(detail_settings.dropped_movies or []) if detail_settings else set()

    try:
        # 1. Fetch from TMDB
        if type == MediaType.movie:
            data = await tmdb.get_movie(tmdb_id, api_key=tmdb_key, language=metadata_lang)
        elif type == MediaType.episode:
            # Look up the episode in the local DB to find its show context
            ep_result = await db.execute(
                select(Media).where(Media.tmdb_id == tmdb_id, Media.media_type == MediaType.episode)
            )
            local_ep = ep_result.scalars().first()

            if local_ep is None or local_ep.show_id is None:
                raise HTTPException(
                    status_code=404,
                    detail="Episode not found — play it via Plex/Jellyfin first so it can be enriched with show context",
                )

            if local_ep.season_number is None or local_ep.episode_number is None:
                raise HTTPException(
                    status_code=404,
                    detail="Episode is missing season/episode numbers — try refreshing metadata from the show page",
                )

            show_result = await db.execute(select(ShowModel).where(ShowModel.id == local_ep.show_id))
            show = show_result.scalar_one_or_none()
            if show is None:
                raise HTTPException(status_code=404, detail="Show not found for this episode")

            ep_data = await tmdb.get_episode(
                show.tmdb_id, local_ep.season_number, local_ep.episode_number,
                api_key=tmdb_key, language=metadata_lang,
            )
            ep_state: dict = {"tmdb_id": tmdb_id, "type": "episode"}
            await media_presentation.enrich_with_state(db, effective_user_id, [ep_state])
            # Store translation for library browsing
            if metadata_lang:
                await upsert_media_translation(
                    db, local_ep.id, metadata_lang,
                    ep_data.get("name") or local_ep.title,
                    ep_data.get("overview"),
                    None,
                    ep_data.get("still_path"),
                )
                await db.commit()
            # Check local info for library tags
            library_info = None
            playable = False
            if local_ep:
                coll_q = (
                    select(CollectionFile)
                    .join(Collection, Collection.id == CollectionFile.collection_id)
                    .where(Collection.media_id == local_ep.id, Collection.user_id == effective_user_id)
                    # Rows from sources with no file details (Nuvio, Stremio) carry no
                    # quality at all, so keep them behind real files or the badge blanks out.
                    .order_by(CollectionFile.resolution.is_(None).asc(), CollectionFile.added_at.desc())
                )
                coll_res = await db.execute(coll_q)
                coll_files = coll_res.scalars().all()
                playable = any(
                    coll_file.source in _PLAYABLE_COLLECTION_SOURCES
                    and coll_file.connection_id is not None
                    and coll_file.source_id is not None
                    for coll_file in coll_files
                )
                coll_file = coll_files[0] if coll_files else None
                if coll_file:
                    library_info = {
                        "resolution": coll_file.resolution,
                        "video_codec": coll_file.video_codec,
                        "audio_codec": coll_file.audio_codec,
                        "audio_channels": coll_file.audio_channels,
                        "audio_languages": coll_file.audio_languages,
                        "subtitle_languages": coll_file.subtitle_languages,
                    }

            return {
                "id": local_ep.id,
                "tmdb_id": tmdb_id,
                "type": "episode",
                "title": ep_data.get("name") or local_ep.title,
                "overview": ep_data.get("overview") or local_ep.overview,
                "poster_path": tmdb.poster_url(ep_data.get("still_path"), size="w780"),
                "backdrop_path": show.backdrop_path,
                "release_date": ep_data.get("air_date"),
                "tmdb_rating": ep_data.get("vote_average"),
                "runtime": ep_data.get("runtime"),
                "season_number": local_ep.season_number,
                "episode_number": local_ep.episode_number,
                "show_title": show.title,
                "show_tmdb_id": show.tmdb_id,
                "show_poster_path": show.poster_path,
                "show_backdrop_path": show.backdrop_path,
                "directors": [
                    {"tmdb_id": c.get("id"), "name": c.get("name")}
                    for c in (ep_data.get("credits") or {}).get("crew", [])
                    if c.get("job") == "Director"
                ],
                "cast": [
                    {
                        "tmdb_id": c.get("id"),
                        "name": c.get("name"),
                        "character": c.get("character"),
                        "profile_path": tmdb.poster_url(c.get("profile_path"), size="w185"),
                    }
                    for c in (ep_data.get("credits") or {}).get("cast", [])[:12]
                ],
                "genres": (show.tmdb_data or {}).get("genres", []),
                "in_library": ep_state.get("in_library", False),
                "playable": playable,
                "watched": ep_state.get("watched", False),
                "in_lists": ep_state.get("in_lists", []),
                "user_rating": ep_state.get("user_rating"),
                "play_count": ep_state.get("play_count", 0),
                "library": library_info,
            }
        else:
            raise HTTPException(
                status_code=400, detail="Use /shows/{tmdb_id} for series"
            )

        # 2. Check local info — aggregate across ALL Media rows for this tmdb_id so
        # that a manually-matched movie whose CollectionFile lives on a different row
        # (e.g. Emby stub vs Trakt row) is still surfaced correctly.
        query = select(Media).where(Media.tmdb_id == tmdb_id, Media.media_type == type)
        result = await db.execute(query)
        all_media = result.scalars().all()
        media = all_media[0] if all_media else None
        all_media_ids = [m.id for m in all_media]

        local_info = {"in_library": False, "playable": False, "library": None, "id": None}
        if all_media:
            local_info["in_library"] = True
            local_info["id"] = media.id
            if data.get("adult", False):
                needs_commit = False
                for m in all_media:
                    if not m.adult:
                        m.adult = True
                        needs_commit = True
                if needs_commit:
                    await db.commit()
            coll_q = (
                select(CollectionFile)
                .join(Collection, Collection.id == CollectionFile.collection_id)
                .where(Collection.media_id.in_(all_media_ids), Collection.user_id == effective_user_id)
                # Rows from sources with no file details (Nuvio, Stremio) carry no
                # quality at all, so keep them behind real files or the badge blanks out.
                .order_by(CollectionFile.resolution.is_(None).asc(), CollectionFile.added_at.desc())
            )
            coll_res = await db.execute(coll_q)
            coll_files = coll_res.scalars().all()
            local_info["playable"] = any(
                coll_file.source in _PLAYABLE_COLLECTION_SOURCES
                and coll_file.connection_id is not None
                and coll_file.source_id is not None
                for coll_file in coll_files
            )
            coll_file = coll_files[0] if coll_files else None
            if coll_file:
                local_info["library"] = {
                    "resolution": coll_file.resolution,
                    "video_codec": coll_file.video_codec,
                    "audio_codec": coll_file.audio_codec,
                    "audio_channels": coll_file.audio_channels,
                    "audio_languages": coll_file.audio_languages,
                    "subtitle_languages": coll_file.subtitle_languages,
                }

        # Store translation for library browsing
        if metadata_lang and local_info.get("id"):
            await upsert_media_translation(
                db, local_info["id"], metadata_lang,
                data.get("title") or data.get("name"),
                data.get("overview"),
                data.get("tagline"),
                data.get("poster_path"),
            )
            await db.commit()

        # 3. Format Merged Response
        production_companies = [
            {
                "id": c["id"],
                "name": c["name"],
                "logo_path": tmdb.poster_url(c.get("logo_path"), size="w500")
                if c.get("logo_path")
                else None,
                "origin_country": c.get("origin_country"),
            }
            for c in data.get("production_companies", [])
        ]

        state_item: dict = {"tmdb_id": tmdb_id, "type": type.value}
        raw_coll = data.get("belongs_to_collection") if type == MediaType.movie else None

        # DB operations must run sequentially — async session doesn't support concurrent use
        await media_presentation.enrich_with_state(db, effective_user_id, [state_item])
        where_to_watch = await media_presentation.get_where_to_watch(db, effective_user_id, tmdb_id, MediaType.movie, media=media, tmdb_key=tmdb_key)
        coll_data = await tmdb.get_collection(raw_coll["id"], api_key=tmdb_key) if raw_coll else None

        collection = None
        if coll_data:
            collection = {
                "id": coll_data.get("id"),
                "name": coll_data.get("name"),
                "poster_path": tmdb.poster_url(coll_data.get("poster_path")),
                "backdrop_path": tmdb.poster_url(
                    coll_data.get("backdrop_path"), size="original"
                ),
                "parts": [
                    {
                        "tmdb_id": p.get("id"),
                        "title": p.get("title"),
                        "type": MediaType.movie,
                        "poster_path": tmdb.poster_url(p.get("poster_path")),
                        "release_date": p.get("release_date"),
                        "overview": p.get("overview"),
                        "adult": p.get("adult", False),
                    }
                    for p in coll_data.get("parts", [])
                ],
            }

        if collection and collection.get("parts"):
            await media_presentation.enrich_with_state(db, effective_user_id, collection["parts"])

        has_mid_credits_scene, has_post_credits_scene = tmdb.extract_credits_stingers(data)

        return {
            **local_info,
            "tmdb_id": tmdb_id,
            "type": type,
            "dropped": local_info["id"] in dropped_movie_ids if local_info["id"] else False,
            "watched": state_item.get("watched", False),
            "in_lists": state_item.get("in_lists", []),
            "user_rating": state_item.get("user_rating"),
            "play_count": state_item.get("play_count", 0),
            "in_library": state_item.get("in_library", local_info["in_library"]),
            "collection_pct": state_item.get("collection_pct", 100 if local_info["in_library"] else 0),
            "is_monitored": state_item.get("is_monitored", False),
            "request_enabled": state_item.get("request_enabled", False),
            "request_status": state_item.get("request_status"),
            "title": data.get("title") or data.get("name"),
            "original_title": data.get("original_title") or data.get("original_name"),
            "overview": data.get("overview") or (media.overview if media else None),
            "poster_path": tmdb.poster_url(data.get("poster_path")),
            "backdrop_path": tmdb.poster_url(
                data.get("backdrop_path"), size="original"
            ),
            "release_date": data.get("release_date") or data.get("first_air_date"),
            "tmdb_rating": data.get("vote_average"),
            "tagline": data.get("tagline"),
            "runtime": data.get("runtime"),
            "status": data.get("status"),
            "genres": [g["name"] for g in data.get("genres", [])],
            "original_language": data.get("original_language"),
            "age_rating": media_presentation._extract_movie_certification(data),
            "release_dates": media_presentation._extract_movie_release_dates(data),
            "imdb_id": data.get("imdb_id"),
            "adult": data.get("adult", False),
            "has_mid_credits_scene": has_mid_credits_scene,
            "has_post_credits_scene": has_post_credits_scene,
            "collection": collection,
            "production_companies": production_companies,
            "directors": [
                {"tmdb_id": c["id"], "name": c["name"]}
                for c in data.get("credits", {}).get("crew", [])
                if c.get("job") == "Director"
            ],
            "cast": [
                {
                    "tmdb_id": c["id"],
                    "name": c["name"],
                    "character": c.get("character", ""),
                    "profile_path": tmdb.poster_url(c.get("profile_path")),
                }
                for c in data.get("credits", {}).get("cast", [])[:12]
            ],
            "where_to_watch": where_to_watch,
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=404, detail=f"TMDB Media not found: {e}")


@router.get("/{type}/{tmdb_id}/recommendations")
async def get_media_recommendations(
    type: MediaType,
    tmdb_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    """Fetch movie/series recommendations from TMDB and enrich with state."""
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID

    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}

    try:
        if type == MediaType.movie:
            data = await tmdb.get_movie(tmdb_id, api_key=tmdb_key)
        else:
            data = await tmdb.get_show(tmdb_id, api_key=tmdb_key)

        recs_raw = data.get("recommendations", {}).get("results", [])[:12]

        # Dropped items must never come back as a recommendation (#117).
        dropped_movie_ids, dropped_show_ids = await media_presentation._dropped_tmdb_ids(db, effective_user_id)
        dropped_rec_tmdb_ids = dropped_movie_ids if type == MediaType.movie else dropped_show_ids
        if dropped_rec_tmdb_ids:
            recs_raw = [r for r in recs_raw if r.get("id") not in dropped_rec_tmdb_ids]

        recommendations = [
            {
                "id": None,
                "tmdb_id": r["id"],
                "type": type.value,
                "title": r.get("title") or r.get("name"),
                "original_title": r.get("original_title") or r.get("original_name"),
                "overview": r.get("overview"),
                "poster_path": tmdb.poster_url(r.get("poster_path")),
                "backdrop_path": tmdb.poster_url(r.get("backdrop_path"), size="w1280"),
                "release_date": r.get("release_date") or r.get("first_air_date"),
                "tmdb_rating": r.get("vote_average"),
                "adult": r.get("adult", False),
            }
            for r in recs_raw
        ]
        await media_presentation.enrich_with_state(db, effective_user_id, recommendations)
        return {"results": recommendations}
    except Exception:
        return {"results": []}


from fastapi.responses import FileResponse, RedirectResponse
from jose import jwt, JWTError
from core.security import ALGORITHM
from core.config import settings

async def verify_image_token(request: Request, db: AsyncSession = Depends(get_db)) -> int | None:
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = None
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        token = auth.split(" ")[1]
    else:
        token = request.query_params.get("token")
        if not token:
            cookie_str = request.headers.get("Cookie") or ""
            match = re.search(r"(?:^|;\s*)token=([^;]+)", cookie_str)
            if match:
                token = urllib.parse.unquote(match.group(1))

    if not token:
        # Poster/backdrop images carry no per-user data (just a TMDB path), so
        # once the admin opts into anonymous browsing, letting those pages
        # load images without a session is safe too.
        gs = await settings_store.get_global_settings(db)
        if gs and gs.enable_logged_out_navigation:
            return None
        raise credentials_exception

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("type") == "2fa_pending":
            raise credentials_exception
        user_id: int = int(payload.get("sub"))
        return user_id
    except (JWTError, TypeError, ValueError):
        raise credentials_exception


@router.get("/image/{size}/{path:path}")
async def serve_image(
    size: str,
    path: str,
    db: AsyncSession = Depends(get_db),
    _: int = Depends(verify_image_token),
):
    if not path.startswith("/"):
        path = "/" + path

    from core.image_cache import (
        ALLOWED_SIZES,
        TVDB_SIZE,
        upstream_image_url,
        download_and_cache_image,
        prune_cache_bg,
    )
    upstream = upstream_image_url(size, path)

    # Check settings
    settings_stmt = select(GlobalSettings).where(GlobalSettings.id == 1)
    gs = (await db.execute(settings_stmt)).scalar_one_or_none()

    if not gs or not gs.image_cache_enabled:
        return RedirectResponse(upstream)

    if size != TVDB_SIZE and size not in ALLOWED_SIZES:
        raise HTTPException(status_code=400, detail="Invalid image size")
    if ".." in path:
        raise HTTPException(status_code=400, detail="Invalid image path")

    local_path_str = await download_and_cache_image(db, size, path)
    if not local_path_str:
        return RedirectResponse(upstream)

    # Eviction pruning check in background
    bg_tasks = BackgroundTask(prune_cache_bg, limit_gb=gs.image_cache_limit_gb)

    return FileResponse(
        local_path_str,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
        background=bg_tasks,
    )


_RPDB_PROVIDERS = {"tmdb", "tvdb", "imdb"}
_RPDB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,64}$")


@router.get("/rating-poster/{provider}/{rpdb_id}")
async def serve_rating_poster(
    provider: str,
    rpdb_id: str,
    fallback: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user_id: int | None = Depends(verify_image_token),
):
    """RatingPosterDB rating-overlay poster for a movie/show portrait slot
    (#377). The API key stays server-side - Scrob fetches the RPDB image and
    streams it back, exactly like the TMDB proxy above. Any failure (no key,
    RPDB down, non-image response) falls back to the already-proxied TMDB
    poster the caller passed in `fallback`."""
    # `fallback` is always an internal proxied-poster path built by the
    # frontend's tmdbImageUrl(); never an arbitrary URL.
    if not fallback.startswith("/api/proxy/media/image/") or "\n" in fallback:
        raise HTTPException(status_code=400, detail="Invalid fallback")

    def _to_fallback() -> RedirectResponse:
        return RedirectResponse(
            fallback, status_code=302,
            headers={"Cache-Control": "public, max-age=3600"},
        )

    if provider not in _RPDB_PROVIDERS or not _RPDB_ID_RE.match(rpdb_id):
        return _to_fallback()
    if user_id is None:
        return _to_fallback()

    settings_row = (await db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )).scalar_one_or_none()

    from core.rpdb import normalize_api_key
    try:
        key = normalize_api_key(settings_row.rpdb_api_key) if settings_row else None
    except ValueError:
        key = None
    if not key:
        return _to_fallback()

    url = (
        f"https://api.ratingposterdb.com/{urllib.parse.quote(key, safe='')}"
        f"/{provider}/poster-default/{rpdb_id}.jpg?fallback=true"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.HTTPError:
        return _to_fallback()

    content_type = resp.headers.get("content-type", "")
    if resp.status_code != 200 or not content_type.startswith("image/"):
        return _to_fallback()

    return Response(
        content=resp.content,
        media_type=content_type or "image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )

