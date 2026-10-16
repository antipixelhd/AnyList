"""Paged discovery with matching local filters and private viewer list state."""

import asyncio
from datetime import date, timedelta

from sqlalchemy import Boolean, Float, and_, case, func, literal, or_, select, text, type_coerce
from sqlalchemy.dialects.postgresql import JSONB

from core import mdblist_discovery, tmdb, tracking_projection
from core.catalog_search import DEFAULT_MIN_VOTES, matches_result_filters, apply_result_filters, known_result_metadata
from core.cinema_releases import theatrical_dates, theatrical_country_dates, has_cinema_release
from models import Media
from models.base import MediaType

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
    "returning": ["Returning Series"],
    "finished": ["Ended"],
    "cancelled": ["Canceled"],
    "upcoming": ["Planned", "In Production", "Post Production"],
    "released": ["Released"],
}
TV_STATUS = {"airing": "0", "returning": "0", "finished": "3", "cancelled": "4"}
SORTS = {
    "trending": "popularity.desc",
    "popular": "popularity.desc",
    "score": "vote_average.desc",
    "newest": "primary_release_date.desc",
    "oldest": "primary_release_date.asc",
    "title": "title.asc",
}
IMDB_SORTS = {"trending", "popular", "score"}
PAGE_SIZE = 24


def series_airing_clause():
    """SQL counterpart of current_airing_details, including cached episodes."""
    return type_coerce(text("""EXISTS (
      SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(media.tmdb_data->'seasons')='array'
        THEN media.tmdb_data->'seasons' ELSE '[]'::jsonb END) season
      WHERE (season->>'season_number')::int > 0 AND (season->>'episode_count')::int > 0
        AND season->>'air_date' <= :airing_today AND season->>'air_date' <> ''
        AND (
          (media.tmdb_data->'next_episode_to_air'->>'season_number' = season->>'season_number'
            AND media.tmdb_data->'next_episode_to_air'->>'air_date' >= :airing_today
            AND ((media.tmdb_data->'last_episode_to_air'->>'season_number')::int IS NULL OR
              ((media.tmdb_data->'next_episode_to_air'->>'season_number')::int,
               (media.tmdb_data->'next_episode_to_air'->>'episode_number')::int) >
              ((media.tmdb_data->'last_episode_to_air'->>'season_number')::int,
               (media.tmdb_data->'last_episode_to_air'->>'episode_number')::int)))
          OR EXISTS (SELECT 1 FROM media episode JOIN shows ON shows.id=episode.show_id
            WHERE (shows.tmdb_id=media.tmdb_id OR shows.tvdb_id=media.tvdb_id)
              AND episode.season_number=(season->>'season_number')::int
              AND episode.release_date > :airing_today)
        )
        AND (SELECT count(*) FROM media episode JOIN shows ON shows.id=episode.show_id
          WHERE (shows.tmdb_id=media.tmdb_id OR shows.tvdb_id=media.tvdb_id)
            AND episode.season_number=(season->>'season_number')::int
            AND episode.release_date <= :airing_today) < (season->>'episode_count')::int
    )""").bindparams(airing_today=date.today().isoformat()), Boolean)


def section_specs(today=None):
    today = today or date.today()
    return [
        ("Trending now", {"sort": "trending"}),
        (
            "Newest",
            {
                "sort": "newest",
                "end": today.isoformat(),
            },
        ),
        ("All-time popular", {"sort": "popular"}),
        ("Highest rated", {"sort": "score"}),
    ]


