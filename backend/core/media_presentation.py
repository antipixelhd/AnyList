"""Shared media serialization, user state, and metadata enrichment."""

import asyncio
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import arr_settings, settings_store, tmdb
from core.episode_order import (
    get_order_keys_for_series,
    get_positions_for_series,
    is_aired_order,
)
from core.rewatch import get_active_rewatches_for_shows
from models.base import CollectionSource, MediaType
from models.collection import Collection, CollectionFile
from models.connections import MediaServerConnection
from models.episode_order import ShowEpisodePosition
from models.events import WatchEvent
from models.lists import List as UserList
from models.lists import ListItem
from models.media import Media
from models.media_request import MediaRequest, RequestStatus
from models.profile import UserProfileData
from models.ratings import Rating
from models.rewatch import RewatchProgress, ShowRewatch
from models.show import Show as ShowModel
from models.users import UserSettings

_TMDB_FANOUT_TIMEOUT = 6.0


def _attach_episode_order_fields(
    item: dict,
    order_keys: dict[int, str],
    order_positions: dict[tuple[int, int], "ShowEpisodePosition"],
) -> None:
    """Attaches show_episode_order + display_season_number/display_episode_number
    so every surface renders a show's episodes in the ordering the user picked
    for it (#174). A pure function over the already-batched lookups,
    deliberately DB-free so it's trivial to unit test without a session.

    `order_positions` is keyed `(series_tmdb_id, canonical_season,
    canonical_episode) -> ShowEpisodePosition` (flattened from
    get_positions_for_series).
    """
    t = item.get("type")
    if t == "series":
        series_id = item.get("tmdb_id")
    elif t == "episode":
        series_id = item.get("show_tmdb_id")
    else:
        return

    key = order_keys.get(series_id)
    if not key or is_aired_order(key):
        return
    item["show_episode_order"] = key

    season = item.get("season_number")
    episode = item.get("episode_number")
    # tvdb_sourced episodes (no TMDB counterpart at all) already store their
    # OWN season_number/episode_number as TVDB-native values, not canonical
    # ones - looking those up risks a coincidental wrong match. Skip them; the
    # frontend uses their season_number/episode_number directly in that case.
    if t == "episode" and season is not None and episode is not None and not item.get("tvdb_sourced"):
        pos = order_positions.get((series_id, season, episode))
        if pos:
            item["display_season_number"] = pos.display_season
            item["display_episode_number"] = pos.display_episode
    elif t == "series" and season is not None:
        # Season list item - no single episode, so pick a representative
        # display season from the lowest-numbered mapped episode in this
        # canonical season.
        candidates = sorted(
            (p for (sid, s, _e), p in order_positions.items() if sid == series_id and s == season),
            key=lambda p: p.tmdb_episode_number,
        )
        if candidates:
            item["display_season_number"] = candidates[0].display_season


