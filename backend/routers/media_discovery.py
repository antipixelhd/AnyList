"""Discovery feeds, recommendations, and external catalog browsing."""

import asyncio
import logging
import time as _time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from core import calendar_service, media_presentation, settings_store, tmdb
from core.config import settings
from core.limiter import limiter
from core.translations import (
    apply_translations,
    get_media_translations,
    get_user_metadata_language,
)
from db import get_db
from dependencies import (
    ANON_USER_ID,
    get_current_user_or_api_key,
    get_optional_user_or_api_key,
)
from models.base import MediaType
from models.calendar_cache import UserCalendarCache
from models.collection import Collection, CollectionFile
from models.connections import MediaServerConnection
from models.events import WatchEvent
from models.lists import List as UserList
from models.lists import ListItem
from models.media import Media
from models.profile import UserProfileData
from models.show import Show as ShowModel
from models.users import User, UserSettings

logger = logging.getLogger(__name__)
router = APIRouter()

_FOR_YOU_CACHE: dict[int, tuple[float, dict]] = {}


_FOR_YOU_TTL = 900  # 15 minutes


MOVIE_GENRE_IDS: dict[str, int] = {
    "Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35,
    "Crime": 80, "Documentary": 99, "Drama": 18, "Family": 10751,
    "Fantasy": 14, "History": 36, "Horror": 27, "Music": 10402,
    "Mystery": 9648, "Romance": 10749, "Science Fiction": 878,
    "Thriller": 53, "War": 10752, "Western": 37,
}


TV_GENRE_IDS: dict[str, int] = {
    "Action & Adventure": 10759, "Animation": 16, "Comedy": 35,
    "Crime": 80, "Documentary": 99, "Drama": 18, "Family": 10751,
    "Kids": 10762, "Mystery": 9648, "News": 10763, "Reality": 10764,
    "Sci-Fi & Fantasy": 10765, "Soap": 10766, "Talk": 10767,
    "War & Politics": 10768, "Western": 37,
}


MOVIE_GENRE_NAMES: dict[int, str] = {v: k for k, v in MOVIE_GENRE_IDS.items()}


TV_GENRE_NAMES: dict[int, str] = {v: k for k, v in TV_GENRE_IDS.items()}


def _filter_disliked(
    results: list[dict],
    disliked: set[str],
    liked: set[str],
    name_map: dict[int, str],
) -> list[dict]:
    """Drop items whose only genres are disliked and none are liked."""
    if not disliked:
        return results
    out = []
    for r in results:
        gids = r.get("genre_ids", [])
        names = {name_map.get(gid) for gid in gids} - {None}
        has_liked = bool(names & liked)
        has_only_disliked = bool(names) and names <= disliked
        if not has_only_disliked or has_liked:
            out.append(r)
    return out


TV_STATUS_IDS: dict[str, int] = {
    "Returning Series": 0, "Planned": 1, "In Production": 2,
    "Ended": 3, "Canceled": 4,
}


async def _sync_trending(
    type: MediaType,
    page: int = 1,
    api_key: str | None = None,
):
    """Fetch trending data from TMDB."""
    if not settings_store.check_tmdb_key(api_key):
        return {"results": [], "page": 1, "total_pages": 1, "total_results": 0}

    try:
        if type == MediaType.movie:
            data = await tmdb.get_trending_movies(page=page, api_key=api_key)
        else:
            data = await tmdb.get_trending_shows(page=page, api_key=api_key)
        return data
    except Exception as e:
        print(f"Error fetching trending from TMDB: {e}")
        return {"results": [], "page": 1, "total_pages": 1, "total_results": 0}