async def browse_sections(
    db, viewer, *, media_type, region, show_anime, key, mdblist_key=None,
    min_votes=DEFAULT_MIN_VOTES, min_score=0, min_runtime=0, excluded_genres=(),
):
    specs = section_specs()
    filters = [
        {
            **dict(
                genres=[],
                start=None,
                end=None,
                status="",
                provider=None,
                region=region,
                min_votes=min_votes,
                min_score=min_score,
                min_runtime=min_runtime, excluded_genres=excluded_genres,
            ),
            **values,
        }
        for _, values in specs
    ]
    # Fetch remote feeds concurrently, but use the shared database session sequentially.
    semaphore = asyncio.Semaphore(2)

    async def prefetch(values):
        async with semaphore:
            return await remote_page(
                "",
                media_type,
                key,
                values,
                1,
                mdblist_key=mdblist_key,
                show_anime=show_anime,
            )

    prefetched = (
        await asyncio.gather(
            *(prefetch(values) for values in filters),
            return_exceptions=True,
        )
        if key or mdblist_key
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
            mdblist_key=mdblist_key,
            prefetched=remote,
            **full_filters,
        )
        sections.append(
            {"title": title, "filters": {**values, "min_score": f'{min_score:g}'}, "results": page["results"][:6]}
        )
        if page.get("notice"):
            notices.append(page["notice"])
    return {"sections": sections, "notice": next(iter(notices), None)}


def metadata_fields(data):
    """Stored in existing JSONB so imports and refreshes retain discovery fields."""
    fields = {
        "popularity": data.get("popularity"),
        "vote_count": data.get("vote_count"),
        "watch_providers": (data.get("watch/providers") or {}).get("results", {}),
    }
    releases=data.get('release_dates')
    if isinstance(data.get('theatrical_release_dates'),list) or (
        isinstance(releases,dict) and isinstance(releases.get('results'),list)):
        fields['theatrical_release_dates']=theatrical_dates(data)
    countries=theatrical_country_dates(data)
    if isinstance(countries,dict):fields['theatrical_release_dates_by_country']=countries
    return fields


def remote_params(
    media_type, *, page, genres, start, end, status, provider, region, sort,
    min_votes=0, min_score=0, min_runtime=0, excluded_genres=(), cinema=False, cinema_country=None,
):
    params = {"page": page, "include_adult": "false", "sort_by": SORTS[sort]}
    # Future releases cannot yet meet a vote floor. Filter the response locally
    # when the requested date range can contain unreleased titles.
    params["vote_count.gte"] = min_votes if end and end <= date.today().isoformat() else 0
    params["vote_average.gte"] = min_score
    if media_type == "series":
        params["sort_by"] = (
            params["sort_by"]
            .replace("primary_release_date", "first_air_date")
            .replace("title", "name")
        )
    if excluded_genres:
        allowed = MOVIE_GENRES if media_type == "movie" else TV_GENRES
        excluded = sorted(set(excluded_genres) & allowed)
        if excluded: params["without_genres"] = ",".join(map(str, excluded))
    if media_type == "movie" and min_runtime:
        params["with_runtime.gte"] = min_runtime
    if genres:
        params["with_genres"] = ",".join(map(str, genres))
    field = "primary_release_date" if media_type == "movie" else "first_air_date"
    if start:
        params[f"{field}.gte"] = start
    if end:
        params[f"{field}.lte"] = end
    if media_type == "series" and status in TV_STATUS:
        params["with_status"] = TV_STATUS[status]
    if media_type == 'series' and status == 'upcoming':
        params[f'{field}.gte'] = max(start or '', (date.today() + timedelta(days=1)).isoformat())
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
    if cinema and media_type=='movie':
        params.update(with_release_type='2|3', **{'release_date.lte':date.today().isoformat()})
        if cinema_country:params['region']=cinema_country
    return params