async def enrich_with_state(
    db: AsyncSession,
    user_id: int,
    items: list[dict],
) -> list[dict]:
    """Add watched, in_lists, collection_pct, and is_monitored fields to a list of media items."""
    movie_tmdb_ids = [i["tmdb_id"] for i in items if i.get("type") == "movie" and i.get("tmdb_id")]
    show_tmdb_ids  = [i["tmdb_id"] for i in items if i.get("type") == "series" and i.get("tmdb_id")]
    ep_tmdb_ids    = [i["tmdb_id"] for i in items if i.get("type") == "episode" and i.get("tmdb_id")]
    all_tmdb_ids   = [i["tmdb_id"] for i in items if i.get("tmdb_id")]
    # A "series" item with season_number set is a season list item (see routers/lists.py) -
    # it shares its tmdb_id with the whole show, so its watch/collection state and list
    # membership need computing separately from the show's own, below.
    season_items = [
        (i["tmdb_id"], i["season_number"])
        for i in items
        if i.get("type") == "series" and i.get("season_number") is not None and i.get("tmdb_id")
    ]
    season_watched_count_map: dict[tuple[int, int], int] = {}
    season_collected_count_map: dict[tuple[int, int], int] = {}

    if not all_tmdb_ids:
        return items

    # --- Episode-order preference / display-position translation (#174) ---
    # A show's episode/season numbers should render in the ordering the user
    # picked for that show (DVD, absolute, a TMDB episode group, ...), on every
    # surface an episode appears. Every item needing this shares a show's
    # series_tmdb_id: a season/whole-show item's own tmdb_id, or an episode
    # item's show_tmdb_id.
    episode_order_series_ids = set(show_tmdb_ids) | {
        i["show_tmdb_id"] for i in items
        if i.get("type") == "episode" and i.get("show_tmdb_id")
    }
    order_keys: dict[int, str] = {}
    # Flat lookup: (series_tmdb_id, canonical_season, canonical_episode) -> position.
    order_positions: dict[tuple[int, int, int], ShowEpisodePosition] = {}
    if episode_order_series_ids:
        # Only shows actually on a non-aired order come back here - the common
        # case (nobody switched) stays at one indexed lookup and no more.
        order_keys = await get_order_keys_for_series(
            db, user_id, list(episode_order_series_ids)
        )
        if order_keys:
            by_pair = await get_positions_for_series(
                db, [(sid, key) for sid, key in order_keys.items()]
            )
            for (sid, _key), canon_map in by_pair.items():
                for (cs, ce), pos in canon_map.items():
                    order_positions[(sid, cs, ce)] = pos

    # --- Radarr / Sonarr state (Request button logic) ---
    settings_q = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    settings = settings_q.scalar_one_or_none()
    gs = await settings_store.get_global_settings(db)

    monitored_status = {} # tmdb_id -> bool
    request_enabled_map = {} # tmdb_id -> bool

    radarr_cfg = arr_settings._effective_radarr(settings, gs)
    sonarr_cfg = arr_settings._effective_sonarr(settings, gs)
    if radarr_cfg or sonarr_cfg:
        radarr_ready = radarr_cfg is not None
        sonarr_ready = sonarr_cfg is not None
        for item in items:
            tid = item.get("tmdb_id")
            t = item.get("type")
            if t == "movie": request_enabled_map[tid] = radarr_ready
            elif t == "series": request_enabled_map[tid] = sonarr_ready

    # Mark items already in Radarr/Sonarr (cached bulk lists); best-effort.
    if radarr_cfg and movie_tmdb_ids:
        from core import radarr as radarr_client

        radarr_ids = await radarr_client.get_all_movie_tmdb_ids(
            radarr_cfg.radarr_url, radarr_cfg.radarr_token
        )
        if radarr_ids:
            for tid in movie_tmdb_ids:
                if tid in radarr_ids:
                    monitored_status[tid] = True
    if sonarr_cfg and show_tmdb_ids:
        from core import sonarr as sonarr_client

        sonarr_ids = await sonarr_client.get_all_series_ids(
            sonarr_cfg.sonarr_url, sonarr_cfg.sonarr_token
        )
        if sonarr_ids:
            sonarr_tmdb, sonarr_tvdb = sonarr_ids
            # Sonarr pre-v4 has no tmdbId - fall back to TVDB ids of local shows.
            tvdb_map: dict[int, int] = {}
            if sonarr_tvdb:
                tvdb_q = await db.execute(
                    select(ShowModel.tmdb_id, ShowModel.tvdb_id).where(
                        ShowModel.tmdb_id.in_(show_tmdb_ids),
                        ShowModel.tvdb_id.isnot(None),
                    )
                )
                tvdb_map = {r[0]: r[1] for r in tvdb_q.all()}
            for tid in show_tmdb_ids:
                if tid in sonarr_tmdb or tvdb_map.get(tid) in sonarr_tvdb:
                    monitored_status[tid] = True

    # --- Pending/rejected request state ---
    request_status_map: dict[int, str] = {}
    if len(items) == 1:
        item = items[0]
        tid = item.get("tmdb_id")
        t   = item.get("type")
        if t in ("movie", "series") and tid:
            req_q = await db.execute(
                select(MediaRequest)
                .where(
                    MediaRequest.user_id == user_id,
                    MediaRequest.tmdb_id == tid,
                    MediaRequest.media_type == t,
                    MediaRequest.status.in_([RequestStatus.pending, RequestStatus.rejected]),
                )
                .order_by(MediaRequest.updated_at.desc())
                .limit(1)
            )
            req = req_q.scalar_one_or_none()
            if req:
                request_status_map[tid] = req.status.value

    # --- Watched state ---
    watched_movies: set[int] = set()
    if movie_tmdb_ids:
        q = await db.execute(
            select(Media.tmdb_id)
            .join(WatchEvent, WatchEvent.media_id == Media.id)
            .where(WatchEvent.user_id == user_id, WatchEvent.completed == True, Media.tmdb_id.in_(movie_tmdb_ids), Media.media_type == MediaType.movie)
            .distinct()
        )
        watched_movies = {r[0] for r in q.all()}

    watched_shows: set[int] = set()
    show_watched_count_map: dict[int, int] = {}
    # tmdb_id -> ShowRewatch, for shows currently mid-rewatch. Their watched
    # counts come from that rewatch's progress instead of full history below.
    show_active_rewatch_by_tmdb: dict[int, ShowRewatch] = {}
    if show_tmdb_ids:
        # Count distinct watched episodes per show, deduplicated by (season, episode).
        # Use a join on ShowModel by tmdb_id to group episodes by their show's TMDB ID.
        # This handles cases where multiple Show rows might exist for the same TMDB ID.
        # Count distinct watched episodes per show, deduplicated by (season, episode).
        # We need to find all watched episodes for these shows.
        # Most episodes will be linked via Media.show_id -> ShowModel.id -> ShowModel.tmdb_id.
        # But some might have a null show_id. We can find those by matching their TMDB ID
        # if we know which episode TMDB IDs belong to which show.
        # To keep it efficient and avoid extra TMDB lookups, let's use the show_id join
        # but also allow matching by show_id directly if we have the local Show IDs.

        # 1. Get local Show IDs for the TMDB IDs we are interested in.
        show_id_map_q = await db.execute(
            select(ShowModel.tmdb_id, ShowModel.id)
            .where(ShowModel.tmdb_id.in_(show_tmdb_ids))
        )
        show_tmdb_to_local_id = {r[0]: r[1] for r in show_id_map_q.all()}
        local_show_ids = list(show_tmdb_to_local_id.values())

        active_rewatches_by_show_id = await get_active_rewatches_for_shows(db, user_id, local_show_ids)
        show_active_rewatch_by_tmdb = {
            tid: active_rewatches_by_show_id[lid]
            for tid, lid in show_tmdb_to_local_id.items()
            if lid in active_rewatches_by_show_id
        }
        non_rewatching_tmdb_ids = [t for t in show_tmdb_ids if t not in show_active_rewatch_by_tmdb]

        if non_rewatching_tmdb_ids:
            watched_eps_sq = (
                select(ShowModel.tmdb_id.label("show_tmdb_id"), Media.season_number, Media.episode_number)
                .join(WatchEvent, WatchEvent.media_id == Media.id)
                .join(ShowModel, ShowModel.id == Media.show_id)
                .where(
                    WatchEvent.user_id == user_id,
                    WatchEvent.completed == True,
                    Media.media_type == MediaType.episode,
                    Media.season_number.isnot(None),
                    Media.season_number != 0,
                    Media.episode_number.isnot(None),
                    ShowModel.tmdb_id.in_(non_rewatching_tmdb_ids),
                )
                .group_by(ShowModel.tmdb_id, Media.season_number, Media.episode_number)
                .subquery()
            )
            watched_count_q = await db.execute(
                select(watched_eps_sq.c.show_tmdb_id, func.count())
                .group_by(watched_eps_sq.c.show_tmdb_id)
            )
            show_watched_count_map = {r[0]: r[1] for r in watched_count_q.all()}

        # 2. Add episodes that might have a null show_id but are watched.
        # This is harder without knowing episode TMDB IDs.
        # But if the user marked them watched via Scrob, they SHOULD have show_id set.
        # Let's check if there are any episodes with null show_id that belong to these shows.
        # Actually, let's just make the existing logic more robust by ensuring show_id is set
        # when marking as watched (which we already do in history.py).

        # 3. Shows currently mid-rewatch: watched count comes from that
        # rewatch's progress, not full history.
        if show_active_rewatch_by_tmdb:
            rewatch_id_to_tmdb = {r.id: tid for tid, r in show_active_rewatch_by_tmdb.items()}
            progress_count_q = await db.execute(
                select(RewatchProgress.rewatch_id, func.count(func.distinct(RewatchProgress.media_id)))
                .where(RewatchProgress.rewatch_id.in_(rewatch_id_to_tmdb.keys()))
                .group_by(RewatchProgress.rewatch_id)
            )
            for rewatch_id, count in progress_count_q.all():
                show_watched_count_map[rewatch_id_to_tmdb[rewatch_id]] = count

    watched_episodes: set[int] = set()
    if ep_tmdb_ids:
        ep_show_q = await db.execute(
            select(Media.tmdb_id, Media.id, Media.show_id)
            .where(Media.tmdb_id.in_(ep_tmdb_ids), Media.media_type == MediaType.episode)
        )
        ep_rows = ep_show_q.all()
        ep_show_ids = [row[2] for row in ep_rows if row[2] is not None]
        active_rewatches_by_show = await get_active_rewatches_for_shows(db, user_id, ep_show_ids)

        rewatching_media_ids = {row[1] for row in ep_rows if row[2] in active_rewatches_by_show}
        non_rewatching_tmdb_ids = [row[0] for row in ep_rows if row[2] not in active_rewatches_by_show]

        if non_rewatching_tmdb_ids:
            q = await db.execute(
                select(Media.tmdb_id)
                .join(WatchEvent, WatchEvent.media_id == Media.id)
                .where(WatchEvent.user_id == user_id, WatchEvent.completed == True, Media.tmdb_id.in_(non_rewatching_tmdb_ids), Media.media_type == MediaType.episode)
                .distinct()
            )
            watched_episodes = {r[0] for r in q.all()}

        if rewatching_media_ids:
            media_id_to_tmdb = {row[1]: row[0] for row in ep_rows}
            progress_q = await db.execute(
                select(RewatchProgress.media_id).where(RewatchProgress.media_id.in_(rewatching_media_ids))
            )
            watched_episodes |= {media_id_to_tmdb[r[0]] for r in progress_q.all()}

    # --- List membership ---
    user_list_ids_q = await db.execute(select(UserList.id).where(UserList.user_id == user_id))
    user_list_ids = [r[0] for r in user_list_ids_q.all()]

    # Split by season_number so a season-only list item doesn't make the whole
    # show (or vice versa) appear "in that list" elsewhere in the app.
    list_membership: dict[tuple[str, int], list[int]] = {}
    season_list_membership: dict[tuple[int, int], list[int]] = {}
    if user_list_ids and all_tmdb_ids:
        q = await db.execute(
            select(Media.tmdb_id, Media.media_type, ListItem.season_number, ListItem.list_id)
            .join(ListItem, ListItem.media_id == Media.id)
            .where(ListItem.list_id.in_(user_list_ids), Media.tmdb_id.in_(all_tmdb_ids))
            .distinct()
        )
        for row_tmdb_id, row_media_type, row_season_number, list_id in q.all():
            if row_season_number is None:
                list_membership.setdefault((row_media_type.value, row_tmdb_id), []).append(list_id)
            else:
                season_list_membership.setdefault((row_tmdb_id, row_season_number), []).append(list_id)

    # --- Collection pct and watched status for shows ---
    show_pct: dict[int, int] = {}
    show_aired_count: dict[int, int] = {}
    if show_tmdb_ids:
        # Total episodes from TMDB metadata.
        # Check local DB first for existing show rows.
        shows_meta_q = await db.execute(
            select(ShowModel.tmdb_id, ShowModel.tmdb_data, ShowModel.status)
            .where(ShowModel.tmdb_id.in_(show_tmdb_ids))
        )
        total_map: dict[int, int] = {}
        show_status_map: dict[int, str] = {}
        show_seasons_map: dict[int, list] = {}

        for show_tmdb_id, tmdb_data, status in shows_meta_q.all():
            seasons = (tmdb_data or {}).get("seasons", [])
            show_status_map[show_tmdb_id] = status or ""
            show_seasons_map[show_tmdb_id] = seasons
            total_map[show_tmdb_id] = sum(
                s.get("episode_count", 0) for s in seasons if s.get("season_number", 0) != 0
            )

        # 2. For shows not in local DB (or to ensure accuracy), fetch details from TMDB
        missing_show_ids = [tid for tid in show_tmdb_ids if tid not in total_map]

        # We also need to get ALL episode TMDB IDs for these shows to correctly identify
        # watched episodes that might not have a show_id link.
        # This is expensive, so we only do it if the user has watched episodes.
        tmdb_key = await settings_store.get_user_tmdb_key(db, user_id)
        if show_tmdb_ids and settings_store.check_tmdb_key(tmdb_key):
            async def fetch_show_and_seasons(tid: int):
                try:
                    data = await tmdb.get_show(tid, api_key=tmdb_key)
                    ep_ids = set()
                    # Keep fan-out bounded; linked local episodes supply watch counts.
                    return tid, data, ep_ids
                except Exception:
                    return tid, None, set()

            if missing_show_ids:
                try:
                    missing_results = await asyncio.wait_for(
                        asyncio.gather(*[fetch_show_and_seasons(tid) for tid in missing_show_ids]),
                        timeout=_TMDB_FANOUT_TIMEOUT,
                    )
                except (asyncio.TimeoutError, tmdb.TMDBUnavailable):
                    missing_results = []
                for tid, data, _ in missing_results:
                    if data:
                        seasons = data.get("seasons", [])
                        show_status_map[tid] = data.get("status", "")
                        show_seasons_map[tid] = seasons
                        total_map[tid] = sum(
                            s.get("episode_count", 0) for s in seasons if s.get("season_number", 0) != 0
                        )

        # Count distinct watched episodes per show, deduplicated by (season, episode).
        # We find episodes by their show_id link.
        # Primary path: show_id -> ShowModel.id -> ShowModel.tmdb_id
        # Recomputed here from scratch (duplicates the "Watched state" block
        # above) - shows mid-rewatch must stay excluded and keep the
        # progress-based counts computed there, not be overwritten with
        # full-history counts.
        if non_rewatching_tmdb_ids:
            watched_eps_sq = (
                select(ShowModel.tmdb_id.label("show_tmdb_id"), Media.season_number, Media.episode_number)
                .join(WatchEvent, WatchEvent.media_id == Media.id)
                .join(ShowModel, ShowModel.id == Media.show_id)
                .where(
                    WatchEvent.user_id == user_id,
                    WatchEvent.completed == True,
                    Media.media_type == MediaType.episode,
                    Media.season_number.isnot(None),
                    Media.season_number != 0,
                    Media.episode_number.isnot(None),
                    ShowModel.tmdb_id.in_(non_rewatching_tmdb_ids),
                )
                .group_by(ShowModel.tmdb_id, Media.season_number, Media.episode_number)
                .subquery()
            )
            watched_count_q = await db.execute(
                select(watched_eps_sq.c.show_tmdb_id, func.count())
                .group_by(watched_eps_sq.c.show_tmdb_id)
            )
            show_watched_count_map.update({r[0]: r[1] for r in watched_count_q.all()})

            # Season-scoped watched counts, from the same dedup subquery. Shows
            # currently mid-rewatch are excluded here too (they're excluded from
            # non_rewatching_tmdb_ids above), so a season of a show being rewatched
            # will under-count until the rewatch finishes - a known limitation.
            if season_items:
                season_watched_q = await db.execute(
                    select(watched_eps_sq.c.show_tmdb_id, watched_eps_sq.c.season_number, func.count())
                    .group_by(watched_eps_sq.c.show_tmdb_id, watched_eps_sq.c.season_number)
                )
                season_watched_count_map = {(r[0], r[1]): r[2] for r in season_watched_q.all()}

        # Count distinct collected episodes per show
        ep_dedup_sq = (
            select(ShowModel.tmdb_id.label("show_tmdb_id"), Media.season_number, Media.episode_number)
            .join(Collection, Collection.media_id == Media.id)
            .join(ShowModel, ShowModel.id == Media.show_id)
            .where(
                Collection.user_id == user_id,
                Media.media_type == MediaType.episode,
                Media.season_number.isnot(None),
                Media.season_number != 0,
                Media.episode_number.isnot(None),
                ShowModel.tmdb_id.in_(show_tmdb_ids),
            )
            .group_by(ShowModel.tmdb_id, Media.season_number, Media.episode_number)
            .subquery()
        )
        collected_q = await db.execute(
            select(ep_dedup_sq.c.show_tmdb_id, func.count())
            .group_by(ep_dedup_sq.c.show_tmdb_id)
        )
        collected_map = {r[0]: r[1] for r in collected_q.all()}

        if season_items:
            season_collected_q = await db.execute(
                select(ep_dedup_sq.c.show_tmdb_id, ep_dedup_sq.c.season_number, func.count())
                .group_by(ep_dedup_sq.c.show_tmdb_id, ep_dedup_sq.c.season_number)
            )
            season_collected_count_map = {(r[0], r[1]): r[2] for r in season_collected_q.all()}

        for tmdb_id in show_tmdb_ids:
            total = total_map.get(tmdb_id, 0)
            collected = collected_map.get(tmdb_id, 0)
            show_pct[tmdb_id] = min(100, int((collected / total) * 100)) if total > 0 else 0

        # --- Aired counts for 'watched' logic ---
        show_aired_count = {tid: total_map.get(tid, 0) for tid in show_tmdb_ids}

        # For shows between 0–100% that are still active (not Ended/Canceled), the stored
        # episode_count includes unaired episodes. Make parallelised live TMDB calls to get
        # last_episode_to_air and calculate against actually-aired episodes only.
        # If a caller already has last_episode_to_air (e.g. detail page), it can pre-populate
        # _last_episode_to_air on the item to skip the redundant fetch.
        prefetched: dict[int, dict] = {
            item["tmdb_id"]: item["_last_episode_to_air"]
            for item in items
            if item.get("type") == "series" and item.get("_last_episode_to_air")
        }
        FINAL_STATUSES = {"Ended", "Canceled"}
        needs_live_call = [
            tid for tid in show_tmdb_ids
            if tid not in prefetched
            and (0 < show_pct.get(tid, 0) < 100 or 0 < show_watched_count_map.get(tid, 0))
            and show_status_map.get(tid, "") not in FINAL_STATUSES
        ]
        if needs_live_call:
            tmdb_key = await settings_store.get_user_tmdb_key(db, user_id)
            if settings_store.check_tmdb_key(tmdb_key):
                async def fetch_last_aired(tid: int) -> tuple[int, dict | None]:
                    try:
                        return tid, await tmdb.get_show_light(tid, api_key=tmdb_key)
                    except Exception:
                        return tid, None

                try:
                    live_results = await asyncio.wait_for(
                        asyncio.gather(*[fetch_last_aired(tid) for tid in needs_live_call]),
                        timeout=_TMDB_FANOUT_TIMEOUT,
                    )
                except (asyncio.TimeoutError, tmdb.TMDBUnavailable):
                    live_results = []
                for tid, data in live_results:
                    if data and data.get("last_episode_to_air"):
                        prefetched[tid] = data["last_episode_to_air"]

        for tid, last_ep in prefetched.items():
            if not last_ep:
                continue
            last_season = last_ep.get("season_number", 0)
            last_ep_num = last_ep.get("episode_number", 0)
            seasons = show_seasons_map.get(tid, [])
            # Sum completed seasons before the current airing season, plus episodes aired so far in it.
            aired_total = sum(
                s.get("episode_count", 0)
                for s in seasons
                if 0 < s.get("season_number", 0) < last_season
            ) + last_ep_num
            show_aired_count[tid] = aired_total
            collected = collected_map.get(tid, 0)
            show_pct[tid] = min(100, int((collected / aired_total) * 100)) if aired_total > 0 else 0

        # Now we can accurately set watched_shows
        watched_shows = {
            tid for tid in show_tmdb_ids
            if show_watched_count_map.get(tid, 0) > 0
            and show_watched_count_map.get(tid, 0) >= show_aired_count.get(tid, 0)
        }

    # --- Collection state for movies/episodes ---
    collected_movie_ids: set[int] = set()
    if movie_tmdb_ids:
        coll_q = await db.execute(
            select(Media.tmdb_id)
            .join(Collection, Collection.media_id == Media.id)
            .where(Collection.user_id == user_id, Media.tmdb_id.in_(movie_tmdb_ids), Media.media_type == MediaType.movie)
            .distinct()
        )
        collected_movie_ids = {r[0] for r in coll_q.all()}

    collected_ep_ids: set[int] = set()
    if ep_tmdb_ids:
        coll_q = await db.execute(
            select(Media.tmdb_id)
            .join(Collection, Collection.media_id == Media.id)
            .where(Collection.user_id == user_id, Media.tmdb_id.in_(ep_tmdb_ids), Media.media_type == MediaType.episode)
            .distinct()
        )
        collected_ep_ids = {r[0] for r in coll_q.all()}

    # --- User ratings ---
    # Only fetch show/movie-level ratings (season_number IS NULL); season-specific ratings
    # are fetched separately in the show detail endpoints.
    user_ratings: dict[tuple, float] = {}
    if all_tmdb_ids:
        ratings_q = await db.execute(
            select(Media.tmdb_id, Media.media_type, func.max(Rating.rating))
            .join(Rating, Rating.media_id == Media.id)
            .where(
                Rating.user_id == user_id,
                Media.tmdb_id.in_(all_tmdb_ids),
                Rating.season_number.is_(None),
            )
            .group_by(Media.tmdb_id, Media.media_type)
        )
        for tmdb_id, media_type, rating_val in ratings_q.all():
            user_ratings[(tmdb_id, media_type.value)] = rating_val

    # --- Play count (detail view only) ---
    play_count_map: dict[int, int] = {}
    if len(items) == 1:
        item0 = items[0]
        tid0 = item0.get("tmdb_id")
        t0 = item0.get("type")
        if tid0 and t0 in ("movie", "episode"):
            mt0 = MediaType.movie if t0 == "movie" else MediaType.episode
            pc_q = await db.execute(
                select(func.count(WatchEvent.id))
                .join(Media, Media.id == WatchEvent.media_id)
                .where(
                    WatchEvent.user_id == user_id,
                    Media.tmdb_id == tid0,
                    Media.media_type == mt0,
                )
            )
            play_count_map[tid0] = pc_q.scalar() or 0

    # --- Apply to items ---
    for item in items:
        tid = item.get("tmdb_id")
        t = item.get("type")
        if t == "movie":
            item["watched"] = tid in watched_movies
            in_lib = tid in collected_movie_ids
            item["in_library"] = in_lib
            item["collection_pct"] = 100 if in_lib else 0
        elif t == "series" and item.get("season_number") is not None:
            # A season list item - state scoped to just that season's episodes,
            # not the whole show (see season_items above).
            sn = item["season_number"]
            season_total = next(
                (s.get("episode_count", 0) for s in show_seasons_map.get(tid, []) if s.get("season_number") == sn),
                0,
            )
            collected = season_collected_count_map.get((tid, sn), 0)
            watched = season_watched_count_map.get((tid, sn), 0)
            item["collection_pct"] = min(100, int((collected / season_total) * 100)) if season_total > 0 else 0
            item["in_library"] = collected > 0
            item["watch_pct"] = min(100, int((watched / season_total) * 100)) if season_total > 0 else 0
            item["watched"] = season_total > 0 and watched >= season_total
            item["watch_started"] = watched > 0
        elif t == "series":
            item["watched"] = tid in watched_shows
            pct = show_pct.get(tid, 0)
            item["collection_pct"] = pct
            item["in_library"] = pct > 0
            _w = show_watched_count_map.get(tid, 0)
            _a = show_aired_count.get(tid, 0)
            item["watch_pct"] = min(100, int((_w / _a) * 100)) if _a > 0 else 0
            # watch_pct rounds down to 0 for e.g. 1 watched out of 200+ episodes -
            # this is the exact boolean the "watched" state pill needs, so it
            # doesn't have to (mis)infer "any progress" from a rounded percentage.
            item["watch_started"] = _w > 0
        elif t == "episode":
            item["watched"] = tid in watched_episodes
            in_lib = tid in collected_ep_ids
            item["in_library"] = in_lib
            item["collection_pct"] = 100 if in_lib else 0
        else:
            item["watched"] = False
            item["collection_pct"] = 0
            item["in_library"] = False

        if t == "series" and item.get("season_number") is not None:
            item["in_lists"] = season_list_membership.get((tid, item["season_number"]), [])
        else:
            item["in_lists"] = list_membership.get((t, tid), [])
        item["is_monitored"] = monitored_status.get(tid, False)
        item["request_enabled"] = request_enabled_map.get(tid, False)
        item["request_status"] = request_status_map.get(tid)
        item["user_rating"] = user_ratings.get((tid, t))
        item["play_count"] = play_count_map.get(tid, 0)
        _attach_episode_order_fields(item, order_keys, order_positions)

    return items