@router.get("/trending/movies")
async def trending_movies(
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID

    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    data = await _sync_trending(MediaType.movie, page, api_key=tmdb_key)
    tmdb_results = data.get("results", [])

    if not tmdb_results:
        return {"page": page, "total_pages": 1, "total_results": 0, "results": []}

    tmdb_ids = [res["id"] for res in tmdb_results]
    query = (
        select(Media)
        .options(joinedload(Media.show))
        .where(Media.tmdb_id.in_(tmdb_ids), Media.media_type == MediaType.movie)
    )
    result = await db.execute(query)
    local_map = {m.tmdb_id: m for m in result.scalars().all()}

    enriched = []
    for res in tmdb_results:
        tmdb_id = res["id"]
        if tmdb_id in local_map:
            enriched.append({**media_presentation.format_media(local_map[tmdb_id]), "in_library": True})
        else:
            enriched.append(
                {
                    "id": None,
                    "tmdb_id": tmdb_id,
                    "type": MediaType.movie,
                    "title": res.get("title"),
                    "poster_path": tmdb.poster_url(res.get("poster_path")),
                    "release_date": res.get("release_date"),
                    "tmdb_rating": res.get("vote_average"),
                    "in_library": False,
                    "adult": res.get("adult", False),
                }
            )
    await media_presentation.enrich_with_state(db, effective_user_id, enriched)
    return {
        "page": data.get("page", 1),
        "total_pages": data.get("total_pages", 1),
        "total_results": data.get("total_results", 0),
        "results": enriched,
    }


@router.get("/trending/shows")
async def trending_shows(
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID

    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    data = await _sync_trending(MediaType.series, page, api_key=tmdb_key)
    tmdb_results = data.get("results", [])

    if not tmdb_results:
        return {"page": page, "total_pages": 1, "total_results": 0, "results": []}

    tmdb_ids = [res["id"] for res in tmdb_results]

    # Collect which of these show TMDB IDs the user has in their library
    collected_q = await db.execute(
        select(ShowModel.tmdb_id)
        .join(Media, Media.show_id == ShowModel.id)
        .join(Collection, Collection.media_id == Media.id)
        .where(Collection.user_id == effective_user_id, ShowModel.tmdb_id.in_(tmdb_ids))
        .distinct()
    )
    in_library: set[int] = {row[0] for row in collected_q.all()}

    enriched = []
    for res in tmdb_results:
        tmdb_id = res["id"]
        enriched.append(
            {
                "id": None,
                "tmdb_id": tmdb_id,
                "type": MediaType.series,
                "title": res.get("name"),
                "poster_path": tmdb.poster_url(res.get("poster_path")),
                "backdrop_path": tmdb.poster_url(res.get("backdrop_path"), size="w1280"),
                "release_date": res.get("first_air_date"),
                "tmdb_rating": res.get("vote_average"),
                "in_library": tmdb_id in in_library,
                "adult": res.get("adult", False),
            }
        )
    await media_presentation.enrich_with_state(db, effective_user_id, enriched)
    return {
        "page": data.get("page", 1),
        "total_pages": data.get("total_pages", 1),
        "total_results": data.get("total_results", 0),
        "results": enriched,
    }


_poster_wall_cache: dict = {"data": None, "ts": 0.0}


_POSTER_WALL_TTL = 3600


_FALLBACK_POSTERS: list[dict] = [
    {"poster_path": "/iPOn6DinuVyLY17YM9mKuPofV08.jpg", "title": "Spider-Man: Brand New Day", "media_type": "movie"},
    {"poster_path": "/4LwvU9SZc8QQzW1X1FAPhNbXnEU.jpg", "title": "Minions & Monsters", "media_type": "movie"},
    {"poster_path": "/5rhTDKUhPYvpdQIijFIs5VoWsON.jpg", "title": "The Odyssey", "media_type": "movie"},
    {"poster_path": "/sfQtVlIHljToOwYjhe21KPGzZWK.jpg", "title": "Toy Story 5", "media_type": "movie"},
    {"poster_path": "/fYXqpgPmHMphSF2W30GbTeJVIa5.jpg", "title": "The End of Oak Street", "media_type": "movie"},
    {"poster_path": "/b7Dr8Chzse8VagexAporUu2RtLx.jpg", "title": "The Invite", "media_type": "movie"},
    {"poster_path": "/bRwnj8WEKBCvmfeUNOukJPwB43K.jpg", "title": "Obsession", "media_type": "movie"},
    {"poster_path": "/6JU7E8Vv2M11egkctWVOScxWR75.jpg", "title": "The Last House", "media_type": "movie"},
    {"poster_path": "/jzPwsojjFStf5lR5Nm07w2hH56G.jpg", "title": "Avengers: Doomsday", "media_type": "movie"},
    {"poster_path": "/dgTKahWonzVLeN8Lm22WR2S7D0A.jpg", "title": "Don't Say Good Luck", "media_type": "movie"},
    {"poster_path": "/rS7byWK9cfPfdLeFNlRIaJxH9mN.jpg", "title": "Camp Rock 3", "media_type": "movie"},
    {"poster_path": "/rhGx6E3qRNMgj3i5su2oukNHwIQ.jpg", "title": "Backrooms", "media_type": "movie"},
    {"poster_path": "/1QCWdqzTfh2x9UylVpspIU6QTuM.jpg", "title": "Supergirl", "media_type": "movie"},
    {"poster_path": "/AnJ8IQJI23hNpYXVNaythu061Ru.jpg", "title": "Disclosure Day", "media_type": "movie"},
    {"poster_path": "/6CdoTKnRQHJkjRGxTefFGkPQplB.jpg", "title": "Young Washington", "media_type": "movie"},
    {"poster_path": "/yihdXomYb5kTeSivtFndMy5iDmf.jpg", "title": "Project Hail Mary", "media_type": "movie"},
    {"poster_path": "/4tTrW9dXCByS5wt2pXVWb58zNjz.jpg", "title": "Insidious: Out of the Further", "media_type": "movie"},
    {"poster_path": "/zP19YO60jwEsfKd5Qf1UvA5uJu8.jpg", "title": "The Furious", "media_type": "movie"},
    {"poster_path": "/uRxrNXQWkHoENm3nwVOZDYSCx2F.jpg", "title": "Evil Dead Burn", "media_type": "movie"},
    {"poster_path": "/3sgnSfNT27Bx5O5ukr7B26mhEQq.jpg", "title": "Avatar Aang: The Last Airbender", "media_type": "movie"},
    {"poster_path": "/gpC7h43xPMEV3goYMQShfJbTtLq.jpg", "title": "Lanterns", "media_type": "series"},
    {"poster_path": "/f1VCQIG2iCyOookdgOzwtUpwWC0.jpg", "title": "Reacher", "media_type": "series"},
    {"poster_path": "/7V0Ebks0GgpKvQ7QbLAIdX5dos4.jpg", "title": "House of the Dragon", "media_type": "series"},
    {"poster_path": "/dB4EDhre2dsC2kxYDavyKWqLQwi.jpg", "title": "One Piece", "media_type": "series"},
    {"poster_path": "/gMYZZvnkVNTqSVnVCphWbPXwWwb.jpg", "title": "Silo", "media_type": "series"},
    {"poster_path": "/rzpHPSEgPTpRs8EHbygwsOw7jC0.jpg", "title": "Lioness", "media_type": "series"},
    {"poster_path": "/xsrkiXg8EuNNtbPtbmvCxg95gK7.jpg", "title": "Lucky", "media_type": "series"},
    {"poster_path": "/uRHsiw1wLxPHFXkkv4Ix1s0O6f4.jpg", "title": "Ted Lasso", "media_type": "series"},
    {"poster_path": "/2HKBc5UiFw8JrruHq8S1Y7TnlW0.jpg", "title": "X-Men '97", "media_type": "series"},
    {"poster_path": "/2EewmxXe72ogD0EaWM8gqa0ccIw.jpg", "title": "Bleach", "media_type": "series"},
    {"poster_path": "/7yUY1HUyQuybbvkAAhLzQ7x1l9g.jpg", "title": "A Shop for Killers", "media_type": "series"},
    {"poster_path": "/eM8bbTn8C8vUwwS6upzzm7gX31u.jpg", "title": "Futurama", "media_type": "series"},
    {"poster_path": "/43iXOUo8dw7KfllwOnuu8JSyqFt.jpg", "title": "Forging Justice", "media_type": "series"},
    {"poster_path": "/oHqYrPAsIiTD5m4DuxumV4er8BU.jpg", "title": "Re:ZERO -Starting Life in Another World-", "media_type": "series"},
    {"poster_path": "/fhpa8B6USatHybK04XBbPAB6Mlz.jpg", "title": "My Brilliant Career", "media_type": "series"},
    {"poster_path": "/Q9u5ZSrthOuwkULV41VUqE8vRV.jpg", "title": "Mystic Nine", "media_type": "series"},
    {"poster_path": "/cThLWEGs6BEqY0QZMbU4FAeWwPT.jpg", "title": "Sterling Point", "media_type": "series"},
    {"poster_path": "/in1R2dDc421JxsoRWaIIAqVI2KE.jpg", "title": "The Boys", "media_type": "series"},
    {"poster_path": "/WGyAyBPncfuu8MZhLY9RtfZPM0.jpg", "title": "VisionQuest", "media_type": "series"},
    {"poster_path": "/wP0GdqwVu2g1y3q1KzBXuSrdTvX.jpg", "title": "The Shards", "media_type": "series"},
]


@router.get("/public/poster-wall")
@limiter.limit("20/minute")
async def public_poster_wall(request: Request, db: AsyncSession = Depends(get_db)):
    """Decorative trending posters for the logged-out landing page.

    Unauthenticated by design - only ever returns bare TMDB poster paths and
    titles, never library or user data, so it's safe regardless of the
    instance's public-profile setting.
    """
    now = _time.monotonic()
    if _poster_wall_cache["data"] is not None and now - _poster_wall_cache["ts"] < _POSTER_WALL_TTL:
        return _poster_wall_cache["data"]

    gs = await settings_store.get_global_settings(db)
    api_key = gs.tmdb_api_key if gs else None
    if not settings_store.check_tmdb_key(api_key):
        return {"posters": _FALLBACK_POSTERS}

    posters: list[dict] = []
    try:
        movies, shows = await asyncio.gather(
            tmdb.get_trending_movies(time_window="week", api_key=api_key),
            tmdb.get_trending_shows(time_window="week", api_key=api_key),
        )
        for res in movies.get("results", []):
            if res.get("poster_path") and not res.get("adult"):
                posters.append({"poster_path": res["poster_path"], "title": res.get("title"), "media_type": "movie"})
        for res in shows.get("results", []):
            if res.get("poster_path") and not res.get("adult"):
                posters.append({"poster_path": res["poster_path"], "title": res.get("name"), "media_type": "series"})
    except Exception as e:
        print(f"Error fetching poster wall from TMDB: {e}")
        return {"posters": _FALLBACK_POSTERS}

    if not posters:
        posters = _FALLBACK_POSTERS

    data = {"posters": posters}
    _poster_wall_cache["data"] = data
    _poster_wall_cache["ts"] = now
    return data


@router.get("/on-air-today")
async def on_air_today(
    page: int = Query(default=1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID

    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": [], "page": 1, "total_pages": 1, "total_results": 0}
    # No browser timezone to go on here (plain SSR fetch, unlike the homepage
    # widget) - use the server's configured TZ instead of defaulting to UTC.
    data = await tmdb.get_on_air_today(page=page, api_key=tmdb_key, timezone=settings.tz)
    results = [
        {
            "id": None,
            "tmdb_id": s.get("id"),
            "type": "series",
            "title": s.get("name"),
            "poster_path": tmdb.poster_url(s.get("poster_path")),
            "backdrop_path": tmdb.poster_url(s.get("backdrop_path"), size="w780"),
            "tmdb_rating": s.get("vote_average"),
            "release_date": s.get("first_air_date"),
        }
        for s in data.get("results", [])
    ]
    await media_presentation.enrich_with_state(db, effective_user_id, results)
    return {
        "results": results,
        "page": data.get("page", page),
        "total_pages": data.get("total_pages", 1),
        "total_results": data.get("total_results", 0),
    }


@router.get("/airing-today/collected")
async def airing_today_collected(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    """Return today's episodes for the user's collected/watched shows.

    Built from the same per-user calendar computation as /calendar (#194,
    #243) rather than TMDB's own /tv/airing_today list, which is a global
    "popular shows airing around now" feed - a collected show not in that
    feed's first 20 pages (or not TMDB-popular at all) used to be silently
    dropped here even though its actual episode aired today. Reusing the
    calendar's cache also guarantees this widget can't drift from the
    calendar's own Today row (#288).
    """
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID

    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}


    # First load after local midnight would otherwise block this widget on a
    # full calendar recompute (two TMDB calls per still-running show).
    # Yesterday's cached payload already has a 14-day forward window that
    # covers today, so serve it immediately and recompute in the background -
    # the next load picks up the fresh row. Fall back to a blocking compute
    # only when there is no usable cache at all (new user, schema bump, or
    # >48h stale). #194
    row = (
        await db.execute(
            select(UserCalendarCache).where(UserCalendarCache.user_id == effective_user_id)
        )
    ).scalars().first()
    fresh = calendar_service._is_cache_fresh(row)
    if fresh or calendar_service._is_cache_usable(row):
        calendar = row.payload
        if not fresh:
            asyncio.create_task(calendar_service._background_compute(effective_user_id))
    else:
        calendar = (await calendar_service._load_or_compute(db, effective_user_id, force=False))["calendar"]
    # The real current date, not calendar["today"] - that field is stamped at
    # cache-compute time, and the payload served here can be up to ~48h stale,
    # so filtering entries by the live date is what keeps the widget from
    # showing yesterday's row as "today".
    today = calendar_service._server_today().isoformat()

    results = [
        {
            "id": None,
            "tmdb_id": e["show_tmdb_id"],
            "type": "episode",
            "title": e.get("episode_name") or e.get("show_title"),
            "show_title": e.get("show_title"),
            "show_tmdb_id": e.get("show_tmdb_id"),
            "season_number": e.get("season_number"),
            "episode_number": e.get("episode_number"),
            "poster_path": e.get("poster_path"),
            "backdrop_path": e.get("poster_path"),
            "release_date": e.get("air_date"),
        }
        for e in calendar.get("entries", [])
        if e.get("air_date") == today
    ]
    await media_presentation.enrich_with_state(db, effective_user_id, results)
    return {"results": results}


def recently_added_order(max_added):
    """Ordering for the Recently Added rail.

    The add-date leads, but media servers stamp a batch of files with the same
    second, so without the rest of these keys Postgres is free to return a
    different arrangement every time and a season comes back shuffled. Grouping
    on show_id keeps one show's episodes together inside a shared timestamp,
    season and episode descending put the newest one first, and the id makes
    the result fully deterministic. Movies have no season/episode, hence
    nulls_last: a DESC sort in Postgres puts NULLs first otherwise."""
    return [
        max_added.desc(),
        Media.show_id.desc().nulls_last(),
        Media.season_number.desc().nulls_last(),
        Media.episode_number.desc().nulls_last(),
        Media.id.desc(),
    ]


@router.get("/recently-added")
async def recently_added(
    type: MediaType | None = Query(None),
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    # Subquery: latest added_at per media for this user (deduplicates movies in both Plex+Jellyfin)
    coll_subq = (
        select(Collection.media_id, func.max(Collection.added_at).label("max_added"))
        .where(Collection.user_id == current_user.id)
        .group_by(Collection.media_id)
        .subquery()
    )
    media_filters = [
        # Exclude episodes missing season/episode numbers — they are unidentifiable
        # orphans (created by webhook before the show was synced) and cannot be displayed.
        or_(
            Media.media_type != MediaType.episode,
            and_(Media.season_number.isnot(None), Media.episode_number.isnot(None)),
        )
    ]
    if type:
        media_filters.append(Media.media_type == type)
    settings_q = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    user_settings = settings_q.scalar_one_or_none()
    if user_settings and user_settings.hide_watched_from_recently_added:
        watched_exists = (
            select(WatchEvent.id)
            .where(WatchEvent.media_id == Media.id, WatchEvent.user_id == current_user.id, WatchEvent.completed == True)
            .exists()
        )
        media_filters.append(~watched_exists)
    query = (
        select(Media)
        .join(coll_subq, coll_subq.c.media_id == Media.id)
        .options(joinedload(Media.show))
        .where(*media_filters)
        .order_by(*recently_added_order(coll_subq.c.max_added))
        .limit(limit)
    )
    result = await db.execute(query)
    items = [media_presentation.format_media(m) for m in result.scalars().all()]
    await media_presentation.enrich_with_state(db, current_user.id, items)
    lang = await get_user_metadata_language(db, current_user.id)
    if lang:
        media_ids = [i["id"] for i in items if i.get("id")]
        translations = await get_media_translations(db, media_ids, lang)
        apply_translations(items, translations)
    return {"results": items}


_PERSON_PAGE_SIZE = 20


@router.get("/person/{person_id}")
async def get_person_details(
    person_id: int,
    page: int = Query(1, ge=1),
    collection: str | None = Query(None),  # "in" | "out" | None (no filter)
    genre: list[str] = Query(default=[]),  # OR'd together — any selected genre matches
    year: list[int] = Query(default=[]),  # OR'd together — any selected year matches
    min_rating: str | None = Query(None),  # "9".."5" (N+ stars) or "lt5" (under 5 stars)
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID

    try:
        tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
        if not settings_store.check_tmdb_key(tmdb_key):
            raise HTTPException(status_code=404, detail="TMDB API Key not configured")
        data = await tmdb.get_person(person_id, api_key=tmdb_key)
        credits = data.get("combined_credits", {})
        formatted_credits = []
        for c in credits.get("cast", []):
            m_type = "movie" if c.get("media_type") == "movie" else "series"
            popularity = c.get("popularity", 0)
            if c.get("media_type") == "tv":
                episode_count = c.get("episode_count") or 0
                role_weight = min(episode_count, 20) / 20.0
            else:
                order = c.get("order") or 0
                role_weight = max(0.05, 1.0 - order * 0.05)
            formatted_credits.append({
                "tmdb_id": c.get("id"),
                "type": m_type,
                "title": c.get("title") or c.get("name"),
                "poster_path": tmdb.poster_url(c.get("poster_path")),
                "release_date": c.get("release_date") or c.get("first_air_date"),
                "character": c.get("character"),
                "popularity": popularity,
                "adult": c.get("adult", False),
                "genre_ids": c.get("genre_ids", []),
                "vote_average": c.get("vote_average") or 0,
                "_score": popularity * max(role_weight, 0.05),
            })
        _crew_dept_weight = {"Directing": 1.0, "Writing": 0.9, "Production": 0.7, "Creator": 1.0}
        for c in credits.get("crew", []):
            m_type = "movie" if c.get("media_type") == "movie" else "series"
            popularity = c.get("popularity", 0)
            role_weight = _crew_dept_weight.get(c.get("department", ""), 0.5)
            formatted_credits.append({
                "tmdb_id": c.get("id"),
                "type": m_type,
                "title": c.get("title") or c.get("name"),
                "poster_path": tmdb.poster_url(c.get("poster_path")),
                "release_date": c.get("release_date") or c.get("first_air_date"),
                "character": c.get("job"),
                "popularity": popularity,
                "adult": c.get("adult", False),
                "genre_ids": c.get("genre_ids", []),
                "vote_average": c.get("vote_average") or 0,
                "_score": popularity * role_weight,
            })
        # Deduplicate by tmdb_id — a person may appear in multiple episodes of the
        # same show; keep the entry with the highest score.
        seen: dict[int, int] = {}  # tmdb_id -> index in formatted_credits
        deduped: list[dict] = []
        for credit in formatted_credits:
            tid = credit["tmdb_id"]
            if tid in seen:
                if credit["_score"] > deduped[seen[tid]]["_score"]:
                    deduped[seen[tid]] = credit
            else:
                seen[tid] = len(deduped)
                deduped.append(credit)
        deduped.sort(key=lambda x: x["_score"], reverse=True)
        for credit in deduped:
            del credit["_score"]

        # Cheap in-memory filters first (no DB work) to shrink the list before
        # the collection cross-reference below, which does need a DB round-trip.
        # Selections within a filter are OR'd (any selected genre/year matches);
        # the different filters are AND'd together.
        if genre:
            def _matches_any_genre(credit: dict) -> bool:
                genre_map = MOVIE_GENRE_IDS if credit["type"] == "movie" else TV_GENRE_IDS
                target_ids = {genre_map[g] for g in genre if g in genre_map}
                return bool(target_ids & set(credit.get("genre_ids", [])))
            deduped = [c for c in deduped if _matches_any_genre(c)]
        if year:
            year_strs = {str(y) for y in year}
            deduped = [c for c in deduped if (c.get("release_date") or "")[:4] in year_strs]
        if min_rating == "lt5":
            deduped = [c for c in deduped if c.get("vote_average", 0) < 5]
        elif min_rating:
            try:
                threshold = float(min_rating)
            except ValueError:
                threshold = None
            if threshold is not None:
                deduped = [c for c in deduped if c.get("vote_average", 0) >= threshold]

        for credit in deduped:
            credit.pop("genre_ids", None)
            credit.pop("vote_average", None)

        if collection in ("in", "out"):
            # Filter before paginating so total_credits/page counts reflect the
            # filtered set, not the full filmography (enrich_with_state is a
            # handful of batched queries regardless of list size, same as any
            # other listing endpoint here).
            await media_presentation.enrich_with_state(db, effective_user_id, deduped)
            wants_in_library = collection == "in"
            deduped = [c for c in deduped if bool(c.get("in_library")) == wants_in_library]

        total_credits = len(deduped)
        start = (page - 1) * _PERSON_PAGE_SIZE
        top_credits = deduped[start:start + _PERSON_PAGE_SIZE]
        if collection not in ("in", "out"):
            await media_presentation.enrich_with_state(db, effective_user_id, top_credits)

        # Which of the user's lists contain this person?
        user_list_ids_q = await db.execute(select(UserList.id).where(UserList.user_id == effective_user_id))
        user_list_ids = [r[0] for r in user_list_ids_q.all()]
        person_in_lists: list[int] = []
        if user_list_ids:
            li_q = await db.execute(
                select(ListItem.list_id)
                .join(Media, Media.id == ListItem.media_id)
                .where(
                    ListItem.list_id.in_(user_list_ids),
                    Media.tmdb_id == person_id,
                    Media.media_type == MediaType.person,
                )
            )
            person_in_lists = [r[0] for r in li_q.all()]

        return {
            "tmdb_id": data.get("id"),
            "name": data.get("name"),
            "biography": data.get("biography"),
            "profile_path": tmdb.poster_url(data.get("profile_path"), size="h632"),
            "birthday": data.get("birthday"),
            "place_of_birth": data.get("place_of_birth"),
            "known_for_department": data.get("known_for_department"),
            "credits": top_credits,
            "total_credits": total_credits,
            "page": page,
            "page_size": _PERSON_PAGE_SIZE,
            "collection": collection,
            "in_lists": person_in_lists,
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=404, detail=f"Person not found: {e}")


@router.get("/collection/{collection_id}")
async def get_collection_details(
    collection_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    try:
        tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)
        if not settings_store.check_tmdb_key(tmdb_key):
            raise HTTPException(status_code=404, detail="TMDB API Key not configured")

        data, genre_data = await asyncio.gather(
            tmdb.get_collection(collection_id, api_key=tmdb_key),
            tmdb.get_genre_list(api_key=tmdb_key),
        )

        genre_map = {g["id"]: g["name"] for g in genre_data.get("genres", [])}
        parts_data = sorted(data.get("parts", []), key=lambda x: x.get("release_date") or "")

        # Fetch credits for all parts in parallel (cap at 15 to avoid long waits)
        credit_results = await asyncio.gather(
            *[tmdb.get_movie_credits(p["id"], api_key=tmdb_key) for p in parts_data[:15]],
            return_exceptions=True,
        )

        # Aggregate unique genres from all parts
        all_genre_ids: set[int] = set()
        for p in parts_data:
            all_genre_ids.update(p.get("genre_ids", []))
        genres = [genre_map[gid] for gid in all_genre_ids if gid in genre_map]

        # Aggregate cast: rank by number of appearances across films, then popularity
        person_data: dict[int, dict] = {}
        for credits in credit_results:
            if isinstance(credits, Exception):
                continue
            for person in credits.get("cast", [])[:20]:
                pid = person.get("id")
                if pid not in person_data:
                    person_data[pid] = {
                        "tmdb_id": pid,
                        "name": person.get("name"),
                        "profile_path": tmdb.poster_url(person.get("profile_path"), size="w185"),
                        "appearances": 0,
                        "popularity": person.get("popularity", 0),
                    }
                person_data[pid]["appearances"] += 1

        cast = sorted(
            person_data.values(),
            key=lambda x: (-x["appearances"], -x["popularity"]),
        )[:15]

        parts = [
            {
                "tmdb_id": p.get("id"),
                "type": "movie",
                "title": p.get("title"),
                "poster_path": tmdb.poster_url(p.get("poster_path")),
                "backdrop_path": tmdb.poster_url(p.get("backdrop_path"), size="w1280"),
                "release_date": p.get("release_date"),
                "tmdb_rating": p.get("vote_average"),
                "overview": p.get("overview"),
            }
            for p in parts_data
        ]
        await media_presentation.enrich_with_state(db, current_user.id, parts)

        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "overview": data.get("overview"),
            "poster_path": tmdb.poster_url(data.get("poster_path")),
            "backdrop_path": tmdb.poster_url(data.get("backdrop_path"), size="w1280"),
            "genres": genres,
            "cast": cast,
            "parts": parts,
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=404, detail=f"Collection not found: {e}")


def _format_studio(data: dict) -> dict:
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "logo_path": tmdb.poster_url(data.get("logo_path"), size="w500") if data.get("logo_path") else None,
        "origin_country": data.get("origin_country") or None,
        "homepage": data.get("homepage") or None,
    }


@router.get("/network/{network_id}")
async def get_network_details(
    network_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    """Header data for the /network/{id} browse page (its titles come from
    /media/tmdb/list?network=)."""
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID
    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        raise HTTPException(status_code=404, detail="TMDB API Key not configured")
    try:
        return _format_studio(await tmdb.get_network(network_id, api_key=tmdb_key))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Network not found: {e}")


@router.get("/company/{company_id}")
async def get_company_details(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    """Header data for the /studio/{id} browse page (its titles come from
    /media/tmdb/list?company=)."""
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID
    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        raise HTTPException(status_code=404, detail="TMDB API Key not configured")
    try:
        return _format_studio(await tmdb.get_company(company_id, api_key=tmdb_key))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Company not found: {e}")


_COLLECTION_CHECKS = {
    "in": lambda i: bool(i.get("in_library")),
    "out": lambda i: not i.get("in_library"),
}


_WATCH_CHECKS = {
    "watched": lambda i: bool(i.get("watched")),
    "unwatched": lambda i: not i.get("watched") and not i.get("watch_started"),
    "started": lambda i: bool(i.get("watch_started")) and not i.get("watched"),
}


_ARR_CHECKS = {
    "added": lambda i: bool(i.get("is_monitored")),
    "notadded": lambda i: not i.get("is_monitored"),
}


_IN_LIST_CHECKS = {
    "in": lambda i: bool(i.get("in_lists")),
    "out": lambda i: not i.get("in_lists"),
}


def _apply_local_filters(
    items: list[dict], collection: list[str], watch: list[str], arr: list[str], year: list[int] | None = None,
    in_list: list[str] | None = None,
) -> list[dict]:
    """Local-only filters get_tmdb_list applies after TMDB enrichment.

    collection/watch/arr can't be expressed by TMDB's discover API at all.
    year could (primary_release_year/first_air_date_year), but only as a
    single value - TMDB has no "year A OR year B" for discover, so a
    multi-year selection is applied locally here instead, same as the others.

    Each category is OR'd internally (an item matching any selected value in
    that category passes) and AND'd across categories - same convention as
    the genre/year filters on /media/list. An unrecognized value contributes
    no check, so a category made up entirely of unrecognized values is a
    no-op rather than matching nothing.
    """
    for values, checks in ((collection, _COLLECTION_CHECKS), (watch, _WATCH_CHECKS), (arr, _ARR_CHECKS), (in_list or [], _IN_LIST_CHECKS)):
        if not values:
            continue
        active = [checks[v] for v in values if v in checks]
        if active:
            items = [i for i in items if any(c(i) for c in active)]
    if year:
        wanted = {str(y) for y in year}
        items = [i for i in items if (i.get("release_date") or "")[:4] in wanted]
    return items


def _paginate_matches(matched: list[dict], page: int, page_size: int) -> tuple[list[dict], int]:
    """Slice a full scanned-and-filtered match list into one fixed-size page.

    total_pages is an approximation: the real total isn't knowable without
    scanning every remaining TMDB page, so this only ever advertises one page
    ahead of the current one when a next page is known to exist.
    """
    page_items = matched[(page - 1) * page_size : page * page_size]
    has_more = len(matched) > page * page_size
    total_pages = page + 1 if has_more else max(page, 1)
    return page_items, total_pages


@router.get("/tmdb/list")
async def get_tmdb_list(
    type: MediaType = Query(...),
    category: str = Query("popular"),
    page: int = Query(1, ge=1),
    genre: list[str] = Query(default=[]),  # OR'd together via TMDB's own with_genres "|" syntax
    # year can't be OR'd in a single TMDB discover request (no multi-value
    # param for it) - applied as a local filter alongside collection/watch/arr
    # below instead, so a multi-year selection still works in one response.
    year: list[int] = Query(default=[]),
    min_rating: float | None = Query(None),
    status: str | None = Query(None),
    original_language: str | None = Query(None),
    # Restrict discover to one TV network (with_networks, TV only) or one
    # production company (with_companies, movie or TV). Powers the
    # /network/{id} and /studio/{id} browse pages.
    network: int | None = Query(None),
    company: int | None = Query(None),
    # Local-state filters, applied after TMDB enrichment (OR'd within each,
    # same convention as genre above): collection = in|out,
    # watch = watched|unwatched|started, arr = added|notadded.
    collection: list[str] = Query(default=[]),
    watch: list[str] = Query(default=[]),
    arr: list[str] = Query(default=[]),
    # in_list = in|out: membership of any of the user's lists (#255).
    in_list: list[str] = Query(default=[]),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        # Anonymous browsing is only allowed when the admin has opted in and a
        # global TMDB key is actually set - mirrors the check in
        # /profile/public-access-status, which the frontend middleware gates
        # page access on. Re-checked here since this endpoint is reachable
        # directly, not just through the page.
        gs = await settings_store.get_global_settings(db)
        if not (gs and gs.enable_logged_out_navigation and gs.tmdb_api_key):
            raise HTTPException(status_code=401, detail="Not authenticated")
        tmdb_key = gs.tmdb_api_key
        # No personal library/watch history to filter on without a user.
        collection, watch, arr = [], [], []
    else:
        tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)

    # with_networks is a TV-only discover constraint - a /network/{id} page only
    # ever wants shows regardless of what `type` the client passed.
    if network is not None:
        type = MediaType.series

    try:
        if not settings_store.check_tmdb_key(tmdb_key):
            return {"page": page, "total_pages": 1, "total_results": 0, "results": []}

        category_sort_map = {
            "popular": "popularity.desc",
            "top_rated": "vote_average.desc",
            "trending": "popularity.desc",
        }
        # A network/company constraint always goes through discover (there's no
        # "popular titles on network X" list endpoint).
        by_studio = network is not None or company is not None
        has_filters = bool(genre or min_rating or status or original_language) or by_studio

        # Explore cards render straight from these TMDB responses, so ask TMDB
        # for the user's Metadata Language directly - detail pages and list
        # cards already translate, Explore was the one place still hardcoded
        # to TMDB's default English (#235 follow-up). No profile to read a
        # language from when browsing anonymously.
        metadata_lang = await get_user_metadata_language(db, current_user.id) if current_user else None

        async def _fetch_tmdb_page(fetch_page: int) -> dict:
            if has_filters:
                sort_by = category_sort_map.get(category, "popularity.desc")
                # Browsing a whole network/company catalogue: drop the default
                # vote-count floor so older/low-vote back-catalogue still shows
                # (popularity sort keeps the obscure stuff at the bottom).
                studio_vote_min = 0 if by_studio else None
                if type == MediaType.movie:
                    genre_ids = [MOVIE_GENRE_IDS[g] for g in genre if g in MOVIE_GENRE_IDS]
                    return await tmdb.discover_movies(
                        page=fetch_page, genre_ids=genre_ids or None,
                        min_rating=min_rating, sort_by=sort_by,
                        vote_count_min=studio_vote_min,
                        with_original_language=original_language,
                        with_companies=company, api_key=tmdb_key,
                        language=metadata_lang,
                    )
                genre_ids = [TV_GENRE_IDS[g] for g in genre if g in TV_GENRE_IDS]
                status_id = TV_STATUS_IDS.get(status) if status else None
                return await tmdb.discover_shows(
                    page=fetch_page, genre_ids=genre_ids or None,
                    min_rating=min_rating, sort_by=sort_by,
                    vote_count_min=studio_vote_min,
                    status=status_id, with_original_language=original_language,
                    with_networks=network, with_companies=company,
                    api_key=tmdb_key, language=metadata_lang,
                )
            if type == MediaType.movie:
                if category == "top_rated":
                    return await tmdb.get_top_rated_movies(page=fetch_page, api_key=tmdb_key, language=metadata_lang)
                if category == "trending":
                    return await tmdb.get_trending_movies(page=fetch_page, api_key=tmdb_key, language=metadata_lang)
                return await tmdb.get_popular_movies(page=fetch_page, api_key=tmdb_key, language=metadata_lang)
            # series/episode
            if category == "top_rated":
                return await tmdb.get_top_rated_shows(page=fetch_page, api_key=tmdb_key, language=metadata_lang)
            if category == "trending":
                return await tmdb.get_trending_shows(page=fetch_page, api_key=tmdb_key, language=metadata_lang)
            return await tmdb.get_popular_shows(page=fetch_page, api_key=tmdb_key, language=metadata_lang)

        async def _build_enriched(results: list[dict]) -> list[dict]:
            tmdb_ids = [res["id"] for res in results]

            # Check local library. Anonymous visitors have no personal
            # collection to match against, so shows never come back "in
            # library" for them (movies below aren't user-scoped, so those
            # are unaffected).
            if type == MediaType.series and current_user is not None:
                # Match against Show.tmdb_id — never use episode tmdb_ids here,
                # as TMDB IDs across shows and episodes share the same number space
                # and collide (causing episodes to appear in show listings).
                show_q = (
                    select(ShowModel.tmdb_id)
                    .join(Media, Media.show_id == ShowModel.id)
                    .join(Collection, Collection.media_id == Media.id)
                    .where(
                        Collection.user_id == current_user.id,
                        ShowModel.tmdb_id.in_(tmdb_ids),
                    )
                    .distinct()
                )
                show_result = await db.execute(show_q)
                library_tmdb_ids = {row[0] for row in show_result.all()}
            elif type == MediaType.series:
                library_tmdb_ids = set()
            else:
                query = (
                    select(Media)
                    .where(Media.tmdb_id.in_(tmdb_ids), Media.media_type == MediaType.movie)
                )
                result = await db.execute(query)
                library_tmdb_ids = {m.tmdb_id for m in result.scalars().all()}

            enriched = []
            for res in results:
                tmdb_id = res["id"]
                enriched.append(
                    {
                        "id": None,
                        "tmdb_id": tmdb_id,
                        "type": type,
                        "title": res.get("title") or res.get("name"),
                        "poster_path": tmdb.poster_url(res.get("poster_path")),
                        "release_date": res.get("release_date") or res.get("first_air_date"),
                        "tmdb_rating": res.get("vote_average"),
                        "in_library": tmdb_id in library_tmdb_ids,
                        "adult": res.get("adult", False),
                    }
                )
            if current_user is not None:
                await media_presentation.enrich_with_state(db, current_user.id, enriched)
            return enriched

        if not (collection or watch or arr or year or in_list):
            data = await _fetch_tmdb_page(page)
            enriched = await _build_enriched(data.get("results", []))
            return {
                "page": data.get("page", 1),
                "total_pages": data.get("total_pages", 1),
                "total_results": data.get("total_results", 0),
                "results": enriched,
            }

        # The local filters drop an unpredictable share of each TMDB page, so
        # scan pages forward and rebuild fixed-size pages of matches.
        PAGE_SIZE = 20
        MAX_SCAN_PAGES = 30
        SCAN_BATCH = 5
        needed = page * PAGE_SIZE
        matched: list[dict] = []
        seen_ids: set[int] = set()
        scan_page = 1
        total_tmdb_pages: int | None = None
        while scan_page <= MAX_SCAN_PAGES and len(matched) <= needed:
            batch_end = min(scan_page + SCAN_BATCH - 1, MAX_SCAN_PAGES)
            if total_tmdb_pages is not None:
                batch_end = min(batch_end, total_tmdb_pages)
            batch_pages = list(range(scan_page, batch_end + 1))
            if not batch_pages:
                break
            datas = await asyncio.gather(*(_fetch_tmdb_page(p) for p in batch_pages))
            if total_tmdb_pages is None:
                total_tmdb_pages = datas[0].get("total_pages", 1)
            batch_results = []
            for d in datas:
                for res in d.get("results", []):
                    # popular/trending ordering shifts between fetches - dedupe
                    if res["id"] not in seen_ids:
                        seen_ids.add(res["id"])
                        batch_results.append(res)
            if batch_results:
                enriched = await _build_enriched(batch_results)
                matched.extend(_apply_local_filters(enriched, collection, watch, arr, year, in_list))
            scan_page = batch_end + 1
            if total_tmdb_pages is not None and scan_page > total_tmdb_pages:
                break

        page_items, total_pages = _paginate_matches(matched, page, PAGE_SIZE)
        return {
            "page": page,
            "total_pages": total_pages,
            "total_results": len(matched),
            "results": page_items,
        }
    except Exception as e:
        print(f"Error fetching TMDB list: {e}")
        return {"page": page, "total_pages": 1, "total_results": 0, "results": []}


def _enrich_movie_list(results: list[dict], library_ids: set[int]) -> list[dict]:
    return [
        {
            "id": None,
            "tmdb_id": r["id"],
            "type": MediaType.movie,
            "title": r.get("title"),
            "poster_path": tmdb.poster_url(r.get("poster_path")),
            "backdrop_path": tmdb.poster_url(r.get("backdrop_path"), size="w1280"),
            "release_date": r.get("release_date"),
            "tmdb_rating": r.get("vote_average"),
            "in_library": r["id"] in library_ids,
            "adult": r.get("adult", False),
        }
        for r in results if r.get("id")
    ]


def _enrich_show_list(results: list[dict], library_ids: set[int]) -> list[dict]:
    return [
        {
            "id": None,
            "tmdb_id": r["id"],
            "type": MediaType.series,
            "title": r.get("name"),
            "poster_path": tmdb.poster_url(r.get("poster_path")),
            "backdrop_path": tmdb.poster_url(r.get("backdrop_path"), size="w1280"),
            "release_date": r.get("first_air_date"),
            "tmdb_rating": r.get("vote_average"),
            "in_library": r["id"] in library_ids,
            "adult": r.get("adult", False),
        }
        for r in results if r.get("id")
    ]


async def _movie_library_ids(db: AsyncSession, user_id: int, tmdb_ids: list[int]) -> set[int]:
    q = await db.execute(
        select(Media.tmdb_id)
        .join(Collection, Collection.media_id == Media.id)
        .where(Collection.user_id == user_id, Media.tmdb_id.in_(tmdb_ids), Media.media_type == MediaType.movie)
        .distinct()
    )
    return {row[0] for row in q.all()}


async def _show_library_ids(db: AsyncSession, user_id: int, tmdb_ids: list[int]) -> set[int]:
    q = await db.execute(
        select(ShowModel.tmdb_id)
        .join(Media, Media.show_id == ShowModel.id)
        .join(Collection, Collection.media_id == Media.id)
        .where(Collection.user_id == user_id, ShowModel.tmdb_id.in_(tmdb_ids))
        .distinct()
    )
    return {row[0] for row in q.all()}


@router.get("/now-playing")
async def now_playing(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID
    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}
    try:
        data = await tmdb.get_now_playing(api_key=tmdb_key)
        results = data.get("results", [])
        ids = [r["id"] for r in results if r.get("id")]
        lib = await _movie_library_ids(db, effective_user_id, ids)
        items = _enrich_movie_list(results, lib)
        await media_presentation.enrich_with_state(db, effective_user_id, items)
        return {"results": items}
    except Exception:
        return {"results": []}


@router.get("/trending/trailers")
async def trending_trailers(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID
    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}
    try:
        data = await tmdb.get_trending_movies(time_window="week", api_key=tmdb_key)
        movies = data.get("results", [])[:16]

        async def fetch_trailer(movie: dict) -> dict | None:
            try:
                vdata = await tmdb.get_movie_videos(movie["id"], api_key=tmdb_key)
                videos = vdata.get("results", [])
                trailer = next(
                    (v for v in videos if v.get("site") == "YouTube" and v.get("type") == "Trailer" and v.get("official")),
                    next((v for v in videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"), None),
                )
                if not trailer:
                    return None
                return {
                    "tmdb_id": movie["id"],
                    "title": movie.get("title") or movie.get("name"),
                    "poster_path": tmdb.poster_url(movie.get("poster_path")),
                    "backdrop_path": tmdb.poster_url(movie.get("backdrop_path"), size="w780"),
                    "release_date": movie.get("release_date"),
                    "trailer_key": trailer["key"],
                    "trailer_name": trailer.get("name", ""),
                }
            except Exception:
                return None

        results_raw = await asyncio.gather(*[fetch_trailer(m) for m in movies])
        results = [r for r in results_raw if r is not None]
        return {"results": results}
    except Exception:
        return {"results": []}


@router.get("/upcoming")
async def upcoming_movies(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID
    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}
    try:
        data = await tmdb.get_upcoming_movies(api_key=tmdb_key)
        results = data.get("results", [])
        ids = [r["id"] for r in results if r.get("id")]
        lib = await _movie_library_ids(db, effective_user_id, ids)
        items = _enrich_movie_list(results, lib)
        await media_presentation.enrich_with_state(db, effective_user_id, items)
        return {"results": items}
    except Exception:
        return {"results": []}


@router.get("/on-air-this-week")
async def on_air_this_week(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID
    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}
    try:
        data = await tmdb.get_on_air_this_week(api_key=tmdb_key)
        results = data.get("results", [])
        ids = [r["id"] for r in results if r.get("id")]
        lib = await _show_library_ids(db, effective_user_id, ids)
        items = _enrich_show_list(results, lib)
        await media_presentation.enrich_with_state(db, effective_user_id, items)
        return {"results": items}
    except Exception:
        return {"results": []}


@router.get("/hidden-gems")
async def hidden_gems(
    type: MediaType = Query(MediaType.movie),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID
    import random
    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}
    try:
        page = random.randint(1, 5)
        if type == MediaType.movie:
            data = await tmdb.discover_movies(
                page=page, sort_by="vote_average.desc",
                min_rating=7.5, vote_count_min=150, vote_count_max=3000,
                api_key=tmdb_key,
            )
            results = data.get("results", [])
            ids = [r["id"] for r in results if r.get("id")]
            lib = await _movie_library_ids(db, effective_user_id, ids)
            items = _enrich_movie_list(results, lib)
            await media_presentation.enrich_with_state(db, effective_user_id, items)
            return {"results": items}
        else:
            data = await tmdb.discover_shows(
                page=page, sort_by="vote_average.desc",
                min_rating=7.5, vote_count_min=150, vote_count_max=3000,
                api_key=tmdb_key,
            )
            results = data.get("results", [])
            ids = [r["id"] for r in results if r.get("id")]
            lib = await _show_library_ids(db, effective_user_id, ids)
            items = _enrich_show_list(results, lib)
            await media_presentation.enrich_with_state(db, effective_user_id, items)
            return {"results": items}
    except Exception:
        return {"results": []}


@router.get("/top-rated-movies")
async def top_rated_movies(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID
    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}
    try:
        data = await tmdb.get_top_rated_movies(api_key=tmdb_key)
        results = data.get("results", [])
        ids = [r["id"] for r in results if r.get("id")]
        lib = await _movie_library_ids(db, effective_user_id, ids)
        items = _enrich_movie_list(results, lib)
        await media_presentation.enrich_with_state(db, effective_user_id, items)
        return {"results": items}
    except Exception:
        return {"results": []}


@router.get("/top-rated-shows")
async def top_rated_shows(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID
    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}
    try:
        data = await tmdb.get_top_rated_shows(api_key=tmdb_key)
        results = data.get("results", [])
        ids = [r["id"] for r in results if r.get("id")]
        lib = await _show_library_ids(db, effective_user_id, ids)
        items = _enrich_show_list(results, lib)
        await media_presentation.enrich_with_state(db, effective_user_id, items)
        return {"results": items}
    except Exception:
        return {"results": []}


@router.get("/for-you")
async def for_you(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    import random

    cached = _FOR_YOU_CACHE.get(current_user.id)
    if cached and (_time.monotonic() - cached[0]) < _FOR_YOU_TTL:
        return cached[1]

    tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}

    profile_q = await db.execute(
        select(UserProfileData).where(UserProfileData.user_id == current_user.id)
    )
    profile = profile_q.scalar_one_or_none()

    if not profile:
        return {"results": []}

    movie_genres = profile.movie_genres or []
    show_genres = profile.show_genres or []
    disliked_genres: set[str] = set(profile.disliked_genres or [])
    language: str | None = getattr(profile, "content_language", None)

    if not movie_genres and not show_genres:
        return {"results": []}

    selected_movie_genres = random.sample(movie_genres, min(2, len(movie_genres)))
    selected_show_genres = random.sample(show_genres, min(2, len(show_genres)))

    movie_coros = []
    show_coros = []

    for genre_name in selected_movie_genres:
        genre_id = MOVIE_GENRE_IDS.get(genre_name)
        if genre_id:
            movie_coros.append(tmdb.discover_movies(
                genre_id=genre_id,
                sort_by="popularity.desc",
                with_original_language=language,
                api_key=tmdb_key,
            ))

    for genre_name in selected_show_genres:
        genre_id = TV_GENRE_IDS.get(genre_name)
        if genre_id:
            show_coros.append(tmdb.discover_shows(
                genre_id=genre_id,
                sort_by="popularity.desc",
                with_original_language=language,
                api_key=tmdb_key,
            ))

    if not movie_coros and not show_coros:
        return {"results": []}

    all_results = await asyncio.gather(*(movie_coros + show_coros), return_exceptions=True)

    num_movie_coros = len(movie_coros)
    movie_raw: list[dict] = []
    show_raw: list[dict] = []

    for i, res in enumerate(all_results):
        if isinstance(res, Exception):
            continue
        raw = res.get("results", [])[:8]
        if i < num_movie_coros:
            movie_raw.extend(raw)
        else:
            show_raw.extend(raw)

    movie_liked_set = set(movie_genres)
    show_liked_set = set(show_genres)

    seen: set[int] = set()
    unique_movies: list[dict] = []
    for r in _filter_disliked(movie_raw, disliked_genres, movie_liked_set, MOVIE_GENRE_NAMES):
        rid = r.get("id")
        if rid and rid not in seen:
            seen.add(rid)
            unique_movies.append(r)

    seen2: set[int] = set()
    unique_shows: list[dict] = []
    for r in _filter_disliked(show_raw, disliked_genres, show_liked_set, TV_GENRE_NAMES):
        rid = r.get("id")
        if rid and rid not in seen2:
            seen2.add(rid)
            unique_shows.append(r)

    movie_ids = [r["id"] for r in unique_movies]
    show_ids = [r["id"] for r in unique_shows]

    movie_lib = await _movie_library_ids(db, current_user.id, movie_ids) if movie_ids else set()
    show_lib = await _show_library_ids(db, current_user.id, show_ids) if show_ids else set()

    movie_items = _enrich_movie_list(unique_movies, movie_lib)
    show_items = _enrich_show_list(unique_shows, show_lib)

    combined = movie_items + show_items
    random.shuffle(combined)

    await media_presentation.enrich_with_state(db, current_user.id, combined)
    # Dropped items must never come back as a recommendation (#117).
    dropped_movie_ids, dropped_show_ids = await media_presentation._dropped_tmdb_ids(db, current_user.id)
    unwatched = [
        item for item in combined
        if not item.get("watched")
        and item.get("tmdb_id") not in (dropped_movie_ids if item.get("type") == MediaType.movie else dropped_show_ids)
    ]
    result = {"results": unwatched[:20]}
    _FOR_YOU_CACHE[current_user.id] = (_time.monotonic(), result)
    return result


@router.get("/streaming")
async def streaming(
    provider_id: int,
    type: MediaType = Query(MediaType.movie),
    watch_region: str = Query("US"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_or_api_key),
):
    if current_user is None:
        await media_presentation.require_anon_nav_allowed(db)
    effective_user_id = current_user.id if current_user else ANON_USER_ID
    tmdb_key = await settings_store.get_user_tmdb_key(db, effective_user_id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}
    try:
        if type == MediaType.movie:
            data = await tmdb.discover_movies(
                watch_provider_id=provider_id,
                watch_region=watch_region,
                api_key=tmdb_key,
            )
            results = data.get("results", [])
            ids = [r["id"] for r in results if r.get("id")]
            lib = await _movie_library_ids(db, effective_user_id, ids)
            items = _enrich_movie_list(results, lib)
        else:
            data = await tmdb.discover_shows(
                watch_provider_id=provider_id,
                watch_region=watch_region,
                api_key=tmdb_key,
            )
            results = data.get("results", [])
            ids = [r["id"] for r in results if r.get("id")]
            lib = await _show_library_ids(db, effective_user_id, ids)
            items = _enrich_show_list(results, lib)
        await media_presentation.enrich_with_state(db, effective_user_id, items)
        return {"results": items}
    except Exception:
        return {"results": []}


@router.get("/new-episodes")
async def new_episodes(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)
    if not settings_store.check_tmdb_key(tmdb_key):
        return {"results": []}
    try:
        data = await tmdb.get_on_air_this_week(api_key=tmdb_key)
        results = data.get("results", [])
        ids = [r["id"] for r in results if r.get("id")]
        lib = await _show_library_ids(db, current_user.id, ids)
        items = _enrich_show_list(results, lib)
        await media_presentation.enrich_with_state(db, current_user.id, items)
        # Only return shows the user has in their library
        library_items = [i for i in items if i.get("in_library")]
        return {"results": library_items}
    except Exception:
        return {"results": []}


def _normalize_path(path: str | None, size: str = "w500") -> str | None:
    if not path:
        return None
    if path.startswith("http"):
        return path
    return tmdb.poster_url(path, size=size)


@router.get("/pick")
async def pick_for_me(
    type: str = Query("movie"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    import random

    if type not in ("movie", "series"):
        raise HTTPException(status_code=400, detail="type must be 'movie' or 'series'")

    tmdb_key = await settings_store.get_user_tmdb_key(db, current_user.id)

    profile_q = await db.execute(select(UserProfileData).where(UserProfileData.user_id == current_user.id))
    profile = profile_q.scalar_one_or_none()

    conns_q = await db.execute(select(MediaServerConnection).where(MediaServerConnection.user_id == current_user.id))
    connections = conns_q.scalars().all()

    streaming_ids = [int(s) for s in (profile.streaming_services or [])] if profile else []
    region = (profile.country if profile and profile.country else None) or "US"
    has_media_server = bool(connections)

    if not streaming_ids and not has_media_server:
        raise HTTPException(status_code=400, detail="no_sources")

    # ── Watched IDs ────────────────────────────────────────────────────────
    if type == "movie":
        wq = await db.execute(
            select(Media.tmdb_id)
            .join(WatchEvent, WatchEvent.media_id == Media.id)
            .where(WatchEvent.user_id == current_user.id, Media.media_type == MediaType.movie)
            .distinct()
        )
    else:
        wq = await db.execute(
            select(ShowModel.tmdb_id)
            .join(Media, Media.show_id == ShowModel.id)
            .join(WatchEvent, WatchEvent.media_id == Media.id)
            .where(WatchEvent.user_id == current_user.id)
            .distinct()
        )
    watched_ids: set[int] = {r[0] for r in wq.all()}

    # ── Collection pool (unwatched) ────────────────────────────────────────
    collection_items: list[dict] = []
    if has_media_server:
        if type == "movie":
            cq = await db.execute(
                select(Media.tmdb_id, Media.title, Media.poster_path, Media.backdrop_path,
                       Media.release_date, Media.tmdb_rating, Media.overview)
                .join(Collection, Collection.media_id == Media.id)
                .where(Collection.user_id == current_user.id, Media.media_type == MediaType.movie)
                .where(Media.tmdb_id.notin_(watched_ids))
                .distinct()
            )
            collection_items = [
                {
                    "tmdb_id": r[0], "type": "movie", "title": r[1],
                    "poster_path": _normalize_path(r[2]),
                    "backdrop_path": _normalize_path(r[3], "w1280"),
                    "release_date": r[4], "tmdb_rating": r[5],
                    "overview": r[6], "in_library": True,
                }
                for r in cq.all() if r[0] and r[0] not in watched_ids
            ]
        else:
            cq = await db.execute(
                select(ShowModel.tmdb_id, ShowModel.title, ShowModel.poster_path,
                       ShowModel.backdrop_path, ShowModel.first_air_date,
                       ShowModel.tmdb_rating, ShowModel.overview)
                .join(Media, Media.show_id == ShowModel.id)
                .join(Collection, Collection.media_id == Media.id)
                .where(Collection.user_id == current_user.id)
                .where(ShowModel.tmdb_id.notin_(watched_ids))
                .distinct()
            )
            collection_items = [
                {
                    "tmdb_id": r[0], "type": "series", "title": r[1],
                    "poster_path": _normalize_path(r[2]),
                    "backdrop_path": _normalize_path(r[3], "w1280"),
                    "release_date": r[4], "tmdb_rating": r[5],
                    "overview": r[6], "in_library": True,
                }
                for r in cq.all() if r[0] and r[0] not in watched_ids
            ]

    # ── Streaming pool (progressive fallback) ─────────────────────────────
    streaming_candidates: list[dict] = []
    if streaming_ids and settings_store.check_tmdb_key(tmdb_key):
        disliked: set[str] = set(profile.disliked_genres or []) if profile else set()
        user_genres = ((profile.movie_genres if type == "movie" else profile.show_genres) or []) if profile else []
        genre_map = MOVIE_GENRE_IDS if type == "movie" else TV_GENRE_IDS
        genre_ids = [genre_map[g] for g in user_genres if g in genre_map]

        tiers = [
            {"genre_ids": genre_ids[:3], "min_rating": 6.0},
            {"genre_ids": [], "min_rating": 6.0},
            {"genre_ids": [], "min_rating": None},
        ]

        for tier in tiers:
            if len(streaming_candidates) + len(collection_items) >= 15:
                break
            coros = []
            for pid in streaming_ids:
                kwargs: dict = dict(watch_provider_id=pid, watch_region=region, api_key=tmdb_key)
                if tier["min_rating"]:
                    kwargs["min_rating"] = tier["min_rating"]
                if tier["genre_ids"]:
                    for gid in tier["genre_ids"]:
                        fn = tmdb.discover_movies if type == "movie" else tmdb.discover_shows
                        coros.append(fn(genre_id=gid, **kwargs))
                else:
                    fn = tmdb.discover_movies if type == "movie" else tmdb.discover_shows
                    coros.append(fn(**kwargs))

            if coros:
                results_list = await asyncio.gather(*coros, return_exceptions=True)
                for res in results_list:
                    if isinstance(res, Exception):
                        continue
                    for r in res.get("results", []):
                        tid = r.get("id")
                        if tid and tid not in watched_ids:
                            streaming_candidates.append(r)

    # ── Combine & deduplicate ──────────────────────────────────────────────
    genre_id_map = {v: k for k, v in (MOVIE_GENRE_IDS if type == "movie" else TV_GENRE_IDS).items()}

    seen: set[int] = set()
    all_candidates: list[dict] = []

    for item in collection_items:
        if item["tmdb_id"] not in seen:
            seen.add(item["tmdb_id"])
            all_candidates.append(item)

    for r in streaming_candidates:
        tid = r.get("id")
        if tid and tid not in seen and tid not in watched_ids:
            seen.add(tid)
            all_candidates.append({
                "tmdb_id": tid,
                "type": type,
                "title": r.get("title") if type == "movie" else r.get("name"),
                "poster_path": tmdb.poster_url(r.get("poster_path")),
                "backdrop_path": tmdb.poster_url(r.get("backdrop_path"), size="w1280"),
                "release_date": r.get("release_date") if type == "movie" else r.get("first_air_date"),
                "tmdb_rating": r.get("vote_average"),
                "overview": r.get("overview"),
                "in_library": False,
                "genres": [genre_id_map[gid] for gid in r.get("genre_ids", []) if gid in genre_id_map],
                "adult": r.get("adult", False),
            })

    if not all_candidates:
        raise HTTPException(status_code=404, detail="no_results")

    liked_set = set(user_genres)
    if disliked or liked_set:
        weights = []
        for item in all_candidates:
            item_genres: list[str] = item.get("genres") or []
            score = 1.0
            for g in item_genres:
                if g in liked_set:
                    score += 2.0
                elif g in disliked:
                    score -= 1.5
            weights.append(max(0.05, score))
        pick = random.choices(all_candidates, weights=weights, k=1)[0]
    else:
        pick = random.choice(all_candidates)

    # ── Fetch genres from local DB for the picked item ─────────────────────
    if not pick.get("genres"):
        if type == "movie":
            genres_q = await db.execute(
                select(Media.tmdb_data).where(
                    Media.tmdb_id == pick["tmdb_id"], Media.media_type == MediaType.movie
                ).limit(1)
            )
        else:
            genres_q = await db.execute(
                select(ShowModel.tmdb_data).where(ShowModel.tmdb_id == pick["tmdb_id"]).limit(1)
            )
        row = genres_q.scalar_one_or_none()
        if row:
            pick["genres"] = (row or {}).get("genres", [])

    # ── Enrich pick: overview + watch providers ────────────────────────────
    sources: list[dict] = []
    if settings_store.check_tmdb_key(tmdb_key):
        try:
            if not pick.get("overview") or not pick.get("genres"):
                if type == "movie":
                    details = await tmdb.get_movie(pick["tmdb_id"], api_key=tmdb_key)
                else:
                    details = await tmdb.get_show(pick["tmdb_id"], api_key=tmdb_key)
                if not pick.get("overview"):
                    pick["overview"] = details.get("overview")
                if not pick.get("genres"):
                    pick["genres"] = [g["name"] for g in details.get("genres", [])]

            if type == "movie":
                providers_data = await tmdb.get_movie_watch_providers(pick["tmdb_id"], api_key=tmdb_key)
            else:
                providers_data = await tmdb.get_show_watch_providers(pick["tmdb_id"], api_key=tmdb_key)

            region_providers = providers_data.get("results", {}).get(region, {})
            flatrate = region_providers.get("flatrate", [])
            str_streaming_ids = [str(s) for s in streaming_ids]
            for p in flatrate:
                if str(p.get("provider_id", "")) in str_streaming_ids:
                    sources.append({
                        "type": "streaming",
                        "name": p.get("provider_name"),
                        "logo": f"https://image.tmdb.org/t/p/w45{p['logo_path']}" if p.get("logo_path") else None,
                    })
        except Exception:
            pass

    if pick.get("in_library"):
        if type == "movie":
            cf_conn_q = await db.execute(
                select(MediaServerConnection)
                .join(CollectionFile, CollectionFile.connection_id == MediaServerConnection.id)
                .join(Collection, Collection.id == CollectionFile.collection_id)
                .join(Media, Media.id == Collection.media_id)
                .where(
                    Media.tmdb_id == pick["tmdb_id"],
                    Media.media_type == MediaType.movie,
                    Collection.user_id == current_user.id,
                    CollectionFile.connection_id.isnot(None),
                )
                .group_by(MediaServerConnection.id)
            )
        else:
            cf_conn_q = await db.execute(
                select(MediaServerConnection)
                .join(CollectionFile, CollectionFile.connection_id == MediaServerConnection.id)
                .join(Collection, Collection.id == CollectionFile.collection_id)
                .join(Media, Media.id == Collection.media_id)
                .join(ShowModel, ShowModel.id == Media.show_id)
                .where(
                    ShowModel.tmdb_id == pick["tmdb_id"],
                    Collection.user_id == current_user.id,
                    CollectionFile.connection_id.isnot(None),
                )
                .group_by(MediaServerConnection.id)
            )
        seen_conn_ids: set[int] = set()
        for c in cf_conn_q.scalars().all():
            if c.id not in seen_conn_ids:
                seen_conn_ids.add(c.id)
                sources.append({"type": c.type, "name": c.name or c.type.title(), "logo": None})

    pick["sources"] = sources
    return pick