def local_query(
    media_type,
    *,
    term,
    genres,
    start,
    end,
    status,
    provider,
    region,
    sort,
    show_anime,
    mdblist_key=None,
    min_votes=0, min_score=0, min_runtime=0, excluded_genres=(), cinema=False, cinema_country=None,
):
    query = select(Media).where(
        Media.media_type == MediaType(media_type), Media.adult.is_(False)
    )
    data = Media.tmdb_data
    if cinema and media_type=='movie':
        dates=data['theatrical_release_dates_by_country'][cinema_country] if cinema_country else data['theatrical_release_dates']
        query=query.where(dates[0].astext<=date.today().isoformat())
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
        if isinstance(genre, str):
            query = query.where(or_(data["genres"].contains([genre[5:]]), data["genres"].contains([{"name": genre[5:]}])))
            continue
        query = query.where(
            or_(
                data["genres"].contains([GENRES[genre]]),
                data["genres"].contains([{"id": genre}]),
                data["genres"].contains([{"name": GENRES[genre]}]),
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
        elif status in ('airing', 'returning'):
            active = series_airing_clause()
            query = query.where(or_(Media.status == 'Returning Series', data['status'].astext == 'Returning Series'),
                                active if status == 'airing' else ~active,
                                Media.release_date <= date.today().isoformat())
        elif status == 'upcoming':
            query = query.where(or_(Media.release_date > date.today().isoformat(),
                                    Media.status.in_(STATUSES[status]), data['status'].astext.in_(STATUSES[status])))
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
                Media.title.op('%')(term),
                Media.original_title.op('%')(term),
            )
        )
        exact = or_(func.lower(Media.title) == term.lower(), func.lower(Media.original_title) == term.lower())
        prefix = or_(Media.title.ilike(f'{escaped}%', escape='\\'), Media.original_title.ilike(f'{escaped}%', escape='\\'))
        substring = or_(Media.title.ilike(f'%{escaped}%', escape='\\'), Media.original_title.ilike(f'%{escaped}%', escape='\\'))
        query = query.order_by(case((exact, 0), (prefix, 1), (substring, 2), else_=3), similarity.desc())
    ordering = {
        "score": Media.imdb_rating.desc().nulls_last(),
        "newest": Media.release_date.desc().nulls_last(),
        "oldest": Media.release_date.asc().nulls_last(),
        "title": Media.title.asc(),
    }
    imdb_id = func.coalesce(Media.imdb_id, data["external_ids"]["imdb_id"].astext)
    cached = (
        mdblist_discovery.cached_rows(media_type, sort, mdblist_key)
        if sort in IMDB_SORTS
        else []
    )
    votes = data["imdb"]["votes"].astext.cast(Float)
    rank = data["imdb"]["popularity_rank"].astext.cast(Float)
    score = Media.imdb_rating
    if cached:
        # One JSON parameter per mapping keeps broad feeds below PostgreSQL's
        # bind-parameter limit, even for catalogues containing 10,000 titles.
        def cached_value(mapping, fallback):
            return func.coalesce(
                literal(mapping, type_=JSONB)[imdb_id].astext.cast(Float), fallback
            )

        votes = cached_value(
            {row["imdb_id"]: row["votes"] for row in cached if row.get("imdb_id")},
            votes,
        )
        rank = cached_value(
            {
                row["imdb_id"]: index
                for index, row in enumerate(cached, 1)
                if row.get("imdb_id")
            },
            rank,
        )
        ratings = {
            row["imdb_id"]: row["rating"]
            for row in cached
            if row.get("imdb_id") and row["rating"] is not None
        }
        if ratings:
            score = cached_value(ratings, score)
    ordering.update(
        score=score.desc().nulls_last(),
        popular=votes.desc().nulls_last(),
        trending=rank.asc().nulls_last(),
    )
    if min_score:
        query = query.where(func.coalesce(data["vote_average"].astext.cast(Float), Media.tmdb_rating, 0) >= min_score)
    query = apply_result_filters(query, data, media_type, min_votes=min_votes, min_runtime=min_runtime,
                                 excluded_genres=excluded_genres, runtime=Media.runtime, imdb_votes=votes, release_date=Media.release_date)
    if not term and status != 'upcoming':
        if sort == "score":
            query = query.where(score > 0)
        elif sort == "popular":
            query = query.where(votes > 0)
        elif sort == "trending":
            query = query.where(rank > 0)
    return query.order_by(ordering[sort], Media.id)