async def require_anon_nav_allowed(db: AsyncSession) -> None:
    """Raise 401 unless the admin has enabled logged-out navigation and a
    global TMDB key is set. Called by read-only detail/list endpoints when
    their optional-auth dependency resolves to no user - re-checked here
    since these endpoints are reachable directly, not just through a page
    the frontend middleware already gated."""
    gs = await settings_store.get_global_settings(db)
    if not (gs and gs.enable_logged_out_navigation and settings_store.get_server_tmdb_key(gs)):
        raise HTTPException(status_code=401, detail="Not authenticated")


def _extract_movie_certification(data: dict, country: str = "US") -> str | None:
    for entry in data.get("release_dates", {}).get("results", []):
        if entry.get("iso_3166_1") == country:
            for rd in entry.get("release_dates", []):
                cert = rd.get("certification", "").strip()
                if cert:
                    return cert
    return None


def _extract_movie_release_dates(data: dict, country: str = "US") -> dict:
    results = data.get("release_dates", {}).get("results", [])
    us_entry = next((e for e in results if e.get("iso_3166_1") == country), None)
    digital = physical = None
    if us_entry:
        for rd in us_entry.get("release_dates", []):
            t = rd.get("type")
            d = (rd.get("release_date") or "")[:10] or None
            if t == 4 and not digital:
                digital = d
            elif t == 5 and not physical:
                physical = d
    return {"digital": digital, "physical": physical}