def matches(item, *, media_type, genres, start, end, status, provider, region, min_votes=0, min_score=0, min_runtime=0, excluded_genres=(), cinema=False, cinema_country=None):
    if item.get("adult"):
        return False
    if cinema and media_type=='movie' and not has_cinema_release(item,cinema_country):
        return False
    if float(item.get("vote_average") or 0) < min_score:
        return False
    if not matches_result_filters(item, media_type, min_votes=min_votes, min_runtime=min_runtime, excluded_genres=excluded_genres):
        return False
    ids = set(item.get("genre_ids", [])) | {
        g["id"] for g in item.get("genres", []) if isinstance(g, dict) and g.get("id")
    }
    if not set(genres).issubset(ids):
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
        elif status == 'upcoming':
            if not (released > date.today().isoformat() or item.get('status') in STATUSES[status]):
                return False
        elif status in ('airing', 'returning'):
            from core.season_releases import current_airing_details
            active, _ = current_airing_details(item, ())
            if item.get('status') != 'Returning Series' or (released and released > date.today().isoformat()) or active != (status == 'airing'):
                return False
        elif item.get('status') not in STATUSES[status]:
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


def anime_visible(item, show_anime):
    if show_anime:
        return True
    from types import SimpleNamespace

    return not tracking_projection.is_anime(
        SimpleNamespace(
            tmdb_data={
                **item,
                "genres": [
                    {"name": GENRES.get(i, "")} for i in item.get("genre_ids", [])
                ]
                or item.get("genres", []),
            }
        )
    )