def _extract_show_content_rating(data: dict, country: str = "US") -> str | None:
    for entry in data.get("content_ratings", {}).get("results", []):
        if entry.get("iso_3166_1") == country:
            rating = entry.get("rating", "").strip()
            if rating:
                return rating
    return None


def format_media(media: Media) -> dict:
    from core.enrichment import is_unmapped_tvdb_episode

    cast = []
    raw_cast = (media.tmdb_data or {}).get("cast", [])
    for c in raw_cast:
        cast.append(
            {
                "tmdb_id": c.get("id"),
                "name": c.get("name"),
                "character": c.get("character"),
                "profile_path": tmdb.poster_url(c.get("profile_path"))
                if c.get("profile_path")
                else None,
            }
        )

    return {
        "id": media.id,
        "tmdb_id": media.tmdb_id,
        "tvdb_id": media.tvdb_id,
        "imdb_id": media.imdb_id,
        "type": media.media_type,
        "title": media.title,
        "original_title": media.original_title,
        "overview": media.overview,
        "poster_path": media.poster_path,
        "backdrop_path": media.backdrop_path,
        "release_date": media.release_date,
        "runtime": media.runtime,
        "tmdb_rating": media.tmdb_rating,
        "tagline": media.tagline,
        "status": media.status,
        "season_number": media.season_number,
        "episode_number": media.episode_number,
        "show_title": media.show.title if media.show else None,
        "show_tmdb_id": media.show.tmdb_id if media.show else None,
        "show_tvdb_id": media.show.tvdb_id if media.show else None,
        "show_poster_path": media.show.poster_path if media.show else None,
        "show_backdrop_path": media.show.backdrop_path if media.show else None,
        "tvdb_sourced": is_unmapped_tvdb_episode(media),
        "genres": (media.tmdb_data or {}).get("genres", []),
        "cast": cast[:12],
        "collection": (media.tmdb_data or {}).get("collection"),
        "adult": media.adult,
        "has_mid_credits_scene": (media.tmdb_data or {}).get("has_mid_credits_scene", False),
        "has_post_credits_scene": (media.tmdb_data or {}).get("has_post_credits_scene", False),
    }


async def refresh_technical_data(db: AsyncSession, media_ids: list[int], user_id: int) -> None:
    """For every CollectionFile the user has for the given media IDs, fetch fresh
    technical data (resolution, codecs, languages) from Plex, Jellyfin, or Emby.
    Manual entries are upgraded to the real source by searching all connections."""
    import core.emby as emby_client
    import core.jellyfin as jellyfin_client
    import core.plex as plex_client
    from models.show import Show as ShowModel

    # Load all connections for this user, grouped by type
    conns_result = await db.execute(
        select(MediaServerConnection).where(MediaServerConnection.user_id == user_id)
    )
    all_conns = conns_result.scalars().all()
    conns_by_id: dict[int, MediaServerConnection] = {c.id: c for c in all_conns}
    plex_conns    = [c for c in all_conns if c.type == "plex"]
    jellyfin_conns = [c for c in all_conns if c.type == "jellyfin"]
    emby_conns    = [c for c in all_conns if c.type == "emby"]

    if not all_conns:
        return

    files_result = await db.execute(
        select(CollectionFile, Collection, Media)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .join(Media, Media.id == Collection.media_id)
        .where(
            Collection.user_id == user_id,
            Collection.media_id.in_(media_ids),
        )
    )
    rows = files_result.all()
    if not rows:
        return

    # Pre-load shows for any episode rows
    show_ids = {media.show_id for _, _, media in rows if media.show_id is not None}
    show_tmdb_map: dict[int, int] = {}
    if show_ids:
        shows_result = await db.execute(select(ShowModel).where(ShowModel.id.in_(show_ids)))
        for s in shows_result.scalars().all():
            show_tmdb_map[s.id] = s.tmdb_id

    for cf, coll, media in rows:
        quality: dict = {}
        new_source: Optional[CollectionSource] = None
        new_source_id: Optional[str] = None
        new_connection_id: Optional[int] = None

        # Resolve the connection for this file (non-manual sources have connection_id set)
        conn = conns_by_id.get(cf.connection_id) if cf.connection_id else None

        if cf.source == CollectionSource.plex and conn and cf.source_id:
            item = await plex_client.get_item(conn.url, conn.token, cf.source_id)
            if item:
                quality = plex_client.extract_quality(item.get("Media", []))

        elif cf.source in (CollectionSource.jellyfin, CollectionSource.emby) and conn and cf.source_id:
            client_mod = jellyfin_client if cf.source == CollectionSource.jellyfin else emby_client
            item = await client_mod.get_item(conn.url, conn.token, cf.source_id, user_id=conn.server_user_id)
            if item:
                quality = client_mod.extract_quality(item.get("MediaStreams", []))
                if not quality.get("file_path") and item.get("Path"):
                    quality["file_path"] = item["Path"]

        elif cf.source == CollectionSource.manual and media.tmdb_id:
            # Try to find the item across all connections by TMDB metadata
            item = None
            if media.media_type == MediaType.movie:
                for c in plex_conns:
                    item = await plex_client.find_movie_by_tmdb_id(c.url, c.token, media.tmdb_id)
                    if item:
                        new_source = CollectionSource.plex
                        new_source_id = str(item.get("ratingKey", ""))
                        new_connection_id = c.id
                        quality = plex_client.extract_quality(item.get("Media", []))
                        break
                if not item:
                    for c in jellyfin_conns:
                        item = await jellyfin_client.find_movie_by_tmdb_id(c.url, c.token, media.tmdb_id, user_id=c.server_user_id)
                        if item:
                            new_source = CollectionSource.jellyfin
                            new_source_id = item.get("Id", "")
                            new_connection_id = c.id
                            quality = jellyfin_client.extract_quality(item.get("MediaStreams", []))
                            if not quality.get("file_path") and item.get("Path"):
                                quality["file_path"] = item["Path"]
                            break
                if not item:
                    for c in emby_conns:
                        item = await emby_client.find_movie_by_tmdb_id(c.url, c.token, media.tmdb_id, user_id=c.server_user_id)
                        if item:
                            new_source = CollectionSource.emby
                            new_source_id = item.get("Id", "")
                            new_connection_id = c.id
                            quality = emby_client.extract_quality(item.get("MediaStreams", []))
                            if not quality.get("file_path") and item.get("Path"):
                                quality["file_path"] = item["Path"]
                            break

            elif media.media_type == MediaType.episode and media.season_number is not None and media.episode_number is not None:
                series_tmdb_id = show_tmdb_map.get(media.show_id) if media.show_id else None
                if series_tmdb_id:
                    for c in plex_conns:
                        item = await plex_client.find_episode_by_ids(
                            c.url, c.token, series_tmdb_id, media.season_number, media.episode_number,
                        )
                        if item:
                            new_source = CollectionSource.plex
                            new_source_id = str(item.get("ratingKey", ""))
                            new_connection_id = c.id
                            quality = plex_client.extract_quality(item.get("Media", []))
                            break
                    if not item:
                        for c in jellyfin_conns:
                            item = await jellyfin_client.find_episode_by_ids(
                                c.url, c.token, series_tmdb_id, media.season_number, media.episode_number,
                                user_id=c.server_user_id,
                            )
                            if item:
                                new_source = CollectionSource.jellyfin
                                new_source_id = item.get("Id", "")
                                new_connection_id = c.id
                                quality = jellyfin_client.extract_quality(item.get("MediaStreams", []))
                                if not quality.get("file_path") and item.get("Path"):
                                    quality["file_path"] = item["Path"]
                                break
                    if not item:
                        for c in emby_conns:
                            item = await emby_client.find_episode_by_ids(
                                c.url, c.token, series_tmdb_id, media.season_number, media.episode_number,
                                user_id=c.server_user_id,
                            )
                            if item:
                                new_source = CollectionSource.emby
                                new_source_id = item.get("Id", "")
                                new_connection_id = c.id
                                quality = emby_client.extract_quality(item.get("MediaStreams", []))
                                if not quality.get("file_path") and item.get("Path"):
                                    quality["file_path"] = item["Path"]
                                break

        if not quality.get("resolution"):
            continue

        # Upgrade manual entry to the real source so future syncs work
        if new_source and new_source_id:
            cf.source = new_source
            cf.source_id = new_source_id
        if new_connection_id:
            cf.connection_id = new_connection_id

        if quality.get("resolution"):    cf.resolution         = quality["resolution"]
        if quality.get("video_codec"):   cf.video_codec        = quality["video_codec"]
        if quality.get("audio_codec"):   cf.audio_codec        = quality["audio_codec"]
        if quality.get("audio_channels"): cf.audio_channels    = quality["audio_channels"]
        if quality.get("audio_languages") is not None: cf.audio_languages    = quality["audio_languages"]
        if quality.get("subtitle_languages") is not None: cf.subtitle_languages = quality["subtitle_languages"]
        if quality.get("file_path"):     cf.file_path          = quality["file_path"]