async def imdb_page(
    media_type, key, filters, page, *, mdblist_key=None, show_anime=True
):
    rows, stale = await mdblist_discovery.ranked_rows(
        media_type, filters["sort"], mdblist_key
    )
    semaphore = asyncio.Semaphore(6)
    needs_details = filters["provider"] or filters.get('status') in ('airing', 'returning') or (media_type=='movie' and filters.get('cinema'))
    get = tmdb.get_movie if media_type == "movie" else tmdb.get_show
    predicate = {k: v for k, v in filters.items() if k != "sort"}
    if needs_details and not key:
        raise ValueError("A TMDB key is needed for streaming filters")

    # Apply cached date/genre/status/adult/anime filters across the full feed first.
    basic = {**predicate, "provider": None, 'cinema':False}
    if filters.get('status') in ('airing', 'returning'):
        basic['status'] = ''  # The ranked feed lacks season/episode evidence.
    candidates = [
        row
        for row in rows
        if matches(row, media_type=media_type, **basic)
        and anime_visible(row, show_anime)
        and (filters.get('status') not in ('airing', 'returning') or row.get('status') in (None, 'Returning Series'))
    ]

    async def detail(row):
        async with semaphore:
            item = await get(row["id"], api_key=key)
            item = {**item, "imdb_votes": row["imdb_votes"]}
            if not matches(
                item, media_type=media_type, **predicate
            ) or not anime_visible(item, show_anime):
                return None
            return {
                **item,
                "imdb_id": row["imdb_id"],
                "imdb_score": row["imdb_score"],
                "imdb_votes": row["imdb_votes"],
            }

    if needs_details:
        # Scan in rank order until this page plus one matching title is found.
        # No empty intermediate pages, and no per-title MDBList requests.
        items = []
        target = page * PAGE_SIZE + 1
        for start in range(0, len(candidates), PAGE_SIZE):
            batch = await asyncio.gather(
                *(detail(row) for row in candidates[start : start + PAGE_SIZE])
            )
            items.extend(item for item in batch if item is not None)
            if len(items) >= target:
                break
        has_more = len(items) > page * PAGE_SIZE
        selected = items[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    else:
        has_more = len(candidates) > page * PAGE_SIZE
        selected = candidates[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    return {
        "has_more": has_more,
        "notice": "Showing cached MDBList rankings." if stale else None,
    }, selected


async def remote_page(
    term, media_type, key, filters, page, *, mdblist_key=None, show_anime=True, feed_offset=None
):
    # Imports reuse the same detail cache; at most six concurrent filtered-search lookups.
    from core.catalog_search import fuzzy_remote_terms, is_close_title_match

    kind = "movie" if media_type == "movie" else "tv"
    if not term and filters["sort"] in IMDB_SORTS and filters.get('status') != 'upcoming':
        return await imdb_page(
            media_type,
            key,
            filters,
            page,
            mdblist_key=mdblist_key,
            show_anime=show_anime,
        )
    if not key:
        raise ValueError("A TMDB key is needed for title search and discovery")
    if not term:
        data = await tmdb._get(
            f"{tmdb.TMDB_BASE}/discover/{kind}",
            headers=tmdb.get_headers(key),
            params=remote_params(media_type, page=page, **filters),
        )
        if (media_type=='movie' and filters.get('cinema')) or (media_type=='series' and filters.get('status') in ('airing', 'returning')):
            semaphore=asyncio.Semaphore(6)
            async def theatrical(item):
                async with semaphore:
                    get = tmdb.get_movie if media_type == 'movie' else tmdb.get_show
                    return await get(item['id'],api_key=key)
            items=await asyncio.gather(*(theatrical(item) for item in data.get('results',[])))
            predicate={k:v for k,v in filters.items() if k!='sort'}
            return data,[item for item in items if matches(item,media_type=media_type,**predicate)]
        return data, [item for item in data.get('results', []) if matches_result_filters(
            item, media_type, min_votes=filters.get('min_votes', 0), min_runtime=filters.get('min_runtime', 0), excluded_genres=filters.get('excluded_genres', ()))]
    search = tmdb.search_movies if media_type == "movie" else tmdb.search_shows
    data = await search(term, page=page, api_key=key) if page <= 500 else {'results': [], 'total_pages': 500}
    items = list(data.get("results", []))
    # Provider pages are small and filters/deduplication can leave only one
    # viable title. Fetch a bounded batch, including every page of short searches.
    last = min(page + (4 if len(term.strip()) >= 3 else 0), min(data.get('total_pages', 1), 500))
    if last > page:
        batches = await asyncio.gather(*(search(term, page=next_page, api_key=key) for next_page in range(page + 1, last + 1)))
        items.extend(item for batch in batches for item in batch.get('results', []))
    data = {**data, 'page': max(page, last), 'has_more': last < min(data.get('total_pages', 1), 500)}
    data['provider_more'] = data['has_more']
    if len(term.strip()) >= 3 and not items and page == 1:
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
        data = {"total_pages": 1, 'page': 1, 'has_more': False}
    items = list({item['id']: item for item in items}.values())
    from core.catalog_search import predictive_titles
    feed_offset = (0 if page == 1 else -1) if feed_offset is None else feed_offset
    data['feed_next'] = -1
    if mdblist_key and feed_offset >= 0:
        # Reuse the full public catalogue already used by browse rankings.
        # Keep normalization/fuzzy work in a worker and avoid per-title
        # remote searches or imports.
        try:
            feed, _ = await mdblist_discovery.ranked_rows(media_type, 'popular', mdblist_key)
            suggestions = await asyncio.to_thread(predictive_titles, feed, term)
            from core.catalog_search import title_match_rank
            suggestions.sort(key=lambda row: title_match_rank(term,row.get('title')))
            if len(suggestions) > feed_offset + PAGE_SIZE:
                data['feed_next'] = feed_offset + PAGE_SIZE
                data['has_more'] = True
            items = list({item['id']: item for item in [*suggestions[feed_offset:feed_offset+PAGE_SIZE], *items]}.values())
        except mdblist_discovery.DiscoveryUnavailable:
            pass  # TMDB and the indexed local catalogue remain usable.
    # TMDB also matches hidden translations. Keep predictive matches in the
    # displayed/original titles instead of flooding short searches with them.
    items = await asyncio.to_thread(predictive_titles, items, term)
    needs_details = filters["status"] or filters["provider"] or (media_type=='movie' and filters.get('cinema'))
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
    mdblist_key=None,
    prefetched=None,
    cursor=None,
    **filters,
):
    from core.browse_cursor import encode_search_cursor
    remote_position, local_position, feed_position = cursor or (page, (page-1)*PAGE_SIZE, 0 if page == 1 else -1)
    query = local_query(
        media_type, term=term, show_anime=show_anime, mdblist_key=mdblist_key, **filters
    )
    notice = None
    remote = None
    named_genre = any(isinstance(value, str) for value in filters.get("genres", []))
    if named_genre:
        source = "local"  # Provider-specific names cannot become TMDB genre IDs.
    imdb_sort = not term and filters["sort"] in IMDB_SORTS and filters.get('status') != 'upcoming'
    available = bool(mdblist_key) if imdb_sort else bool(key)
    if source == "remote" and not available:
        raise ValueError("The metadata key is no longer available")
    if source != "local" and available:
        try:
            if isinstance(prefetched, BaseException):
                raise prefetched
            remote, items = prefetched or await remote_page(
                term,
                media_type,
                key,
                filters,
                remote_position if term else page,
                mdblist_key=mdblist_key,
                show_anime=show_anime,
                feed_offset=feed_position,
            )
        except Exception:
            # Once a remote stream starts, don't silently change its pagination source.
            if source == "remote" or page > 1:
                raise
            notice = (
                "MDBList discovery is unavailable. Showing titles with cached IMDb data."
                if imdb_sort
                else "Discovery is unavailable. Showing the local catalogue."
            )
    elif not available and not named_genre:
        notice = (
            "Showing the local catalogue. Add an MDBList key in Settings to discover more."
            if imdb_sort
            else "Showing the local catalogue. Add a TMDB key in Settings to discover more."
        )
    if remote is None:
        # A feed may have been cached even when a metadata lookup failed.
        query = local_query(
            media_type,
            term=term,
            show_anime=show_anime,
            mdblist_key=mdblist_key,
            **filters,
        )
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
        notice = remote.get("notice")
        source = "remote"
        has_more = remote.get("has_more", page < min(remote.get("total_pages", 1), 500))
        # Each stream advances independently; provider scans cannot skip local
        # matches or force the entire cached catalogue into one response.
        local = (await db.execute(query.offset(local_position).limit(PAGE_SIZE+1))).scalars().all() if term and local_position >= 0 else []
        local_more = len(local) > PAGE_SIZE
        local = local[:PAGE_SIZE]
        has_more = has_more or local_more
        local_ids = {m.tmdb_id for m in local if m.tmdb_id}
        results = [tracking_projection.media_data(m) for m in local]
        seen = set(local_ids)
        for item in items:
            if (
                item.get("adult")
                or item["id"] in seen
                or not anime_visible(item, show_anime)
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
                    "release_date": item.get("release_date") or item.get("first_air_date") or None,
                    "year": (
                        item.get("release_date") or item.get("first_air_date") or ""
                    )[:4],
                    "tmdb_score": item.get("vote_average"),
                    "imdb_score": item.get("imdb_score"),
                    "imdb_id": item.get("imdb_id"),
                    "genres": [GENRES.get(i, "") for i in item.get("genre_ids", [])]
                        or [g.get('name','') for g in item.get('genres',[]) if isinstance(g,dict)],
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
        remote_metadata = {item['id']: item for item in items}
        known_metadata = {m.tmdb_id: known_result_metadata(remote_metadata.get(m.tmdb_id, {}), m) for m in known}
        results = [row for row in results if row['tmdb_id'] not in known_metadata or matches_result_filters(
            known_metadata[row['tmdb_id']], media_type, min_votes=filters.get('min_votes', 0),
            min_runtime=filters.get('min_runtime', 0), excluded_genres=filters.get('excluded_genres', ()))]
        mapping = {m.tmdb_id: m.id for m in known}
        for row in results:
            row["id"] = row.get("id") or mapping.get(row["tmdb_id"])
    for row in results:
        row.update(list_status=None, score=None, rating_mode="manual")
    from core.tracking_editor import attach_editor_context
    await attach_editor_context(db, viewer, results, media_rows=known if remote is not None else rows)
    if term:
        from core.catalog_search import title_match_rank
        results.sort(key=lambda row: title_match_rank(term, row.get('title')))
    return {
        "results": results,
        "page": page,
        "cursor": encode_search_cursor(
            remote.get('page',remote_position)+1 if remote.get('provider_more',False) else 501,
            local_position+PAGE_SIZE if local_more else -1,remote.get('feed_next',-1)
        ) if term and remote is not None and has_more else None,
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