async def get_where_to_watch(
    db: AsyncSession,
    user_id: int,
    tmdb_id: int,
    media_type: MediaType,
    media: "Media | None" = None,
    show: "ShowModel | None" = None,
    tmdb_key: str | None = None,
) -> list[dict]:
    """Return a deduplicated list of local servers and streaming services where this title is available."""
    sources: list[dict] = []
    seen_names: set[str] = set()

    def _add(entry: dict) -> None:
        key = (entry["type"], entry["name"])
        if key not in seen_names:
            seen_names.add(key)
            sources.append(entry)

    # ── Local media servers ───────────────────────────────────────────────────
    if media_type == MediaType.movie:
        # Query across ALL Media rows for this tmdb_id so that a manually-matched
        # movie whose CollectionFile lives on a different row is still found.
        all_media_ids_q = await db.execute(
            select(Media.id)
            .join(Collection, Collection.media_id == Media.id)
            .where(
                Media.tmdb_id == tmdb_id,
                Media.media_type == MediaType.movie,
                Collection.user_id == user_id,
            )
        )
        all_media_ids = [r[0] for r in all_media_ids_q.all()]
        if all_media_ids:
            files_q = await db.execute(
                select(CollectionFile, MediaServerConnection)
                .join(Collection, Collection.id == CollectionFile.collection_id)
                .outerjoin(MediaServerConnection, MediaServerConnection.id == CollectionFile.connection_id)
                .where(Collection.media_id.in_(all_media_ids), Collection.user_id == user_id)
            )
            for cf, conn in files_q.all():
                name = conn.name if conn else cf.source.value.title()
                _add({"type": cf.source.value, "name": name, "logo": None})

    elif media_type == MediaType.series and show:
        files_q = await db.execute(
            select(CollectionFile.connection_id, CollectionFile.source, MediaServerConnection.name)
            .distinct()
            .join(Collection, Collection.id == CollectionFile.collection_id)
            .join(Media, Media.id == Collection.media_id)
            .outerjoin(MediaServerConnection, MediaServerConnection.id == CollectionFile.connection_id)
            .where(
                Media.show_id == show.id,
                Collection.user_id == user_id,
                Media.media_type == MediaType.episode,
            )
        )
        for _cid, src, conn_name in files_q.all():
            name = conn_name if conn_name else src.value.title()
            _add({"type": src.value, "name": name, "logo": None})

    # ── TMDB streaming providers ──────────────────────────────────────────────
    if tmdb_key and settings_store.check_tmdb_key(tmdb_key):
        try:
            profile_q = await db.execute(
                select(UserProfileData).where(UserProfileData.user_id == user_id)
            )
            profile = profile_q.scalar_one_or_none()
            country = (profile.country if profile and profile.country else None) or "US"
            user_streaming_ids = (
                {int(s) for s in (profile.streaming_services or [])} if profile else set()
            )

            if media_type == MediaType.movie:
                providers_data = await tmdb.get_movie_watch_providers(tmdb_id, api_key=tmdb_key)
            else:
                providers_data = await tmdb.get_show_watch_providers(tmdb_id, api_key=tmdb_key)

            country_data = (providers_data.get("results") or {}).get(country, {})
            for p in country_data.get("flatrate", []):
                pid = p.get("provider_id")
                if user_streaming_ids and pid not in user_streaming_ids:
                    continue
                logo = tmdb.poster_url(p.get("logo_path"), size="w92") if p.get("logo_path") else None
                _add({"type": "streaming", "name": p.get("provider_name"), "logo": logo})
        except Exception:
            pass

    return sources


async def _dropped_tmdb_ids(db: AsyncSession, user_id: int) -> tuple[set[int], set[int]]:
    """Dropped movie/show tmdb_ids for a user, so any recommendation surface
    can exclude them (#117) - dropped_movies/dropped_shows store local ids,
    so this resolves them to the tmdb_id space recommendation results live in."""
    settings_q = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    settings = settings_q.scalar_one_or_none()
    if not settings:
        return set(), set()

    movie_ids: set[int] = set()
    if settings.dropped_movies:
        q = await db.execute(select(Media.tmdb_id).where(Media.id.in_(settings.dropped_movies), Media.tmdb_id.isnot(None)))
        movie_ids = {row[0] for row in q.all()}

    show_ids: set[int] = set()
    if settings.dropped_shows:
        q = await db.execute(select(ShowModel.tmdb_id).where(ShowModel.id.in_(settings.dropped_shows), ShowModel.tmdb_id.isnot(None)))
        show_ids = {row[0] for row in q.all()}

    return movie_ids, show_ids
