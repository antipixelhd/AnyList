"""Media server synchronization, collection delivery, and repair jobs."""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Literal

from dateutil import parser
from sqlalchemy import DateTime, bindparam, cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import (
    arvio,
    arvio_payloads,
    db_queries,
    emby,
    jellyfin,
    nuvio,
    nuvio_payloads,
    nuvio_projection,
    outbound_sync,
    plex,
    plex_watchlist,
    settings_store,
    show_metadata,
    stremio,
    stremio_delivery,
    stremio_payloads,
    tmdb,
    watch_echo,
)
from core.catalog_import import get_or_create_series_media
from core.connection_identity import refresh_stream_connection
from core.db_queries import select_in_chunks as _select_in_chunks
from core.enrichment import (
    apply_media_change_safely,
    create_media_safely,
    enrich_episode_from_tvdb,
    enrich_media,
    enrich_media_safely,
    enrich_series_from_show,
    is_unmapped_tvdb_episode,
)
from core.episode_order import load_tvdb_episode_id_positions, reconcile_divergent_episode_media
from core.identity import coerce_id
from core.image_cache import pre_cache_all_collected_bg
from core.jellyfin import get_jellyfin_tmdb_id
from core.rewatch import get_active_rewatches_for_shows, record_rewatch_progress
from core.sync_jobs import (
    SyncCancelled,
    mark_job_running_unless_cancelled,
    raise_if_cancelled,
)
from core.translations import get_user_metadata_language
from core.watch_dedup import (
    find_duplicate_watch_event,
    get_dedup_window_minutes,
    is_duplicate_watch_time,
)
from db import engine
from models.base import CollectionSource, MediaType
from models.collection import Collection, CollectionFile
from models.connections import MediaServerConnection
from models.events import WatchEvent
from models.library_selections import (
    EmbyLibrarySelection,
    JellyfinLibrarySelection,
    PlexLibrarySelection,
)
from models.media import Media
from models.playback_progress import PlaybackProgress
from models.plex_pending_push import PlexPendingPush
from models.ratings import Rating, RatingChanges, RatingKey
from models.rewatch import RewatchProgress, ShowRewatch
from models.show import Show
from models.sync import SyncJob, SyncStatus
from models.users import UserSettings

logger = logging.getLogger("uvicorn.error")


async def _record_full_push_visibility_echo(db, conn, written) -> None:
    """Persist a confirmed settings echo before later full-push writes run."""
    from core.nuvio_visibility import record_visibility_echo
    await record_visibility_echo(db, conn, written)
    await db.commit()


_sync_semaphore = asyncio.Semaphore(1)


BATCH_SIZE = 500


TMDB_CONCURRENCY = 5


_MAX_IN_PARAMS = 30_000


_MEDIA_BROWSER_ITEM_SOURCES = (
    CollectionSource.jellyfin,
    CollectionSource.emby,
    CollectionSource.nuvio,
    CollectionSource.stremio,
    CollectionSource.arvio,
)


def extract_watch_state(item: dict, source: CollectionSource) -> dict:
    state = {"completed": False, "last_played": None, "play_count": 0, "user_rating": None}

    if source in _MEDIA_BROWSER_ITEM_SOURCES:
        user_data = item.get("UserData", {})
        state["completed"] = user_data.get("Played", False)
        state["play_count"] = user_data.get("PlayCount", 1 if state["completed"] else 0)
        lp = user_data.get("LastPlayedDate")
        if lp:
            dt = parser.isoparse(lp)
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            state["last_played"] = dt
        r = user_data.get("Rating")
        if r is not None:
            state["user_rating"] = float(r)
    else:  # Plex
        state["play_count"] = int(item.get("viewCount", 0))
        state["completed"] = state["play_count"] > 0
        lp = item.get("lastViewedAt")
        if lp:
            state["last_played"] = datetime.fromtimestamp(lp, tz=timezone.utc).replace(tzinfo=None)
        r = item.get("userRating")
        if r is not None:
            state["user_rating"] = float(r)

    return state


def provider_added_at(item: dict, source: CollectionSource) -> datetime | None:
    """When the media server added this item to its library, as naive UTC.

    Returns None when the source has no such concept or the value is missing
    or unparseable, in which case the caller leaves the column on its server
    default. Sources are matched explicitly rather than through
    _MEDIA_BROWSER_ITEM_SOURCES: Nuvio and Stremio items are synthesized with
    a fixed key set and carry no date, and matching them here would only wait
    for a key rename to start feeding in something wrong."""
    if source is CollectionSource.plex:
        raw = item.get("addedAt")
        if raw is None:
            return None
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OSError, OverflowError):
            return None

    if source in (CollectionSource.jellyfin, CollectionSource.emby, CollectionSource.arvio):
        raw = item.get("DateCreated")
        if not raw:
            return None
        try:
            dt = parser.isoparse(raw)
        except (TypeError, ValueError):
            return None
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt

    return None


def collection_added_at_heal_stmt():
    """Lower Collection.added_at towards the provider's date, in SQL.

    LEAST is deliberate. Taking the minimum in Python would go wrong in three
    ways: a multi-episode file expands into several items sharing one
    collection, so last-write-wins could raise the value; a preloaded snapshot
    goes stale after the first batch commit; and a concurrent webhook could
    interleave. Doing it in the database makes the write monotonic and
    idempotent, so a run that dies half way just leaves the rest for the next
    one."""
    table = Collection.__table__
    return (
        update(table)
        .where(table.c.id == bindparam("b_id"))
        .values(added_at=func.least(table.c.added_at, bindparam("b_added", type_=DateTime)))
    )


def is_fresh_rewatch_play(
    already_recorded: bool,
    media_type: MediaType,
    show_id: int | None,
    media_id: int,
    active_rewatches_by_show_id: dict,
    rewatch_progressed_media_ids: set,
    last_played,
) -> bool:
    """A full-library sync must not skip an episode just because it already
    has watch history - that's true of almost every rewatch by definition.
    But it also can't treat every "watched" flag as a fresh play, since
    Plex/Jellyfin/Emby's played flag stays true forever once set - so this
    only counts a play as belonging to the active rewatch if it hasn't been
    counted for that cycle yet AND the server's own last-played date is on
    or after the rewatch's start."""
    if not already_recorded or media_type != MediaType.episode or show_id is None:
        return False
    active_rewatch = active_rewatches_by_show_id.get(show_id)
    return bool(
        active_rewatch
        and media_id not in rewatch_progressed_media_ids
        and last_played
        and last_played >= active_rewatch.started_at
    )


def extract_jellyfin_quality(item: dict) -> dict:
    from core.jellyfin import extract_quality
    quality = extract_quality(item.get("MediaStreams", []))
    quality["file_path"] = item.get("Path")
    return quality


async def sync_shows_batch(
    series_tmdb_map: dict,  # source_series_id → tmdb_id
    db: AsyncSession,
    api_key: str = None,
) -> tuple[dict, dict]:
    """
    Fetch and insert all shows in parallel (up to TMDB_CONCURRENCY concurrent requests).
    Returns (show_map: source_id→show.id, show_id_to_tmdb: show.id→series_tmdb_id).
    """
    all_tmdb_ids = list({tid for tid in series_tmdb_map.values() if tid})

    # Bulk load already-known shows (chunked to stay under asyncpg's 32767-param limit)
    existing_shows: dict[int, Show] = {}
    if all_tmdb_ids:
        shows_loaded = await _select_in_chunks(
            db,
            lambda chunk: select(Show).where(Show.tmdb_id.in_(chunk)),
            all_tmdb_ids,
        )
        for s in shows_loaded:
            existing_shows[s.tmdb_id] = s

    missing = [tid for tid in all_tmdb_ids if tid not in existing_shows]

    # Also re-fetch active shows so new seasons added to TMDB appear without a manual refresh.
    ACTIVE_STATUSES = {"Returning Series", "In Production", "Planned"}
    stale = [
        tid for tid in all_tmdb_ids
        if tid in existing_shows and existing_shows[tid].status in ACTIVE_STATUSES
    ]
    to_fetch = list({*missing, *stale})
    print(f"    {len(existing_shows)} shows in DB, fetching {len(missing)} new + {len(stale)} active from TMDB...")

    semaphore = asyncio.Semaphore(TMDB_CONCURRENCY)
    fetched: dict[int, dict] = {}

    async def fetch_show(tmdb_id: int):
        async with semaphore:
            try:
                fetched[tmdb_id] = await tmdb.get_show(tmdb_id, api_key=api_key)
            except Exception as e:
                print(f"  Failed to fetch show tmdb={tmdb_id}: {e}")

    if to_fetch:
        await asyncio.gather(*[fetch_show(tid) for tid in to_fetch])

    if fetched:
        # Persist TMDB's TVDB cross-reference on the row (step 1 of
        # docs/tvdb-first-class-plan.md). shows.tvdb_id is unique, so only
        # claim ids no other show holds and that exactly one fetched show
        # wants; the rest are left for link_show_ids to sort out later.
        wanted_tvdb: dict[int, int] = {}
        tvdb_claims: dict[int, int] = {}
        for tmdb_id, d in fetched.items():
            ext_tvdb = coerce_id((d.get("external_ids") or {}).get("tvdb_id"))
            if ext_tvdb:
                wanted_tvdb.setdefault(ext_tvdb, tmdb_id)
                tvdb_claims[ext_tvdb] = tvdb_claims.get(ext_tvdb, 0) + 1
        dup_tvdb = {t for t, n in tvdb_claims.items() if n > 1}
        taken_tvdb: dict[int, int | None] = {}
        if wanted_tvdb:
            taken_rows = await _select_in_chunks(
                db,
                lambda chunk: select(Show).where(Show.tvdb_id.in_(chunk)),
                list(wanted_tvdb.keys()),
            )
            taken_tvdb = {s.tvdb_id: s.tmdb_id for s in taken_rows}

        def _claimable_tvdb_id(tmdb_id: int, d: dict) -> int | None:
            ext_tvdb = coerce_id((d.get("external_ids") or {}).get("tvdb_id"))
            if not ext_tvdb or ext_tvdb in dup_tvdb:
                return None
            if ext_tvdb in taken_tvdb and taken_tvdb[ext_tvdb] != tmdb_id:
                return None
            return ext_tvdb

        values = []
        for tmdb_id, d in fetched.items():
            values.append({
                "tmdb_id": tmdb_id,
                "tvdb_id": _claimable_tvdb_id(tmdb_id, d),
                "title": d.get("name"),
                "original_title": d.get("original_name"),
                "overview": d.get("overview"),
                "poster_path": tmdb.poster_url(d.get("poster_path")),
                "backdrop_path": tmdb.poster_url(d.get("backdrop_path"), size="w1280"),
                "tmdb_rating": d.get("vote_average"),
                "status": d.get("status"),
                "tagline": d.get("tagline"),
                "first_air_date": d.get("first_air_date"),
                "last_air_date": d.get("last_air_date"),
                "tmdb_data": {
                    "genres": [g["name"] for g in d.get("genres", [])],
                    "external_ids": d.get("external_ids", {}),
                    "original_language": d.get("original_language"),
                    "networks": [
                        {
                            "id": n.get("id"),
                            "name": n.get("name"),
                            "origin_country": n.get("origin_country"),
                            "logo_path": tmdb.poster_url(n.get("logo_path"), size="w185"),
                        }
                        for n in d.get("networks", [])
                        if n.get("name")
                    ],
                    "production_companies": [
                        {
                            "id": company.get("id"),
                            "name": company.get("name"),
                            "origin_country": company.get("origin_country"),
                            "logo_path": tmdb.poster_url(company.get("logo_path"), size="w185"),
                        }
                        for company in d.get("production_companies", [])
                        if company.get("name")
                    ],
                    "created_by": [
                        {"id": creator.get("id"), "name": creator.get("name")}
                        for creator in d.get("created_by", [])
                        if creator.get("name")
                    ],
                    "episode_run_time": d.get("episode_run_time", []),
                    "number_of_seasons": d.get("number_of_seasons"),
                    "number_of_episodes": d.get("number_of_episodes"),
                    "last_air_date": d.get("last_air_date"),
                    "seasons": [
                        {
                            "season_number": s["season_number"],
                            "poster_path": tmdb.poster_url(s.get("poster_path")),
                            "episode_count": s["episode_count"],
                            "name": s["name"],
                            "overview": s.get("overview"),
                            "air_date": s.get("air_date"),
                        }
                        for s in d.get("seasons", [])
                    ],
                },
            })

        # Show has 12 value columns; 32767 / 12 = 2730 rows max per statement.
        # Use BATCH_SIZE (500) to stay well under the asyncpg 32767-parameter limit.
        update_cols = [k for k in values[0].keys() if k not in ("tmdb_id", "tvdb_id")]
        for i in range(0, len(values), BATCH_SIZE):
            chunk = values[i : i + BATCH_SIZE]
            stmt = insert(Show).values(chunk)
            set_ = {k: getattr(stmt.excluded, k) for k in update_cols}
            # Fill a missing tvdb_id on an existing row, never clear or
            # replace one already there.
            set_["tvdb_id"] = func.coalesce(Show.__table__.c.tvdb_id, stmt.excluded.tvdb_id)
            stmt = stmt.on_conflict_do_update(
                index_elements=["tmdb_id"],
                set_=set_,
            )
            stmt = stmt.returning(Show)
            res = await db.execute(stmt)
            for s in res.scalars().all():
                existing_shows[s.tmdb_id] = s

    show_map: dict[str, int] = {}
    show_id_to_tmdb: dict[int, int] = {}
    for source_id, tmdb_id in series_tmdb_map.items():
        show = existing_shows.get(tmdb_id)
        if show:
            show_map[str(source_id)] = show.id
            show_id_to_tmdb[show.id] = show.tmdb_id

    # Whole-series Media rows power AnyList lists and details, while
    # Show rows own episode numbering. Older syncs enriched only Show, leaving
    # a matching Media row without its poster, synopsis, genres, or seasons.
    # Repair those rows from the canonical Show metadata on every successful
    # show mapping. Preserve the tracker-specific catalogue markers stored only
    # on Media so watch history is never invalidated by a metadata refresh.
    if existing_shows:
        media_rows = await _select_in_chunks(
            db,
            lambda chunk: select(Media).where(
                Media.tmdb_id.in_(chunk), Media.media_type == MediaType.series
            ),
            list(existing_shows.keys()),
        )
        for media in media_rows:
            show = existing_shows.get(media.tmdb_id)
            if not show:
                continue
            enrich_series_from_show(media, show)

    return show_map, show_id_to_tmdb


async def batch_enrich_items(
    db: AsyncSession,
    items: list[tuple],  # (Media, series_tmdb_id | None)
    api_key: str = None,
    show_title_map: dict[int, str] | None = None,
    user_id: int | None = None,
) -> list[dict]:
    """
    Parallel enrichment for newly created media.
    Episodes: one TMDB /season/{n} call per unique season (3865 calls vs 45k).
    Movies: parallel /movie/{id} calls.
    Returns a list of warning dicts for seasons/items that couldn't be enriched.
    """
    semaphore = asyncio.Semaphore(TMDB_CONCURRENCY)
    if show_title_map is None:
        show_title_map = {}

    movies = [m for (m, _) in items if m.media_type == MediaType.movie]
    series = [m for (m, _) in items if m.media_type == MediaType.series]
    episodes = [(m, stid) for (m, stid) in items if m.media_type == MediaType.episode and stid]

    # ── Movies: parallel enrichment ──────────────────────────────────────────
    async def enrich_movie(media: Media):
        async with semaphore:
            await enrich_media(media, api_key=api_key)

    if movies:
        await asyncio.gather(*[enrich_movie(m) for m in movies], return_exceptions=True)

    async def enrich_series(media: Media):
        async with semaphore:
            await enrich_media(media, api_key=api_key)

    if series:
        await asyncio.gather(*[enrich_series(m) for m in series], return_exceptions=True)

    from core import tvdb as tvdb_client

    # ── Episodes: one TMDB call per unique (series, season) ──────────────────
    season_to_eps: dict[tuple, list[Media]] = {}
    for media, stid in episodes:
        if media.season_number is not None:
            season_to_eps.setdefault((stid, media.season_number), []).append(media)

    season_data: dict[tuple, dict[int, dict]] = {}
    failed_season_keys: set[tuple] = set()

    async def fetch_season(stid: int, sn: int):
        async with semaphore:
            try:
                d = await tmdb.get_season(stid, sn, api_key=api_key)
                season_data[(stid, sn)] = {ep["episode_number"]: ep for ep in d.get("episodes", [])}
            except Exception as e:
                print(f"  Failed to fetch show={stid} season={sn}: {e}")
                season_data[(stid, sn)] = {}
                failed_season_keys.add((stid, sn))

    if season_to_eps:
        print(f"    Fetching {len(season_to_eps)} seasons from TMDB...")
        await asyncio.gather(
            *[fetch_season(stid, sn) for (stid, sn) in season_to_eps],
            return_exceptions=True,
        )

    # Some shows have season/episode numbering that only lines up under TVDB,
    # not TMDB (#162, #186) - resolve TVDB data for any (show, season) with at
    # least one episode TMDB didn't have, for shows that have a TVDB match.
    tvdb_season_data: dict[tuple, dict[int, dict]] = {}
    seasons_missing_episodes = {
        (stid, sn) for (stid, sn), ep_list in season_to_eps.items()
        if any(m.episode_number not in season_data.get((stid, sn), {}) for m in ep_list)
    }
    if user_id and seasons_missing_episodes:
        needing_tvdb_stids = {stid for (stid, sn) in seasons_missing_episodes}
        shows_result = await db.execute(
            select(Show.tmdb_id, Show.tvdb_id).where(
                Show.tmdb_id.in_(needing_tvdb_stids), Show.tvdb_id.isnot(None)
            )
        )
        tvdb_id_by_stid = {row[0]: row[1] for row in shows_result.all()}
        if tvdb_id_by_stid:

            tvdb_api_key = await settings_store.get_user_tvdb_key(db, user_id)
            if tvdb_api_key:
                tvdb_lang = tvdb_client.tvdb_language(await get_user_metadata_language(db, user_id))

                async def fetch_tvdb_season(stid: int, sn: int, tvdb_id: int):
                    async with semaphore:
                        try:
                            raw_eps = await tvdb_client.get_series_episodes(tvdb_id, sn, tvdb_api_key, language=tvdb_lang)
                            tvdb_season_data[(stid, sn)] = {e.get("number"): e for e in raw_eps}
                        except Exception:
                            tvdb_season_data[(stid, sn)] = {}

                await asyncio.gather(
                    *[
                        fetch_tvdb_season(stid, sn, tvdb_id_by_stid[stid])
                        for (stid, sn) in seasons_missing_episodes
                        if stid in tvdb_id_by_stid
                    ],
                    return_exceptions=True,
                )

    def apply_ep_data(media: Media, ep: dict) -> None:
        media.tmdb_id = ep.get("id") or media.tmdb_id
        media.title = ep.get("name") or media.title
        media.overview = ep.get("overview")
        media.poster_path = tmdb.poster_url(ep.get("still_path"), size="w500")
        media.release_date = ep.get("air_date")
        media.tmdb_rating = ep.get("vote_average")
        media.runtime = ep.get("runtime") or media.runtime  # see #169
        media.tmdb_data = {"runtime": ep.get("runtime"), "cast": []}

    async def apply_tvdb_ep_data(media: Media, raw_ep: dict) -> None:
        await enrich_episode_from_tvdb(media, tvdb_client.format_episode(raw_ep))

    tvdb_resolved_season_keys: set[tuple] = set()
    for (stid, sn), ep_list in season_to_eps.items():
        ep_map = season_data.get((stid, sn), {})
        tvdb_ep_map = tvdb_season_data.get((stid, sn), {})
        for media in ep_list:
            ep = ep_map.get(media.episode_number)
            if ep:
                await apply_media_change_safely(db, media, lambda media=media, ep=ep: apply_ep_data(media, ep))
                continue
            tvdb_ep = tvdb_ep_map.get(media.episode_number)
            if tvdb_ep:
                await apply_media_change_safely(
                    db, media, lambda media=media, tvdb_ep=tvdb_ep: apply_tvdb_ep_data(media, tvdb_ep)
                )
                tvdb_resolved_season_keys.add((stid, sn))

    # Build per-season warning entries (one entry per still-failed season) -
    # a season fully recovered via TVDB doesn't need to warn the user.
    warnings: list[dict] = []
    for (stid, sn) in sorted(failed_season_keys - tvdb_resolved_season_keys):
        warnings.append({
            "show": show_title_map.get(stid, f"TMDB show #{stid}"),
            "tmdb_id": stid,
            "season": sn,
            "affected_episodes": len(season_to_eps.get((stid, sn), [])),
            "reason": "Season not found on TMDB — the show may be split into separate series on TMDB",
        })

    return warnings


_STALE_REMOVAL_MIN_EXISTING = 10


_STALE_REMOVAL_MAX_FRACTION = 0.5


async def _remove_stale_collection_files(
    db: AsyncSession,
    user_id: int,
    source: CollectionSource,
    connection_id: int,
    seen_source_ids: set[str],
) -> set[int]:
    """After a full library scan, prunes CollectionFiles for this connection
    whose source_id wasn't seen this run - i.e. it's no longer on the media
    server (see #139: deleting a title in Plex/Jellyfin/Emby never removed it
    from the collection, since sync only ever added/updated). Only safe to
    call when the scan covered the connection's entire selected library, or
    everything not scanned this pass looks "deleted" and gets pruned too."""
    result = await db.execute(
        select(CollectionFile, Collection.media_id)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .where(
            Collection.user_id == user_id,
            CollectionFile.source == source,
            CollectionFile.connection_id == connection_id,
        )
    )
    rows = result.all()
    stale = [(cf, media_id) for cf, media_id in rows if cf.source_id not in seen_source_ids]

    if len(rows) >= _STALE_REMOVAL_MIN_EXISTING and len(stale) / len(rows) > _STALE_REMOVAL_MAX_FRACTION:
        logger.warning(
            "Refusing to prune %d/%d %s collection files for connection %s (user %s) - "
            "this looks like a bad/partial scan rather than real deletions on the server; "
            "skipping stale-collection cleanup for this sync run.",
            len(stale), len(rows), source.value, connection_id, user_id,
        )
        return set()

    removed_media_ids: set[int] = set()
    for collection_file, media_id in stale:
        collection_id = collection_file.collection_id
        await db.delete(collection_file)
        await db.flush()
        remaining = await db.execute(
            select(func.count(CollectionFile.id)).where(
                CollectionFile.collection_id == collection_id
            )
        )
        if remaining.scalar_one() == 0:
            collection = await db.get(Collection, collection_id)
            if collection:
                await db.delete(collection)
                removed_media_ids.add(media_id)
    return removed_media_ids


def _expand_multi_episode_items(items: list, media_type: MediaType, source: CollectionSource) -> list:
    """Jellyfin/Emby can represent multiple episodes muxed into one video file as
    a single library item, exposing the span via IndexNumber..IndexNumberEnd (e.g.
    a cartoon with two episodes per file). sync_items is built around one file ==
    one episode, so expand a combined item into one shallow copy per episode number
    in the range - each copy still points at the same underlying source_id/file, so
    they all end up as separate CollectionFiles on that one Jellyfin item (see #138:
    previously only the first episode of a combined file was ever collected)."""
    if media_type != MediaType.episode or source not in _MEDIA_BROWSER_ITEM_SOURCES:
        return items
    expanded = []
    for item in items:
        start = item.get("IndexNumber")
        end = item.get("IndexNumberEnd")
        if start is not None and end is not None and end > start:
            for ep in range(start, end + 1):
                copy = dict(item)
                copy["IndexNumber"] = ep
                expanded.append(copy)
        else:
            expanded.append(item)
    return expanded


def _canonical_episode_position(
    item: dict, series_tmdb_id: int | None, positions: dict[tuple[int, int], tuple[int, int]]
) -> tuple[int, int] | None:
    """The canonical TMDB (season, episode) of a Jellyfin/Emby episode item, found
    through the episode's own TVDB id (#447), or None to keep the raw numbers.

    The server's SeasonNumber/EpisodeNumber follow whatever metadata provider it
    uses, so they are never assumed to be TVDB's - only an exact episode-id match
    against an EpisodeOrderMapping row counts. A multi-episode file is skipped: its
    ProviderIds belong to the first episode only, not to each expanded copy."""
    if not series_tmdb_id or item.get("IndexNumberEnd") is not None:
        return None
    tvdb_id = jellyfin.get_jellyfin_tvdb_id(item.get("ProviderIds") or {})
    return positions.get((series_tmdb_id, tvdb_id)) if tvdb_id else None


async def _fold_divergent_episodes(
    db: AsyncSession,
    items: list,
    show_map: dict,
    show_id_to_tmdb: dict,
    tvdb_positions: dict[tuple[int, int], tuple[int, int]],
    media_by_episode: dict,
) -> tuple[bool, list[Media]]:
    """#447: settle divergent twins of translated items before the sync loop. Returns
    (changed, moved_rows): changed is True when any row moved or merged, so the caller
    must reload its lookup maps; moved_rows are the rows repositioned onto a canonical
    slot, which still lack their TMDB data and need enriching."""
    # A divergent twin from before this ran (or from the webhook path) holds the
    # watch history. Where the canonical row exists, fold the twin into it; where
    # it was never created, move the twin itself to the canonical position. Either
    # way this sync then neither duplicates the file nor records the watch twice.
    reconcile_show_ids: set[int] = set()
    moved_rows: list[Media] = []
    for it in items:
        sid = show_map.get(str(it.get("SeriesId")))
        pos = _canonical_episode_position(it, show_id_to_tmdb.get(sid), tvdb_positions)
        raw = (sid, it.get("ParentIndexNumber"), it.get("IndexNumber"))
        divergent = media_by_episode.get(raw) if pos and sid else None
        if divergent is None:
            continue
        canonical = media_by_episode.get((sid, *pos))
        if canonical is divergent:
            continue
        if canonical is not None:
            reconcile_show_ids.add(sid)
            continue
        # Only a row provably this very episode may be moved: the raw slot can
        # just as well hold a genuinely different TMDB episode. Same identity
        # rule as reconcile_divergent_episode_media (tmdb_id only as the legacy
        # stand-in for a TVDB id when tvdb_id was never set).
        item_tvdb_id = jellyfin.get_jellyfin_tvdb_id(it.get("ProviderIds") or {})
        row_tvdb_id = divergent.tvdb_id if divergent.tvdb_id is not None else divergent.tmdb_id
        if row_tvdb_id != item_tvdb_id:
            continue
        divergent.season_number, divergent.episode_number = pos
        del media_by_episode[raw]
        media_by_episode[(sid, *pos)] = divergent
        moved_rows.append(divergent)
    if moved_rows:
        await db.flush()
    merged_any = bool(moved_rows)
    for sid in reconcile_show_ids:
        show_row = await db.get(Show, sid)
        if show_row:
            try:
                stats_r = await reconcile_divergent_episode_media(db, show_row)
                merged_any = merged_any or bool(stats_r.get("merged"))
            except Exception:
                logger.exception("Pre-sync episode reconciliation failed for show=%s", sid)
    return merged_any, moved_rows


async def sync_items(
    items: list,
    media_type: MediaType,
    source: CollectionSource,
    db: AsyncSession,
    stats: dict,
    user_id: int,
    job_id: int = None,
    show_map: dict = {},
    api_key: str = None,
    show_id_to_tmdb: dict = {},  # show.id → series tmdb_id, for episode enrichment
    sync_collection: bool = True,
    sync_watched: bool = True,
    sync_ratings: bool = True,
    new_watched_ids: set[int] | None = None,  # accumulated across calls; mutated in-place
    new_ratings: RatingChanges | None = None,  # accumulated across calls; mutated in-place
    observed_ratings: RatingChanges | None = None,  # complete source values for reviewed media-server pulls
    new_collected_ids: set[int] | None = None,  # accumulated across calls; mutated in-place
    connection_id: int | None = None,
    ratingkey_to_media_id: dict[str, int] | None = None,  # accumulated across calls; mutated in-place
    seen_source_ids: set[str] | None = None,  # accumulated across calls; every source_id encountered this run, used to prune deletions afterward
    push_back: dict[int, str] | None = None,  # accumulated across calls; media_id → source_id of newly collected items Scrob has watched but the server reports unwatched (#420)
    snapshot_started_at: datetime | None = None,
    full_resync: bool = False,
) -> list[dict]:  # returns warnings
    items = _expand_multi_episode_items(items, media_type, source)
    print(f"  Syncing {len(items)} {media_type.value}s from {source.value}...")

    # ── Phase 1: Pre-load existing data (replaces all N+1 queries) ────────────

    # All existing CollectionFiles for this user+source: (source_id, episode_number) →
    # (CollectionFile, media_id, Media). Keyed on episode_number too (None for movies,
    # where it's a no-op) so a multi-episode Jellyfin file - several CollectionFiles
    # sharing one source_id - doesn't collide into a single dict entry (see #138).
    files_q = await db.execute(
        select(CollectionFile, Collection.media_id, Media)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .join(Media, Media.id == Collection.media_id)
        .where(Collection.user_id == user_id, CollectionFile.source == source)
    )
    files_rows = files_q.all()
    existing_files: dict[tuple[str, int | None], tuple[CollectionFile, int, Media]] = {
        (f.source_id, m.episode_number): (f, media_id, m) for f, media_id, m in files_rows
    }
    # (media_id, source) → CollectionFile — to detect webhook-vs-sync source_id mismatches
    files_by_media_source: dict[tuple[int, CollectionSource], CollectionFile] = {
        (media_id, f.source): f for f, media_id, _ in files_rows
    }

    # All existing Collections for this user: media_id → Collection.id
    # Used to attach new CollectionFiles to existing Collections (multi-source items)
    colls_q = await db.execute(
        select(Collection.id, Collection.media_id).where(Collection.user_id == user_id)
    )
    existing_coll_by_media_id: dict[int, int] = {
        media_id: coll_id for coll_id, media_id in colls_q.all()
    }

    # All relevant media, keyed for O(1) lookup
    media_by_episode: dict[tuple, Media] = {}   # (show_id, season, ep) → Media
    media_by_tmdb: dict[tuple, Media] = {}       # (tmdb_id, media_type) → Media

    if media_type == MediaType.episode:
        show_ids = list(set(show_map.values()))
        if show_ids:
            episodes = await _select_in_chunks(
                db,
                lambda chunk: select(Media).where(Media.media_type == MediaType.episode, Media.show_id.in_(chunk)),
                show_ids,
            )
            for m in episodes:
                media_by_episode[(m.show_id, m.season_number, m.episode_number)] = m
        # Also pre-load orphaned episode rows (show_id=None, created by webhook before first sync)
        # so they can be deduplicated by TMDB ID instead of creating a second row.
        ep_tmdb_ids: set[int] = set()
        for item in items:
            tid = (
                get_jellyfin_tmdb_id(item.get("ProviderIds", {}))
                if source in _MEDIA_BROWSER_ITEM_SOURCES
                else plex.extract_tmdb_id(item.get("Guid", []))
            )
            if tid:
                ep_tmdb_ids.add(tid)
        if ep_tmdb_ids:
            orphans = await _select_in_chunks(
                db,
                lambda chunk: select(Media).where(
                    Media.media_type == MediaType.episode,
                    Media.tmdb_id.in_(chunk),
                    Media.show_id.is_(None),
                ),
                list(ep_tmdb_ids),
            )
            for m in orphans:
                media_by_tmdb[(m.tmdb_id, m.media_type)] = m
    else:
        tmdb_ids: set[int] = set()
        for item in items:
            tid = (
                get_jellyfin_tmdb_id(item.get("ProviderIds", {}))
                if source in _MEDIA_BROWSER_ITEM_SOURCES
                else plex.extract_tmdb_id(item.get("Guid", []))
            )
            if tid:
                tmdb_ids.add(tid)
        if tmdb_ids:
            medias = await _select_in_chunks(
                db,
                lambda chunk: select(Media).where(Media.media_type == media_type, Media.tmdb_id.in_(chunk)),
                list(tmdb_ids),
            )
            for m in medias:
                media_by_tmdb[(m.tmdb_id, m.media_type)] = m

    # #447: Jellyfin/Emby report their own numbering, which for a show whose TVDB and
    # TMDB layouts diverge is not the canonical TMDB position every other path uses.
    # Resolve each item through its episode-level TVDB id so the lookups below land
    # on the canonical row instead of creating a divergent twin on every sync.
    tvdb_positions: dict[tuple[int, int], tuple[int, int]] = {}
    moved_rows: list[Media] = []
    if media_type == MediaType.episode and source in _MEDIA_BROWSER_ITEM_SOURCES and show_ids:
        series_ids = sorted({t for t in show_id_to_tmdb.values() if t})
        item_tvdb_ids = sorted({
            tid for it in items
            if it.get("IndexNumberEnd") is None
            and (tid := jellyfin.get_jellyfin_tvdb_id(it.get("ProviderIds") or {}))
        })
        tvdb_positions = await load_tvdb_episode_id_positions(db, series_ids, item_tvdb_ids)

    if tvdb_positions:
        merged_any, moved_rows = await _fold_divergent_episodes(
            db, items, show_map, show_id_to_tmdb, tvdb_positions, media_by_episode
        )
        if merged_any:
            await db.commit()
            media_by_episode.clear()
            for m in await _select_in_chunks(
                db,
                lambda chunk: select(Media).where(Media.media_type == MediaType.episode, Media.show_id.in_(chunk)),
                show_ids,
            ):
                media_by_episode[(m.show_id, m.season_number, m.episode_number)] = m
            # The CollectionFiles were keyed on the rows' old positions.
            files_q = await db.execute(
                select(CollectionFile, Collection.media_id, Media)
                .join(Collection, Collection.id == CollectionFile.collection_id)
                .join(Media, Media.id == Collection.media_id)
                .where(Collection.user_id == user_id, CollectionFile.source == source)
            )
            files_rows = files_q.all()
            existing_files = {(f.source_id, m.episode_number): (f, media_id, m) for f, media_id, m in files_rows}
            files_by_media_source = {(media_id, f.source): f for f, media_id, _ in files_rows}
            colls_q = await db.execute(
                select(Collection.id, Collection.media_id).where(Collection.user_id == user_id)
            )
            existing_coll_by_media_id = {media_id: coll_id for coll_id, media_id in colls_q.all()}

    # Reverse lookup: media.id → Media object (for healing unenriched items in skipped branch)
    media_by_id: dict[int, Media] = {m.id: m for _, _, m in files_rows}
    for m in list(media_by_episode.values()) + list(media_by_tmdb.values()):
        media_by_id[m.id] = m

    # Existing watch event media_ids (only need the int, not the ORM object).
    # existing_completed is the narrower set that actually finished (#253) -
    # Jellyfin/Emby can report PlayCount > 0 with Played still False (started
    # but not yet past their own played-threshold), which already_recorded
    # alone would treat as "nothing to do" forever, even once the server
    # later reports the real completion.
    we_res = await db.execute(select(WatchEvent.media_id, WatchEvent.completed).where(WatchEvent.user_id == user_id))
    we_rows = we_res.all()
    existing_watched: set[int] = {row[0] for row in we_rows}
    existing_completed: set[int] = {row[0] for row in we_rows if row[1]}

    # Rewatch-aware watched dedup: a show mid-rewatch must not skip an episode
    # just because raw history already has it (it always will - that's the
    # point of a rewatch) - it needs to check that rewatch's own progress
    # instead. A play only counts as fresh if the server's own last-played
    # date is after the rewatch started, since Plex/Jellyfin/Emby's "watched"
    # flag stays true forever once set, so every sync would otherwise look
    # like a fresh play for every episode.
    active_rewatches_by_show_id: dict[int, ShowRewatch] = {}
    rewatch_progressed_media_ids: set[int] = set()
    if media_type == MediaType.episode and show_ids:
        active_rewatches_by_show_id = await get_active_rewatches_for_shows(db, user_id, show_ids)
        if active_rewatches_by_show_id:
            progress_q = await db.execute(
                select(RewatchProgress.media_id).where(
                    RewatchProgress.rewatch_id.in_([r.id for r in active_rewatches_by_show_id.values()])
                )
            )
            rewatch_progressed_media_ids = {row[0] for row in progress_q.all()}

    # Existing ratings: media_id → Rating
    if observed_ratings is None:
        rat_res = await db.execute(
            select(Rating).where(
                Rating.user_id == user_id,
                Rating.season_number.is_(None),
                Rating.episode_order.is_(None),
            )
        )
        existing_ratings: dict[int, Rating] = {r.media_id: r for r in rat_res.scalars()}
    else:
        existing_ratings = {}

    # ── Phase 2: Main sync loop (no N+1 queries, savepoints for error isolation) ──
    new_media_for_enrichment: list[tuple] = []  # (Media, series_tmdb_id | None)
    # Rows moved onto their canonical slot above: fill in the TMDB data (and tmdb_id)
    # they never had, exactly like a newly created episode.
    new_media_for_enrichment.extend((m, show_id_to_tmdb.get(m.show_id)) for m in moved_rows)
    skipped_warnings: list[dict] = []

    # collection_id → earliest add-date seen this run, applied in batches so a
    # large library costs one statement per batch rather than one per item.
    collection_heals: dict[int, datetime] = {}

    async def flush_collection_heals() -> None:
        if not collection_heals:
            return
        await db.execute(
            collection_added_at_heal_stmt(),
            [{"b_id": cid, "b_added": dt} for cid, dt in collection_heals.items()],
        )
        collection_heals.clear()

    for i, item in enumerate(items):
        if (
            i % BATCH_SIZE == 0
            and snapshot_started_at is not None
            and connection_id is not None
            and source in (CollectionSource.nuvio, CollectionSource.stremio)
        ):
            from core.streaming_clear import assert_pull_snapshot_current
            await assert_pull_snapshot_current(
                db, user_id, SimpleNamespace(id=connection_id), snapshot_started_at,
                full_resync=full_resync,
            )
        new_media: Media | None = None
        rating_observation: tuple[RatingKey, float] | None = None
        try:
            async with db.begin_nested():
                if source in _MEDIA_BROWSER_ITEM_SOURCES:
                    source_id = str(item.get("Id"))
                    quality = extract_jellyfin_quality(item)
                    tmdb_id = get_jellyfin_tmdb_id(item.get("ProviderIds", {}))
                    parent_id = item.get("SeriesId")
                    name = item.get("Name")
                    season_num = item.get("ParentIndexNumber")
                    episode_num = item.get("IndexNumber")
                    if tvdb_positions:
                        canonical_pos = _canonical_episode_position(
                            item, show_id_to_tmdb.get(show_map.get(str(parent_id))), tvdb_positions
                        )
                        if canonical_pos:
                            season_num, episode_num = canonical_pos
                else:  # Plex
                    source_id = str(item.get("ratingKey"))
                    quality = plex.extract_quality(item.get("Media", []))
                    tmdb_id = plex.extract_tmdb_id(item.get("Guid", []))
                    parent_id = item.get("grandparentRatingKey")
                    name = item.get("title")
                    season_num = item.get("parentIndex")
                    episode_num = item.get("index")

                added_at = provider_added_at(item, source)

                if seen_source_ids is not None:
                    seen_source_ids.add(source_id)

                file_entry = existing_files.get((source_id, episode_num))
                media_id_for_watch: int | None = None
                heal_collection_id: int | None = None
                new_file_media_id: int | None = None
                show_id: int | None = None  # (re)assigned below for episodes; stays None for movies

                # Detect re-match: same Plex ratingKey but TMDB ID changed.
                # Evict the stale CollectionFile so the item is re-processed below.
                if file_entry and tmdb_id and sync_collection:
                    _, _existing_media_id, _existing_media = file_entry
                    if _existing_media.tmdb_id is not None and _existing_media.tmdb_id != tmdb_id:
                        stale_file = file_entry[0]
                        stale_collection_id = stale_file.collection_id
                        await db.delete(stale_file)
                        await db.flush()
                        remaining_q = await db.execute(
                            select(func.count(CollectionFile.id)).where(
                                CollectionFile.collection_id == stale_collection_id
                            )
                        )
                        if remaining_q.scalar() == 0:
                            stale_coll = await db.get(Collection, stale_collection_id)
                            if stale_coll:
                                await db.delete(stale_coll)
                                existing_coll_by_media_id.pop(_existing_media_id, None)
                        existing_files.pop((source_id, episode_num), None)
                        files_by_media_source.pop((_existing_media_id, source), None)
                        file_entry = None

                if file_entry:
                    existing_file, existing_media_id, existing_media_obj = file_entry
                    if sync_collection:
                        # Update quality metadata in-place on the CollectionFile.
                        # Never overwrite language lists with empty — bulk endpoints (e.g. Plex
                        # /library/sections/all) often omit Part.Stream data, so an empty result
                        # means "not available here", not "no languages".
                        existing_file.resolution = quality.get("resolution")
                        existing_file.video_codec = quality.get("video_codec")
                        existing_file.audio_codec = quality.get("audio_codec")
                        existing_file.audio_channels = quality.get("audio_channels")
                        if quality.get("audio_languages"):
                            existing_file.audio_languages = quality["audio_languages"]
                        if quality.get("subtitle_languages"):
                            existing_file.subtitle_languages = quality["subtitle_languages"]
                        existing_file.file_path = quality.get("file_path")
                        if connection_id is not None:
                            existing_file.connection_id = connection_id
                        if added_at is not None:
                            existing_file.added_at = added_at
                        heal_collection_id = existing_file.collection_id
                    stats["skipped"] += 1
                    media_id_for_watch = existing_media_id

                    # Heal missing TMDB ID for movies
                    if media_type == MediaType.movie and existing_media_obj.tmdb_id is None and tmdb_id is not None:
                        existing_media_obj = await apply_media_change_safely(
                            db, existing_media_obj, lambda m=existing_media_obj: setattr(m, "tmdb_id", tmdb_id)
                        )
                        if not any(m is existing_media_obj for m, _ in new_media_for_enrichment):
                            new_media_for_enrichment.append((existing_media_obj, None))

                    # Heal unenriched episodes: webhook may have created a Media row
                    # without show_id/poster_path before the first sync ran.
                    if media_type == MediaType.episode:
                        show_id = show_map.get(str(parent_id)) if parent_id else None
                        if show_id:
                            if existing_media_obj and (
                                existing_media_obj.show_id is None
                                or (existing_media_obj.poster_path is None and not existing_media_obj.tmdb_data)
                            ):
                                ep_series_tmdb_id = show_id_to_tmdb.get(show_id)
                                if ep_series_tmdb_id:
                                    existing_media_obj.show_id = show_id
                                    # Also fill in season/episode numbers if the webhook
                                    # created the row without them — required for enrichment.
                                    if existing_media_obj.season_number is None and season_num is not None:
                                        existing_media_obj.season_number = season_num
                                    if existing_media_obj.episode_number is None and episode_num is not None:
                                        existing_media_obj.episode_number = episode_num
                                    if not any(m is existing_media_obj for m, _ in new_media_for_enrichment):
                                        new_media_for_enrichment.append((existing_media_obj, ep_series_tmdb_id))
                        else:
                            # Heal missing show_title tag on existing stub episodes (synced before
                            # stub-tagging was introduced). Backfill so match-unmatched-show can find them.
                            if (
                                existing_media_obj.tmdb_id is None
                                and existing_media_obj.show_id is None
                                and not (existing_media_obj.tmdb_data or {}).get("show_title")
                            ):
                                _series_name = (
                                    item.get("SeriesName") if source in _MEDIA_BROWSER_ITEM_SOURCES
                                    else item.get("grandparentTitle")
                                )
                                if _series_name:
                                    existing_media_obj.tmdb_data = {
                                        **(existing_media_obj.tmdb_data or {}),
                                        "show_title": _series_name,
                                    }
                else:
                    show_id = show_map.get(str(parent_id)) if media_type == MediaType.episode else None

                    # For Jellyfin/Emby episodes whose metadata scraping failed: the item title
                    # is often the raw filename (e.g. "Show.Name.S02E01"). Try to salvage the
                    # season/episode numbers from the filename so the item can be stored and
                    # later enriched (or generate a Remap-capable enrichment warning) instead of
                    # being silently skipped as unmatched.
                    if (media_type == MediaType.episode and show_id and not tmdb_id
                            and (season_num is None or episode_num is None)):
                        _m = re.search(r'[Ss](\d+)[Ee](\d+)', name or '')
                        if _m:
                            if season_num is None:
                                season_num = int(_m.group(1))
                            if episode_num is None:
                                episode_num = int(_m.group(2))

                    # Look up existing media from pre-loaded dicts (O(1), no DB query)
                    if media_type == MediaType.episode and show_id:
                        media = media_by_episode.get((show_id, season_num, episode_num))
                        if not media and tmdb_id:
                            # Fallback: catch orphaned rows created by webhook without show_id
                            media = media_by_tmdb.get((tmdb_id, media_type))
                            if media:
                                # Backfill missing show_id so future lookups work correctly
                                media.show_id = show_id
                                media_by_episode[(show_id, season_num, episode_num)] = media
                    elif tmdb_id:
                        media = media_by_tmdb.get((tmdb_id, media_type))
                    else:
                        media = None

                    if media and (media.id, source) in files_by_media_source:
                        # Media has a CollectionFile for this source but a different source_id
                        # (e.g., webhook ratingKey differs from sync ratingKey for the same item).
                        # Update the existing CollectionFile in-place instead of inserting a duplicate.
                        if sync_collection:
                            existing_alt_file = files_by_media_source[(media.id, source)]
                            existing_alt_file.source_id = source_id
                            existing_alt_file.resolution = quality.get("resolution")
                            existing_alt_file.video_codec = quality.get("video_codec")
                            existing_alt_file.audio_codec = quality.get("audio_codec")
                            existing_alt_file.audio_channels = quality.get("audio_channels")
                            if quality.get("audio_languages"):
                                existing_alt_file.audio_languages = quality["audio_languages"]
                            if quality.get("subtitle_languages"):
                                existing_alt_file.subtitle_languages = quality["subtitle_languages"]
                            existing_alt_file.file_path = quality.get("file_path")
                            if connection_id is not None:
                                existing_alt_file.connection_id = connection_id
                            if added_at is not None:
                                existing_alt_file.added_at = added_at
                            # Keep in-memory maps consistent
                            old_source_id = existing_alt_file.source_id
                            existing_files.pop((old_source_id, episode_num), None)
                            existing_files[(source_id, episode_num)] = (existing_alt_file, media.id, tmdb_id)
                            files_by_media_source[(media.id, source)] = existing_alt_file
                            heal_collection_id = existing_alt_file.collection_id
                        stats["skipped"] += 1
                        media_id_for_watch = media.id
                    else:
                        if not media:
                            can_store_stub = False
                            series_name: str | None = None
                            plex_guids: list[str] = []
                            if not tmdb_id:
                                # TV episodes belonging to a known show can still be tracked and
                                # enriched later even without an individual episode TMDB ID (e.g.
                                # Jellyfin hasn't finished fetching episode metadata yet).
                                # Everything else (movies, episodes without show context) is skipped.
                                series_name = (
                                    item.get("SeriesName") if source in _MEDIA_BROWSER_ITEM_SOURCES
                                    else item.get("grandparentTitle")
                                ) if media_type == MediaType.episode else None

                                # Episodes with no TMDB show match but with a known series name,
                                # season, and episode number are stored as stubs so the user can
                                # later match them to TVDB from the Settings warnings panel.
                                # Movies with no TMDB match are stored as stubs so the user can
                                # later match them from the Settings warnings panel.
                                can_store_stub = (
                                    media_type == MediaType.episode
                                    and series_name
                                    and season_num is not None
                                    and episode_num is not None
                                ) or (
                                    media_type == MediaType.movie
                                    and bool(name)
                                )

                                if not (show_id or can_store_stub):
                                    skipped_warnings.append({
                                        "title": name,
                                        "media_type": media_type.value,
                                        "source_id": source_id,
                                        **({"series_name": series_name} if series_name else {}),
                                        "reason": "Unmatched on source — no TMDB ID available",
                                    })
                                    stats["skipped"] += 1
                                    raise Exception("Skip this item (unmatched)") # Triggers rollback of the nested transaction

                                # Stub episode/movie: add a warning (for the settings panel) and let the
                                # Media row be created below so the user can match it later.
                                if can_store_stub and not show_id:
                                    plex_guids = [
                                        g["id"] for g in (item.get("Guid") or [])
                                        if isinstance(g, dict) and g.get("id")
                                    ]
                                    skipped_warnings.append({
                                        "title": name,
                                        "media_type": media_type.value,
                                        "source_id": source_id,
                                        **({"series_name": series_name} if series_name else {}),
                                        **({"plex_guids": plex_guids} if plex_guids else {}),
                                        "reason": "Unmatched on source — no TMDB ID available",
                                    })

                            media, _created = await create_media_safely(
                                db,
                                tmdb_id,
                                media_type,
                                title=name,
                                show_id=show_id,
                                season_number=season_num,
                                episode_number=episode_num,
                            )
                            new_media = media  # Cache updated after savepoint commits below

                            # Tag stub episodes so the match-unmatched-show endpoint can find them
                            if can_store_stub and not show_id and media.tmdb_data is None and media_type == MediaType.episode:
                                media.tmdb_data = {
                                    "show_title": series_name,
                                    **({"plex_guids": plex_guids} if plex_guids else {}),
                                }

                            ep_series_tmdb_id = show_id_to_tmdb.get(show_id) if show_id else None
                            if tmdb_id or ep_series_tmdb_id:
                                new_media_for_enrichment.append((media, ep_series_tmdb_id))

                        if sync_collection:
                            coll_id = existing_coll_by_media_id.get(media.id)
                            if coll_id is None:
                                # Upsert guards against races between concurrent
                                # webhooks / savepoint rollbacks that desynchronise the
                                # in-memory dict from the DB. On conflict the row already
                                # exists, so keep whichever add-date is earlier rather
                                # than leaving it on the row's insert time.
                                coll_values = {"user_id": user_id, "media_id": media.id}
                                if added_at is not None:
                                    coll_values["added_at"] = added_at
                                coll_stmt = insert(Collection).values(**coll_values)
                                if added_at is not None:
                                    coll_stmt = coll_stmt.on_conflict_do_update(
                                        constraint="uq_collection_user_media",
                                        set_={"added_at": func.least(Collection.added_at, coll_stmt.excluded.added_at)},
                                    )
                                else:
                                    coll_stmt = coll_stmt.on_conflict_do_nothing(constraint="uq_collection_user_media")
                                await db.execute(coll_stmt)
                                await db.flush()
                                coll_result = await db.execute(
                                    select(Collection.id).where(
                                        Collection.user_id == user_id,
                                        Collection.media_id == media.id,
                                    )
                                )
                                coll_id = coll_result.scalar_one()
                                existing_coll_by_media_id[media.id] = coll_id
                                stat_key = "movies" if media_type == MediaType.movie else "series" if media_type == MediaType.series else "episodes"
                                stats[stat_key] = stats.get(stat_key, 0) + 1
                                if new_collected_ids is not None:
                                    new_collected_ids.add(media.id)
                            # else: collection already exists from another source — just add the file
                            db.add(CollectionFile(
                                collection_id=coll_id,
                                connection_id=connection_id,
                                source=source,
                                source_id=source_id,
                                added_at=added_at,
                                file_path=quality.get("file_path"),
                                resolution=quality.get("resolution"),
                                video_codec=quality.get("video_codec"),
                                audio_codec=quality.get("audio_codec"),
                                audio_channels=quality.get("audio_channels"),
                                audio_languages=quality.get("audio_languages"),
                                subtitle_languages=quality.get("subtitle_languages"),
                            ))
                            heal_collection_id = coll_id
                            new_file_media_id = media.id
                        media_id_for_watch = media.id

                if ratingkey_to_media_id is not None and media_id_for_watch is not None:
                    ratingkey_to_media_id[source_id] = media_id_for_watch

                if media_id_for_watch is not None:
                    watch_state = extract_watch_state(item, source)
                    if sync_watched and (watch_state["completed"] or watch_state["play_count"] > 0):
                        from core.watch_dates import (
                            inferred_watch_datetime,
                            reconcile_inferred_watch_date,
                        )
                        shared_date = bool((item.get('UserData') or {}).get('SharedWatchDate'))
                        if not shared_date and watch_state["completed"] and watch_state["last_played"] is not None:
                            await reconcile_inferred_watch_date(
                                db, user_id, media_id_for_watch, watch_state["last_played"],
                                authoritative=source in (CollectionSource.stremio, CollectionSource.nuvio),
                            )
                        already_recorded = media_id_for_watch in existing_watched
                        rewatch_eligible = not shared_date and is_fresh_rewatch_play(
                            already_recorded,
                            media_type,
                            show_id,
                            media_id_for_watch,
                            active_rewatches_by_show_id,
                            rewatch_progressed_media_ids,
                            watch_state["last_played"],
                        )
                        # An existing record that never actually completed (#253 -
                        # e.g. Jellyfin/Emby reporting PlayCount > 0 while Played is
                        # still False, a partial watch under their own played
                        # threshold) must not block recording the real completion
                        # once the server later reports it - only a record that's
                        # ALREADY completed counts as nothing new to do here.
                        needs_completion = watch_state["completed"] and media_id_for_watch not in existing_completed
                        if not already_recorded or rewatch_eligible or needs_completion:
                            # Keep the estimate replaceable if a later pull
                            # supplies the server's actual play date.
                            watch_event = WatchEvent(
                                user_id=user_id,
                                media_id=media_id_for_watch,
                                watched_at=watch_state["last_played"] or inferred_watch_datetime(),
                                date_inferred=watch_state["last_played"] is None or shared_date,
                                date_shared=shared_date,
                                completed=watch_state["completed"],
                                play_count=max(1, watch_state["play_count"]),
                                progress_percent=1.0 if watch_state["completed"] else 0.0,
                            )
                            db.add(watch_event)
                            if watch_state["completed"]:
                                await db.flush()
                                await record_rewatch_progress(db, user_id, media_id_for_watch, watch_event.id)
                                existing_completed.add(media_id_for_watch)
                            existing_watched.add(media_id_for_watch)
                            if rewatch_eligible:
                                rewatch_progressed_media_ids.add(media_id_for_watch)
                            # Only finished plays fan out - mark_watched is
                            # all-or-nothing, so pushing a merely started item
                            # (#253) marks it fully watched on the other side.
                            if new_watched_ids is not None and watch_state["completed"]:
                                new_watched_ids.add(media_id_for_watch)

                    if (
                        push_back is not None
                        and new_file_media_id == media_id_for_watch
                        and not watch_state["completed"]
                        and media_id_for_watch in existing_completed
                    ):
                        push_back[media_id_for_watch] = source_id

                    if sync_ratings and watch_state["user_rating"] is not None:
                        if observed_ratings is not None:
                            rating_observation = ((media_id_for_watch, None), watch_state["user_rating"])
                        else:
                            existing_r = existing_ratings.get(media_id_for_watch)
                            if not existing_r or existing_r.rating != watch_state["user_rating"]:
                                if existing_r:
                                    existing_r.rating = watch_state["user_rating"]
                                else:
                                    new_r = Rating(user_id=user_id, media_id=media_id_for_watch, rating=watch_state["user_rating"])
                                    db.add(new_r)
                                    existing_ratings[media_id_for_watch] = new_r
                                if new_ratings is not None:
                                    new_ratings[(media_id_for_watch, None)] = watch_state["user_rating"]

            # Savepoint committed, so queue the collection's add-date only now:
            # an item that rolled back must not leave a heal behind for work
            # that was undone.
            if heal_collection_id is not None and added_at is not None:
                queued = collection_heals.get(heal_collection_id)
                if queued is None or added_at < queued:
                    collection_heals[heal_collection_id] = added_at
            if rating_observation is not None and observed_ratings is not None:
                observed_ratings[rating_observation[0]] = rating_observation[1]

            # Savepoint committed - update pre-loaded caches so duplicates within the
            # same sync batch reuse the newly created media instead of creating another.
            if new_media:
                if media_type == MediaType.episode and new_media.show_id:
                    media_by_episode[(new_media.show_id, new_media.season_number, new_media.episode_number)] = new_media
                elif new_media.tmdb_id:
                    media_by_tmdb[(new_media.tmdb_id, new_media.media_type)] = new_media

        except Exception as e:
            if str(e) == "Skip this item (unmatched)":
                continue
            # Savepoint already rolled back — remove the enrichment entry we may have queued
            if new_media and new_media_for_enrichment and new_media_for_enrichment[-1][0] is new_media:
                new_media_for_enrichment.pop()
            stats["errors"] += 1
            print(f"    Error syncing item {i}: {e}")

        if (i + 1) % BATCH_SIZE == 0:
            await flush_collection_heals()
            await db.commit()
            if job_id:
                await db.execute(
                    update(SyncJob)
                    .where(SyncJob.id == job_id)
                    .values(processed_items=SyncJob.processed_items + BATCH_SIZE, updated_at=func.now())
                )
                await db.commit()
                await raise_if_cancelled(db, job_id)
            print(f"    Processed {i+1}/{len(items)} items...")

    await flush_collection_heals()
    await db.commit()
    processed_remainder = len(items) % BATCH_SIZE
    if job_id and processed_remainder > 0:
        await db.execute(
            update(SyncJob)
            .where(SyncJob.id == job_id)
            .values(processed_items=SyncJob.processed_items + processed_remainder, updated_at=func.now())
        )
        await db.commit()
        await raise_if_cancelled(db, job_id)

    # ── Phase 3: Batch enrich newly created media ─────────────────────────────
    warnings: list[dict] = []
    if new_media_for_enrichment:
        unique_seasons = len({(stid, m.season_number) for m, stid in new_media_for_enrichment if m.media_type == MediaType.episode and stid})
        print(f"  Enriching {len(new_media_for_enrichment)} new items ({unique_seasons} unique seasons)...")

        # Build series_tmdb_id → source title map so warnings can name the show
        series_title_map: dict[int, str] = {}
        if media_type == MediaType.episode:
            for item in items:
                if source in _MEDIA_BROWSER_ITEM_SOURCES:
                    parent_id = str(item.get("SeriesId", ""))
                    title = item.get("SeriesName")
                else:
                    parent_id = str(item.get("grandparentRatingKey", ""))
                    title = item.get("grandparentTitle")
                if parent_id and title:
                    show_id = show_map.get(parent_id)
                    if show_id:
                        series_tmdb_id = show_id_to_tmdb.get(show_id)
                        if series_tmdb_id:
                            series_title_map[series_tmdb_id] = title

        warnings = await batch_enrich_items(
            db, new_media_for_enrichment, api_key=api_key, show_title_map=series_title_map, user_id=user_id
        )
        await db.commit()

    all_warnings = skipped_warnings + warnings
    print(f"  Finished syncing {media_type.value}s. Stats: {stats}")
    return all_warnings


async def run_jellyfin_sync(user_id: int, job_id: int, movie_limit: int, show_limit: int, connection_id: int | None = None):
    async with _sync_semaphore:
        await _run_media_browser_sync("jellyfin", user_id, job_id, movie_limit, show_limit, connection_id)


async def run_emby_sync(user_id: int, job_id: int, movie_limit: int, show_limit: int, connection_id: int | None = None):
    async with _sync_semaphore:
        await _run_media_browser_sync("emby", user_id, job_id, movie_limit, show_limit, connection_id)


async def _run_media_browser_sync(provider: Literal["jellyfin", "emby"], user_id: int, job_id: int, movie_limit: int, show_limit: int, connection_id: int | None = None):
    adapter, selection_model = (jellyfin, JellyfinLibrarySelection) if provider == "jellyfin" else (emby, EmbyLibrarySelection)
    provider_name = "Jellyfin" if provider == "jellyfin" else "Emby"
    source = CollectionSource(provider)
    stats = {"movies": 0, "episodes": 0, "skipped": 0, "errors": 0}
    print(f"Starting {provider_name} sync for user {user_id}, job {job_id}")
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        try:
            if not await mark_job_running_unless_cancelled(db, job_id, processed_items=0, total_items=0):
                print(f"{provider_name} sync job {job_id} was cancelled before it started - skipping")
                return

            settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
            settings = settings_result.scalar_one_or_none()
            tmdb_api_key = await settings_store.get_effective_tmdb_key(db, settings)

            # Scope the pull to the requested connection or the oldest one for this provider.
            conn_q = select(MediaServerConnection).where(
                MediaServerConnection.user_id == user_id,
                MediaServerConnection.type == provider,
            )
            has_connection_id = bool(connection_id) if provider == "jellyfin" else connection_id is not None
            if has_connection_id:
                conn_q = conn_q.where(MediaServerConnection.id == connection_id)
            else:
                conn_q = conn_q.order_by(MediaServerConnection.id.asc()).limit(1)
            conn_result = await db.execute(conn_q)
            conn = conn_result.scalar_one_or_none()

            # Jellyfin requires metadata enrichment; Emby also supports pulls without a TMDB key.
            err = None
            if provider == "jellyfin" and (not conn or not tmdb_api_key):
                err = "Missing Jellyfin connection or TMDB API key"
            elif provider == "emby" and (not conn or not conn.url or not conn.token or not conn.server_user_id):
                err = "Missing Emby connection (URL, Token, or User ID)"
            if err:
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed, error_message=err))
                await db.commit()
                return

            server_url, server_token, server_user = conn.url, conn.token, conn.server_user_id

            print(f"  Fetching libraries from {server_url}")
            libraries = await adapter.get_libraries(server_url, server_token, server_user)

            sel_result = await db.execute(
                select(selection_model).where(selection_model.connection_id == conn.id)
            )
            selected_ids = {row.library_id for row in sel_result.scalars().all()}
            if selected_ids:
                libraries = [lib for lib in libraries if lib.get("Id") in selected_ids]

            print(f"  Found {len(libraries)} libraries to sync")
            all_warnings: list[dict] = []
            total_discovered = 0
            _new_watched: set[int] = set()
            _observed_ratings: RatingChanges = {}
            _new_collected: set[int] = set()
            _push_back: dict[int, str] = {}
            _seen_collection_source_ids: set[str] = set()

            for lib in libraries:
                lib_type = (lib.get("CollectionType") or "").lower()
                lib_id = lib.get("Id")
                lib_name = lib.get("Name")
                print(f"  Processing library: {lib_name} ({lib_type})")

                if lib_type == "movies":
                    items = await adapter.get_movies(lib_id, server_url, server_token, server_user)

                    if movie_limit:
                        items = items[:movie_limit]

                    movies_without_tmdb = [
                        m for m in items
                        if not get_jellyfin_tmdb_id(m.get("ProviderIds", {}))
                        and (m.get("ProviderIds", {}).get("Imdb") or m.get("Name"))
                    ]
                    if movies_without_tmdb:
                        print(f"    Resolving {len(movies_without_tmdb)} movies via IMDb/title fallback...")
                        semaphore = asyncio.Semaphore(TMDB_CONCURRENCY)

                        async def resolve_movie_tmdb_id(m: dict) -> None:
                            async with semaphore:
                                pids = m.get("ProviderIds", {})
                                imdb_id = pids.get("Imdb") or pids.get("imdb")
                                try:
                                    tid = await tmdb.resolve_movie_id(imdb_id, m.get("Name"), year=m.get("ProductionYear"), api_key=tmdb_api_key)
                                    if tid is not None:
                                        m.setdefault("ProviderIds", {})["Tmdb"] = str(tid)
                                except Exception as e:
                                    print(f"    Could not resolve movie '{m.get('Name')}': {e}")

                        await asyncio.gather(*[resolve_movie_tmdb_id(m) for m in movies_without_tmdb])

                    total_discovered += len(items)
                    await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(total_items=total_discovered, current_step="Pulling movies"))
                    await db.commit()

                    w = await sync_items(items, MediaType.movie, source, db, stats, user_id, job_id, api_key=tmdb_api_key,
                        sync_collection=conn.sync_collection, sync_watched=conn.sync_watched, sync_ratings=conn.sync_ratings,
                        new_watched_ids=_new_watched, observed_ratings=_observed_ratings, new_collected_ids=_new_collected, connection_id=conn.id, push_back=_push_back,
                        seen_source_ids=_seen_collection_source_ids)
                    all_warnings.extend(w)

                elif lib_type in ("tvshows", "tv"):
                    shows = await adapter.get_shows(lib_id, server_url, server_token, server_user)
                    if show_limit:
                        shows = shows[:show_limit]

                    series_tmdb_map = {
                        s.get("Id"): get_jellyfin_tmdb_id(s.get("ProviderIds", {}))
                        for s in shows if get_jellyfin_tmdb_id(s.get("ProviderIds", {}))
                    }

                    total_discovered += len(series_tmdb_map)
                    await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(total_items=total_discovered, current_step="Pulling shows"))
                    await db.commit()

                    print(f"    Mapping {len(series_tmdb_map)} shows to TMDB...")
                    show_map, show_id_to_tmdb = await sync_shows_batch(series_tmdb_map, db, api_key=tmdb_api_key)
                    unmatched_shows = [s for s in shows if str(s.get("Id")) not in show_map]
                    for s in unmatched_shows:
                        all_warnings.append({
                            "title": s.get("Name"),
                            "media_type": "series",
                            "source_id": str(s.get("Id")),
                            "reason": "Unmatched on source — no TMDB ID available for the series",
                        })

                    items = await adapter.get_episodes(lib_id, server_url, server_token, server_user)
                    filtered_episodes = [e for e in items if str(e.get("SeriesId")) in show_map]
                    unmatched_series_ids = {str(s.get("Id")) for s in shows if str(s.get("Id")) not in show_map}
                    unmatched_series_episodes = [e for e in items if str(e.get("SeriesId")) in unmatched_series_ids]

                    total_discovered = total_discovered - len(series_tmdb_map) + len(filtered_episodes) + len(unmatched_series_episodes)
                    await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(total_items=total_discovered, current_step="Pulling episodes"))
                    await db.commit()

                    w = await sync_items(
                        filtered_episodes, MediaType.episode, source,
                        db, stats, user_id, job_id, show_map,
                        api_key=tmdb_api_key, show_id_to_tmdb=show_id_to_tmdb,
                        sync_collection=conn.sync_collection, sync_watched=conn.sync_watched, sync_ratings=conn.sync_ratings,
                        new_watched_ids=_new_watched, observed_ratings=_observed_ratings, new_collected_ids=_new_collected, connection_id=conn.id, push_back=_push_back,
                        seen_source_ids=_seen_collection_source_ids,
                    )
                    all_warnings.extend(w)

                    if unmatched_series_episodes:
                        w = await sync_items(
                            unmatched_series_episodes, MediaType.episode, source,
                            db, stats, user_id, job_id, {},
                            api_key=tmdb_api_key, show_id_to_tmdb={},
                            sync_collection=conn.sync_collection, sync_watched=conn.sync_watched, sync_ratings=conn.sync_ratings,
                            new_watched_ids=_new_watched, observed_ratings=_observed_ratings, new_collected_ids=_new_collected, connection_id=conn.id, push_back=_push_back,
                            seen_source_ids=_seen_collection_source_ids,
                        )
                        all_warnings.extend(w)

            if conn.sync_collection and not movie_limit and not show_limit:
                removed_media_ids = await _remove_stale_collection_files(
                    db, user_id, source, conn.id, _seen_collection_source_ids,
                )
                if removed_media_ids:
                    stats["removed"] = len(removed_media_ids)
                    await db.commit()
                    print(f"{provider_name} sync job {job_id}: removed {len(removed_media_ids)} item(s) no longer in {provider_name}.")

            if not movie_limit and not show_limit and not stats["errors"]:
                pushed_back = await _push_watched_back_to_source(db, user_id, conn, _push_back)
                if pushed_back:
                    print(f"{provider_name} sync job {job_id}: pushed watched state for {pushed_back} newly collected item(s).")

            print(f"{provider_name} sync job {job_id} completed. Stats: {stats}")
            from core.media_server_reconciliation import reconcile_media_server_pull
            accepted_watched, accepted_ratings = await reconcile_media_server_pull(
                db, conn, stats, _new_watched, _observed_ratings,
                complete=not movie_limit and not show_limit and not stats["errors"],
            )
            all_warnings = await _stamp_matched_show_warnings(db, user_id, all_warnings)
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.completed, stats=stats, warnings=all_warnings or None, updated_at=func.now()))
            await db.commit()
            from core.pull_propagation import propagate_media_server_pull
            await propagate_media_server_pull(
                db, conn=conn, watched_ids=accepted_watched, ratings=accepted_ratings,
            )
            asyncio.create_task(pre_cache_all_collected_bg())
        except SyncCancelled:
            print(f"{provider_name} sync job {job_id} cancelled")
            await db.rollback()
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.cancelled, stats=stats, updated_at=func.now()))
            await db.commit()
        except Exception as e:
            print(f"{provider_name} sync job {job_id} failed: {e}")
            import traceback
            traceback.print_exc()
            await db.rollback()
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed, error_message=str(e)[:900]))
            await db.commit()


_BACKFILL_CHUNK = 50


async def _backfill_plex_languages(user_id: int, connection_id: int, p_url: str, p_token: str, job_id: int | None = None) -> int:
    """Fetch full item detail from Plex for CollectionFiles that have no language data yet.

    Runs in its own DB session so the main sync connection is released before this
    long-running phase starts. Processes in chunks to avoid holding a transaction open
    across thousands of outbound HTTP calls.
    """
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        result = await db.execute(
            select(CollectionFile)
            .join(Collection, Collection.id == CollectionFile.collection_id)
            .where(
                Collection.user_id == user_id,
                CollectionFile.source == CollectionSource.plex,
                CollectionFile.connection_id == connection_id,
                CollectionFile.source_id.isnot(None),
                (CollectionFile.audio_languages == None) | (CollectionFile.audio_languages.cast(JSONB) == cast([], JSONB)),
            )
        )
        files = result.scalars().all()
        if not files:
            return 0

        total = len(files)
        print(f"  Backfilling language data for {total} Plex file(s)...")

        if job_id is not None:
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(processed_items=0, total_items=total, current_step="Backfilling file details"))
            await db.commit()

        sem = asyncio.Semaphore(10)

        async def _fetch_quality(cf: CollectionFile) -> tuple[int, dict]:
            async with sem:
                item = await plex.get_item(p_url, p_token, cf.source_id)
                if not item:
                    return cf.id, {}
                return cf.id, plex.extract_quality(item.get("Media", []))

        done = 0
        for chunk_start in range(0, total, _BACKFILL_CHUNK):
            chunk = files[chunk_start:chunk_start + _BACKFILL_CHUNK]
            cf_map = {cf.id: cf for cf in chunk}

            results = await asyncio.gather(*[_fetch_quality(cf) for cf in chunk], return_exceptions=True)

            for res in results:
                if isinstance(res, Exception):
                    continue
                cf_id, quality = res
                cf = cf_map.get(cf_id)
                if cf and quality:
                    if quality.get("audio_languages"):
                        cf.audio_languages = quality["audio_languages"]
                    if quality.get("subtitle_languages"):
                        cf.subtitle_languages = quality["subtitle_languages"]

            done += len(chunk)
            await db.commit()

            if job_id is not None:
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(processed_items=done))
                await db.commit()

        return total


PLEX_PENDING_PUSH_MAX_AGE = timedelta(hours=1)


async def _push_watched_back_to_source(
    db: AsyncSession,
    user_id: int,
    conn: MediaServerConnection,
    push_back: dict[int, str],
) -> int:
    """Push Scrob's existing watched state to ``conn`` for items that just
    appeared in its library (#420): something marked watched in Scrob before it
    was collected would otherwise stay unwatched on the server until a full push.

    Gated on the connection's own push_watched flag, and unlike the rest of a
    pull this is the one thing it pushes back - to the very server it just
    read, and only for items that server had no watch state for. Jellyfin/Emby
    get the original watch date; Plex's /:/scrobble has no timestamp parameter,
    so it stamps its own receipt time (see PlexPendingPush). Returns how many
    pushes succeeded.
    """
    if not push_back or not conn.push_watched or conn.type not in ("plex", "jellyfin", "emby"):
        return 0

    from models.tracking import StreamBaseline
    baseline = await db.get(StreamBaseline, conn.id)
    if baseline is None or not baseline.approved:
        return 0


    # A combined episode file may map to several media rows. Marking it played
    # is safe only when every episode in that file has a completed watch.
    source_ids = set(push_back.values())
    by_source_id: dict[str, set[int]] = {}
    source_id_list = list(source_ids)
    for offset in range(0, len(source_id_list), _MAX_IN_PARAMS):
        rows = await db.execute(
            select(CollectionFile.source_id, Collection.media_id)
            .join(Collection, Collection.id == CollectionFile.collection_id)
            .where(
                CollectionFile.connection_id == conn.id,
                CollectionFile.source_id.in_(source_id_list[offset : offset + _MAX_IN_PARAMS]),
            )
        )
        for source_id, media_id in rows.all():
            by_source_id.setdefault(source_id, set()).add(media_id)
    all_media_ids = {media_id for media_ids in by_source_id.values() for media_id in media_ids}
    watched_at_by_media = await db_queries.latest_watched_at(db, user_id, list(all_media_ids))
    sem = asyncio.Semaphore(20)

    async def _push_one(source_id: str, media_ids: set[int]) -> bool:
        async with sem:
            try:
                if not media_ids or not media_ids.issubset(watched_at_by_media):
                    return False
                if conn.type == "plex":
                    return await outbound_sync.push_plex_watched_and_record(conn, source_id, user_id, min(media_ids))
                # Registered before the call so the server's UserDataSaved echo
                # can't beat it (#247/#251).
                for media_id in media_ids:
                    watch_echo.mark_pushed_watched(user_id, media_id)
                push = jellyfin.mark_watched if conn.type == "jellyfin" else emby.mark_watched
                dates = [watched_at_by_media[media_id] for media_id in media_ids if watched_at_by_media[media_id] is not None]
                return await push(
                    conn.url, conn.token, conn.server_user_id, source_id,
                    played_at=max(dates) if dates else None,
                )
            except Exception:
                return False

    results = await asyncio.gather(*[_push_one(sid, mids) for sid, mids in by_source_id.items()])
    return sum(1 for ok in results if ok)


PLEX_WEBHOOK_RECONCILE_WINDOW = timedelta(minutes=10)


PLEX_CONFIRMED_RECONCILE_WINDOW = timedelta(minutes=2)


_PLEX_HISTORY_CHUNK = 200


async def _backfill_plex_watch_history(
    user_id: int,
    connection_id: int,
    p_url: str,
    p_token: str,
    server_username: str | None,
    ratingkey_to_media: dict[str, int],
    job_id: int | None = None,
    window_minutes: int = 0,
) -> tuple[int, int]:
    """Import every distinct Plex play as its own WatchEvent, not just the most
    recent one — Plex's library-scan endpoints (get_movies/get_shows/get_episodes)
    only expose aggregate viewCount/lastViewedAt, which is why the regular
    sync_items() pass can only ever record a single WatchEvent per item
    (see GitHub #126). This uses Plex's actual per-play history endpoint instead,
    mirroring Trakt's /sync/history import.

    Every WatchEvent this function itself previously wrote has a watched_at
    computed identically from Plex's own viewedAt, so re-runs dedup those by an
    exact (media_id, watched_at) match — no tolerance needed, it's deterministic.
    The one non-deterministic case is a WatchEvent the real-time Plex webhook
    already wrote for this same play (see webhooks.py:_write_watch_event):
    its watched_at is this server's receipt time, not Plex's, so it can differ
    from the authoritative viewedAt here by a webhook-latency-sized gap. Those
    rows are marked provisional=True specifically so this function can find and
    correct the one that matches, instead of either exact-matching (missing it)
    or fuzzy-matching every existing play regardless of source (over-merging
    unrelated history — see GitHub #135's original fix attempt).

    Runs in its own DB session, same rationale as _backfill_plex_languages.
    ratingkey_to_media is built by the sync_items() calls that just ran (via
    their ratingkey_to_media_id out-param), not queried from CollectionFile —
    CollectionFile rows only ever exist when sync_collection is enabled, but
    watched-history sync is an independent setting, so relying on CollectionFile
    here would leave every play unmatched on a watched-only connection.

    Returns (new_events, reconciled, unmatched) for the caller to log.
    """
    from collections import defaultdict

    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        conn = await db.get(MediaServerConnection, connection_id)
        if not conn:
            return 0, 0, 0

        account_id = await plex.get_account_id(p_url, p_token, server_username) if server_username else None

        # Always a full pull, not bounded by a saved cursor - an incremental
        # cursor that advances on every run (even one that ends up matching
        # nothing, e.g. before server_username was configured correctly) can
        # permanently skip real history that was never actually imported.
        # Every previously-imported play still dedupes by exact (media_id,
        # watched_at) below, so re-scanning the full history each time is
        # safe, just not the cheapest possible option.
        history = await plex.get_history(p_url, p_token, since=None)
        history = [h for h in history if h.get("type") in ("movie", "episode")]
        if account_id is not None:
            history = [h for h in history if h.get("accountID") == account_id]
        elif len({h.get("accountID") for h in history if h.get("accountID") is not None}) > 1:
            print(
                f"  Plex history for connection {connection_id} spans multiple accounts and "
                f"no server_username is configured — plays from other users on this server "
                f"may be attributed to this connection. Configure a Plex username to scope this."
            )

        if not history:
            return 0, 0, 0

        if job_id is not None:
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(processed_items=0, total_items=len(history), current_step="Backfilling watch history"))
            await db.commit()

        we_res = await db.execute(
            select(WatchEvent.id, WatchEvent.media_id, WatchEvent.watched_at, WatchEvent.provisional)
            .where(WatchEvent.user_id == user_id)
        )
        confirmed_watched_by_media: dict[int, set[datetime]] = defaultdict(set)
        # media_id -> list of (event_id, watched_at) still awaiting reconciliation
        provisional_by_media: dict[int, list[tuple[int, datetime]]] = defaultdict(list)
        for event_id, media_id, watched_at, provisional in we_res:
            if watched_at is None:
                continue
            if provisional:
                provisional_by_media[media_id].append((event_id, watched_at))
            else:
                confirmed_watched_by_media[media_id].add(watched_at)

        # window_minutes is the user's raw configured override (0 if unset) -
        # deliberately NOT the floored effective value the general dedup
        # setting uses elsewhere (core.watch_dedup.DEFAULT_DEDUP_WINDOW_MINUTES
        # = 5), which would otherwise widen PLEX_CONFIRMED_RECONCILE_WINDOW's
        # tuned 2-minute echo check by default and reintroduce #320 (a genuine
        # distinct play a few minutes after a confirmed one wrongly suppressed
        # as an echo). An explicit override only ever widens these two
        # reconcile windows beyond their tuned defaults, never narrows them.
        user_window = timedelta(minutes=window_minutes)
        provisional_window = max(PLEX_WEBHOOK_RECONCILE_WINDOW, user_window)
        confirmed_window = max(PLEX_CONFIRMED_RECONCILE_WINDOW, user_window)

        def _closest_provisional(media_id: int, watched_at: datetime) -> tuple[int, datetime] | None:
            candidates = provisional_by_media.get(media_id) or []
            in_range = [
                c for c in candidates
                if abs((watched_at - c[1]).total_seconds()) <= provisional_window.total_seconds()
            ]
            if not in_range:
                return None
            return min(in_range, key=lambda c: abs((watched_at - c[1]).total_seconds()))

        def _has_nearby_confirmed(media_id: int, watched_at: datetime) -> bool:
            candidates = confirmed_watched_by_media.get(media_id) or set()
            return any(
                abs((watched_at - c).total_seconds()) <= confirmed_window.total_seconds()
                for c in candidates
            )

        pp_res = await db.execute(
            select(PlexPendingPush.id, PlexPendingPush.media_id, PlexPendingPush.pushed_at)
            .where(
                PlexPendingPush.user_id == user_id,
                PlexPendingPush.pushed_at >= datetime.utcnow() - PLEX_PENDING_PUSH_MAX_AGE,
            )
        )
        # media_id -> list of (pending_push_id, pushed_at) still awaiting their echo
        pending_push_by_media: dict[int, list[tuple[int, datetime]]] = defaultdict(list)
        for pp_id, media_id, pushed_at in pp_res:
            pending_push_by_media[media_id].append((pp_id, pushed_at))

        # Opportunistically clear out this user's pending-push rows that missed
        # their echo's reconcile window for good - nothing left to match them
        # against, and a rewatch of the same media later shouldn't be able to
        # accidentally match a push from long ago.
        await db.execute(
            delete(PlexPendingPush).where(
                PlexPendingPush.user_id == user_id,
                PlexPendingPush.pushed_at < datetime.utcnow() - PLEX_PENDING_PUSH_MAX_AGE,
            )
        )

        def _closest_pending_push(media_id: int, watched_at: datetime) -> tuple[int, datetime] | None:
            candidates = pending_push_by_media.get(media_id) or []
            in_range = [
                c for c in candidates
                if abs((watched_at - c[1]).total_seconds()) <= PLEX_CONFIRMED_RECONCILE_WINDOW.total_seconds()
            ]
            if not in_range:
                return None
            return min(in_range, key=lambda c: abs((watched_at - c[1]).total_seconds()))

        new_events = 0
        reconciled = 0
        unmatched = 0
        for i, entry in enumerate(history):
            media_id = ratingkey_to_media.get(str(entry.get("ratingKey")))
            if media_id is None:
                unmatched += 1
                continue
            viewed_at = entry.get("viewedAt")
            if not viewed_at:
                unmatched += 1
                continue
            watched_at = datetime.fromtimestamp(viewed_at, tz=timezone.utc).replace(tzinfo=None)

            if watched_at in confirmed_watched_by_media.get(media_id, ()):
                pass  # already recorded by a previous run of this same backfill
            elif (match := _closest_provisional(media_id, watched_at)) is not None:
                # Confirm the webhook's estimate with Plex's authoritative time,
                # rather than inserting a second row for the same play.
                match_id, match_watched_at = match
                await db.execute(
                    update(WatchEvent).where(WatchEvent.id == match_id).values(watched_at=watched_at, provisional=False)
                )
                provisional_by_media[media_id].remove(match)
                confirmed_watched_by_media[media_id].add(watched_at)
                reconciled += 1
            elif _has_nearby_confirmed(media_id, watched_at):
                # Echo of Scrob's own synchronous push (#320) - the existing
                # confirmed event's own watched_at is left as-is (it's the
                # user's own action time, more meaningful than Plex's
                # push-receipt time), just don't insert a second row for it.
                reconciled += 1
            elif (pp_match := _closest_pending_push(media_id, watched_at)) is not None:
                # Echo of a push whose original watch was recorded long
                # before it was pushed (#320) - too large a gap from the
                # existing confirmed event's own watched_at for
                # _has_nearby_confirmed above to catch, but close to when
                # this connection actually told Plex to mark it watched.
                # That existing event already covers this play; consume the
                # pending marker instead of inserting a second row.
                pp_id, _ = pp_match
                await db.execute(delete(PlexPendingPush).where(PlexPendingPush.id == pp_id))
                pending_push_by_media[media_id].remove(pp_match)
                reconciled += 1
            else:
                watch_event = WatchEvent(
                    user_id=user_id,
                    media_id=media_id,
                    watched_at=watched_at,
                    completed=True,
                    play_count=1,
                )
                db.add(watch_event)
                await db.flush()
                await record_rewatch_progress(db, user_id, media_id, watch_event.id)
                confirmed_watched_by_media[media_id].add(watched_at)
                new_events += 1

            if (i + 1) % _PLEX_HISTORY_CHUNK == 0:
                await db.commit()
                if job_id is not None:
                    await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(processed_items=i + 1, total_items=len(history)))
                    await db.commit()

        await db.commit()
        return new_events, reconciled, unmatched


def plex_sync_needs_library_scan(conn) -> bool:
    """Whether _run_plex_sync's per-library scan (movies/shows/episodes)
    should run at all. That scan only ever produces collection/watched/
    ratings data, so a connection with all three of those off - e.g. a
    watchlist-only pull - must skip it rather than re-fetching and
    iterating the user's entire Plex library just to throw the results
    away. The watchlist pull itself is a separate step that doesn't depend
    on this scan having run."""
    return bool(conn.sync_collection or conn.sync_watched or conn.sync_ratings)


async def run_plex_sync(user_id: int, job_id: int, movie_limit: int, show_limit: int, connection_id: int | None = None):
    async with _sync_semaphore:
        await _run_plex_sync(user_id, job_id, movie_limit, show_limit, connection_id)


async def _run_plex_sync(user_id: int, job_id: int, movie_limit: int, show_limit: int, connection_id: int | None = None):
    print(f"Starting Plex sync for user {user_id}, job {job_id}")
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        try:
            if not await mark_job_running_unless_cancelled(
                db, job_id, processed_items=0, total_items=0, current_step="Pulling library",
            ):
                print(f"Plex sync job {job_id} was cancelled before it started - skipping")
                return

            if connection_id is not None:
                conn_result = await db.execute(
                    select(MediaServerConnection).where(
                        MediaServerConnection.id == connection_id,
                        MediaServerConnection.user_id == user_id,
                        MediaServerConnection.type == "plex",
                    )
                )
            else:
                conn_result = await db.execute(
                    select(MediaServerConnection).where(
                        MediaServerConnection.user_id == user_id,
                        MediaServerConnection.type == "plex",
                    ).order_by(MediaServerConnection.id.asc()).limit(1)
                )
            conn = conn_result.scalar_one_or_none()

            settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
            settings = settings_result.scalar_one_or_none()
            tmdb_api_key = await settings_store.get_effective_tmdb_key(db, settings)

            if not conn or not conn.url or not conn.token:
                err = "Missing Plex connection (URL or Token)"
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed, error_message=err))
                await db.commit()
                return

            p_url = conn.url
            p_token = conn.token

            # Plex's bulk library-listing endpoints (get_movies/get_shows/get_episodes)
            # only ever return viewCount/lastViewedAt/userRating for whichever account
            # the token itself belongs to - there's no per-Home-user view of that data,
            # unlike Jellyfin/Emby's admin-key-plus-user-id model. A connection scoped
            # to a specific Home user via server_username can share the same token as
            # another connection (e.g. the server owner's own), so trusting this bulk
            # watched-status data here would silently attribute the token owner's own
            # plays to whichever Scrob account this connection belongs to.
            # _backfill_plex_watch_history below is the one watched-status path that's
            # actually account-scoped (via Plex's real per-play history + account_id
            # filtering), so it stays the sole source of truth whenever server_username
            # is set - this only affects the redundant bulk pass, not sync_watched
            # itself, which still gates the (correct) backfill call further down.
            plex_watched_state_is_reliable = conn.sync_watched and not conn.server_username

            print("  Fetching Plex libraries...")
            libraries = await plex.get_libraries(p_url, p_token)

            sel_result = await db.execute(
                select(PlexLibrarySelection).where(PlexLibrarySelection.connection_id == conn.id)
            )
            selected_keys = {row.library_key for row in sel_result.scalars().all()}
            if selected_keys:
                libraries = [lib for lib in libraries if lib.get("key") in selected_keys]

            did_library_scan = plex_sync_needs_library_scan(conn)
            if not did_library_scan:
                print("  Skipping library scan - collection/watched/ratings sync are all disabled for this connection")
                libraries = []

            print(f"  Found {len(libraries)} libraries to sync")
            stats = {"movies": 0, "episodes": 0, "ratings": 0, "skipped": 0, "errors": 0}
            all_warnings: list[dict] = []
            total_discovered = 0
            _new_watched: set[int] = set()
            _observed_ratings: RatingChanges = {}
            _new_collected: set[int] = set()
            _push_back: dict[int, str] = {}
            _seen_collection_source_ids: set[str] = set()
            # ratingKey -> media_id, accumulated across every movie/show library this
            # run so _backfill_plex_watch_history can resolve play history afterward —
            # built here (not via CollectionFile) since it must exist even when
            # sync_collection is off, as watched-history sync doesn't depend on it.
            _plex_ratingkey_to_media: dict[str, int] = {}
            for lib in libraries:
                lib_type = lib.get("type")
                lib_key = lib.get("key")
                lib_title = lib.get("title")
                print(f"  Processing library: {lib_title} ({lib_type})")

                if lib_type == "movie":
                    items = await plex.get_movies(p_url, p_token, lib_key)
                    if movie_limit:
                        items = items[:movie_limit]

                    movies_without_tmdb = [
                        m for m in items
                        if not plex.extract_tmdb_id(m.get("Guid", []))
                        and (plex.extract_imdb_id(m.get("Guid", [])) or m.get("title"))
                    ]
                    if movies_without_tmdb:
                        print(f"    Resolving {len(movies_without_tmdb)} movies via IMDb/title fallback...")
                        semaphore = asyncio.Semaphore(TMDB_CONCURRENCY)

                        async def resolve_movie_tmdb_id(m: dict) -> None:
                            async with semaphore:
                                guids = m.get("Guid", [])
                                imdb_id = plex.extract_imdb_id(guids)
                                try:
                                    tid = await tmdb.resolve_movie_id(imdb_id, m.get("title"), year=m.get("year"), api_key=tmdb_api_key)
                                    if tid is not None:
                                        m.setdefault("Guid", []).append({"id": f"tmdb://{tid}"})
                                except Exception as e:
                                    print(f"    Could not resolve movie '{m.get('title')}': {e}")

                        await asyncio.gather(*[resolve_movie_tmdb_id(m) for m in movies_without_tmdb])

                    total_discovered += len(items)
                    await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(total_items=total_discovered, current_step="Pulling movies"))
                    await db.commit()

                    w = await sync_items(items, MediaType.movie, CollectionSource.plex, db, stats, user_id, job_id, api_key=tmdb_api_key,
                        sync_collection=conn.sync_collection, sync_watched=plex_watched_state_is_reliable, sync_ratings=conn.sync_ratings,
                        new_watched_ids=_new_watched, observed_ratings=_observed_ratings, new_collected_ids=_new_collected, connection_id=conn.id, push_back=_push_back,
                        ratingkey_to_media_id=_plex_ratingkey_to_media, seen_source_ids=_seen_collection_source_ids)
                    all_warnings.extend(w)

                elif lib_type == "show":
                    shows = await plex.get_shows(p_url, p_token, lib_key)
                    if show_limit:
                        shows = shows[:show_limit]

                    series_tmdb_map = {
                        s.get("ratingKey"): plex.extract_tmdb_id(plex.get_guids(s))
                        for s in shows if plex.extract_tmdb_id(plex.get_guids(s))
                    }

                    shows_without_tmdb = [
                        s for s in shows
                        if s.get("ratingKey") not in series_tmdb_map
                        and (plex.extract_tvdb_id(plex.get_guids(s)) or plex.extract_imdb_id(plex.get_guids(s)))
                    ]

                    if shows_without_tmdb:
                        print(f"    Resolving {len(shows_without_tmdb)} shows via TVDB/IMDb fallback...")
                        semaphore = asyncio.Semaphore(TMDB_CONCURRENCY)

                        async def resolve_show_tmdb_id(s: dict) -> None:
                            async with semaphore:
                                guids = plex.get_guids(s)
                                tvdb_id = plex.extract_tvdb_id(guids)
                                imdb_id = plex.extract_imdb_id(guids)
                                try:
                                    if tvdb_id:
                                        res = await tmdb.find_by_external_id(tvdb_id, "tvdb_id", api_key=tmdb_api_key)
                                        if res.get("tv_results"):
                                            series_tmdb_map[s["ratingKey"]] = res["tv_results"][0]["id"]
                                            return
                                    if imdb_id:
                                        res = await tmdb.find_by_external_id(imdb_id, "imdb_id", api_key=tmdb_api_key)
                                        if res.get("tv_results"):
                                            series_tmdb_map[s["ratingKey"]] = res["tv_results"][0]["id"]
                                            return
                                    title = s.get("title") or s.get("titleSort")
                                    if title:
                                        res = await tmdb.search_shows(title, api_key=tmdb_api_key)
                                        if res.get("results"):
                                            series_tmdb_map[s["ratingKey"]] = res["results"][0]["id"]
                                except Exception as e:
                                    print(f"    Could not resolve show '{s.get('title')}': {e}")

                        await asyncio.gather(*[resolve_show_tmdb_id(s) for s in shows_without_tmdb])

                    total_discovered += len(series_tmdb_map)
                    await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(total_items=total_discovered, current_step="Pulling shows"))
                    await db.commit()

                    print(f"    Mapping {len(series_tmdb_map)} shows to TMDB...")
                    show_map, show_id_to_tmdb = await sync_shows_batch(
                        series_tmdb_map, db, api_key=tmdb_api_key
                    )
                    print(f"    Mapped {len(show_map)}/{len(series_tmdb_map)} shows.")

                    if conn.sync_ratings:
                        seasons = await plex.get_seasons(p_url, p_token, lib_key)
                        show_titles = {
                            str(show.get("ratingKey")): str(show.get("title") or "")
                            for show in shows
                        }
                        rated_seasons = [
                            season
                            for season in seasons
                            if season.get("userRating") is not None
                        ]
                        total_discovered += len(rated_seasons)
                        for season in rated_seasons:
                            parent_key = str(season.get("parentRatingKey") or "")
                            show_id = show_map.get(parent_key)
                            show_tmdb_id = show_id_to_tmdb.get(show_id) if show_id else None
                            season_number = season.get("index")
                            if show_tmdb_id is None or season_number is None:
                                stats["skipped"] += 1
                                continue
                            try:
                                async with db.begin_nested():
                                    media = await get_or_create_series_media(
                                        db,
                                        show_tmdb_id,
                                        show_titles.get(parent_key, ""),
                                        tmdb_api_key,
                                    )
                                    key = (media.id, int(season_number))
                                    rating_value = float(season["userRating"])
                                    _observed_ratings[key] = rating_value
                            except Exception as exc:
                                logger.warning(
                                    "Error importing Plex season rating show=%s season=%s: %s",
                                    show_tmdb_id,
                                    season_number,
                                    exc,
                                )
                                stats["errors"] += 1

                        # Whole-show ratings (Plex lets you rate a series itself, not
                        # just its seasons/episodes) - same shape as a season rating
                        # but with season_number=None, matching how the app's own
                        # "rate this show" action on the show's main page stores it.
                        rated_shows = [show for show in shows if show.get("userRating") is not None]
                        total_discovered += len(rated_shows)
                        for show in rated_shows:
                            show_key = str(show.get("ratingKey") or "")
                            show_id = show_map.get(show_key)
                            show_tmdb_id = show_id_to_tmdb.get(show_id) if show_id else None
                            if show_tmdb_id is None:
                                stats["skipped"] += 1
                                continue
                            try:
                                async with db.begin_nested():
                                    media = await get_or_create_series_media(
                                        db,
                                        show_tmdb_id,
                                        show_titles.get(show_key, ""),
                                        tmdb_api_key,
                                    )
                                    key = (media.id, None)
                                    rating_value = float(show["userRating"])
                                    _observed_ratings[key] = rating_value
                            except Exception as exc:
                                logger.warning(
                                    "Error importing Plex show rating show=%s: %s",
                                    show_tmdb_id,
                                    exc,
                                )
                                stats["errors"] += 1

                    unmatched_shows = [s for s in shows if str(s.get("ratingKey")) not in show_map]
                    for s in unmatched_shows:
                        all_warnings.append({
                            "title": s.get("title"),
                            "media_type": "series",
                            "source_id": str(s.get("ratingKey")),
                            "plex_guids": [g.get("id", "") for g in plex.get_guids(s) if isinstance(g, dict)],
                            "reason": "Unmatched on source — no TMDB ID available for the series",
                        })

                    print(f"    Fetching episodes for {lib_title}...")
                    items = await plex.get_episodes(p_url, p_token, lib_key)
                    filtered_episodes = [i for i in items if str(i.get("grandparentRatingKey")) in show_map]
                    unmatched_ratingkeys = {str(s.get("ratingKey")) for s in shows if str(s.get("ratingKey")) not in show_map}
                    unmatched_series_episodes = [i for i in items if str(i.get("grandparentRatingKey")) in unmatched_ratingkeys]

                    total_discovered = total_discovered - len(series_tmdb_map) + len(filtered_episodes) + len(unmatched_series_episodes)
                    await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(total_items=total_discovered, current_step="Pulling episodes"))
                    await db.commit()

                    w = await sync_items(
                        filtered_episodes, MediaType.episode, CollectionSource.plex,
                        db, stats, user_id, job_id, show_map,
                        api_key=tmdb_api_key, show_id_to_tmdb=show_id_to_tmdb,
                        sync_collection=conn.sync_collection, sync_watched=plex_watched_state_is_reliable, sync_ratings=conn.sync_ratings,
                        new_watched_ids=_new_watched, observed_ratings=_observed_ratings, new_collected_ids=_new_collected, connection_id=conn.id, push_back=_push_back,
                        ratingkey_to_media_id=_plex_ratingkey_to_media, seen_source_ids=_seen_collection_source_ids,
                    )
                    all_warnings.extend(w)

                    if unmatched_series_episodes:
                        w = await sync_items(
                            unmatched_series_episodes, MediaType.episode, CollectionSource.plex,
                            db, stats, user_id, job_id, {},
                            api_key=tmdb_api_key, show_id_to_tmdb={},
                            sync_collection=conn.sync_collection, sync_watched=plex_watched_state_is_reliable, sync_ratings=conn.sync_ratings,
                            new_watched_ids=_new_watched, observed_ratings=_observed_ratings, new_collected_ids=_new_collected, connection_id=conn.id, push_back=_push_back,
                            ratingkey_to_media_id=_plex_ratingkey_to_media, seen_source_ids=_seen_collection_source_ids,
                        )
                        all_warnings.extend(w)

            # ── Plex watchlist ↔ Scrob list ──────────────────────────────────
            if conn.plex_sync_watchlist:
                await plex_watchlist.reconcile_watchlist(user_id, conn.id, tmdb_api_key)

            backfilled = await _backfill_plex_languages(user_id, conn.id, p_url, p_token, job_id)
            if backfilled:
                print(f"Plex sync job {job_id}: backfilled language data for {backfilled} file(s).")

            if conn.sync_watched:
                new_events, reconciled, unmatched = await _backfill_plex_watch_history(
                    user_id, conn.id, p_url, p_token, conn.server_username, _plex_ratingkey_to_media, job_id,
                    window_minutes=(settings.duplicate_watch_window_minutes or 0) if settings else 0,
                )
                if new_events or reconciled or unmatched:
                    print(
                        f"Plex sync job {job_id}: backfilled {new_events} historical play(s), "
                        f"reconciled {reconciled} webhook estimate(s) ({unmatched} unmatched)."
                    )

            # After the history backfill above, so it never sees this push's echo
            # in the same run - the pending-push record it leaves handles the next.
            pushed_back = await _push_watched_back_to_source(db, user_id, conn, _push_back) if not movie_limit and not show_limit and not stats["errors"] else 0
            if pushed_back:
                print(f"Plex sync job {job_id}: pushed watched state for {pushed_back} newly collected item(s).")

            if conn.sync_collection and did_library_scan and not movie_limit and not show_limit:
                removed_media_ids = await _remove_stale_collection_files(
                    db, user_id, CollectionSource.plex, conn.id, _seen_collection_source_ids,
                )
                if removed_media_ids:
                    stats["removed"] = len(removed_media_ids)
                    await db.commit()
                    print(f"Plex sync job {job_id}: removed {len(removed_media_ids)} item(s) no longer in Plex.")

            print(f"Plex sync job {job_id} completed. Stats: {stats}")
            from core.media_server_reconciliation import reconcile_media_server_pull
            accepted_watched, accepted_ratings = await reconcile_media_server_pull(
                db, conn, stats, _new_watched, _observed_ratings,
                complete=not movie_limit and not show_limit and not stats["errors"],
            )
            # The watchlist reconcile above is the one exception: it honors this
            # connection's own plex_push_watchlist flag in both jobs, so pull/push
            # scheduling order can't resurrect items removed on the other side.
            all_warnings = await _stamp_matched_show_warnings(db, user_id, all_warnings)
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.completed, stats=stats, warnings=all_warnings or None, updated_at=func.now()))
            await db.commit()
            from core.pull_propagation import propagate_media_server_pull
            await propagate_media_server_pull(
                db, conn=conn, watched_ids=accepted_watched, ratings=accepted_ratings,
            )
            asyncio.create_task(pre_cache_all_collected_bg())
        except SyncCancelled:
            print(f"Plex sync job {job_id} cancelled")
            await db.rollback()
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.cancelled, stats=stats, updated_at=func.now()))
            await db.commit()
        except Exception as e:
            print(f"Plex sync job {job_id} failed: {e}")
            import traceback
            traceback.print_exc()
            await db.rollback()
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed, error_message=str(e)[:900]))
            await db.commit()


def _parse_nuvio_tmdb_id(content_id: object) -> int | None:
    value = str(content_id or "")
    if not value.startswith("tmdb:"):
        return None
    try:
        return int(value[5:])
    except ValueError:
        return None


async def _resolve_nuvio_tmdb_ids(
    records: list[dict],
    db: AsyncSession,
    user_id: int,
    api_key: str,
    source: CollectionSource = CollectionSource.nuvio,
) -> dict[str, int]:
    content_types: dict[str, str] = {}
    resolved: dict[str, int] = {}
    for record in records:
        content_id = str(record.get("content_id") or "").strip()
        if not content_id:
            continue
        if tmdb_id := _parse_nuvio_tmdb_id(content_id):
            resolved[content_id] = tmdb_id
        elif re.fullmatch(r"tt\d+", content_id, flags=re.IGNORECASE):
            content_types.setdefault(content_id, str(record.get("content_type") or "").lower())

    unresolved = set(content_types) - set(resolved)
    if unresolved:
        existing_result = await db.execute(
            select(CollectionFile.source_id, Media.tmdb_id)
            .join(Collection, Collection.id == CollectionFile.collection_id)
            .join(Media, Media.id == Collection.media_id)
            .where(
                Collection.user_id == user_id,
                CollectionFile.source == source,
                Media.tmdb_id.isnot(None),
            )
        )
        for source_id, tmdb_id in existing_result.all():
            parts = str(source_id).split(":")
            if len(parts) >= 2 and parts[1] in unresolved:
                resolved[parts[1]] = int(tmdb_id)
        unresolved -= set(resolved)

    semaphore = asyncio.Semaphore(TMDB_CONCURRENCY)

    async def resolve_imdb_id(content_id: str) -> None:
        async with semaphore:
            try:
                result = await tmdb.find_by_external_id(content_id, "imdb_id", api_key=api_key)
                result_key = "movie_results" if content_types[content_id] == "movie" else "tv_results"
                matches = result.get(result_key) or []
                if matches and matches[0].get("id") is not None:
                    resolved[content_id] = int(matches[0]["id"])
            except Exception as exc:
                logger.warning(
                    "Failed to resolve Nuvio IMDb ID %s through TMDB: %s",
                    content_id,
                    exc,
                )

    if unresolved:
        await asyncio.gather(*(resolve_imdb_id(content_id) for content_id in sorted(unresolved)))
        logger.info(
            "Resolved %s/%s new Nuvio IMDb IDs through TMDB",
            len(unresolved & set(resolved)),
            len(unresolved),
        )
    return resolved


def _nuvio_datetime(epoch_ms: object) -> datetime | None:
    from core.watch_dates import normalize_watch_datetime
    return normalize_watch_datetime(epoch_ms)


def _normalize_nuvio_item(
    record: dict,
    profile_id: int,
    watched: bool = False,
    tmdb_id: int | None = None,
) -> tuple[MediaType, dict] | None:
    tmdb_id = tmdb_id or _parse_nuvio_tmdb_id(record.get("content_id"))
    if tmdb_id is None:
        return None

    content_type = str(record.get("content_type") or "").lower()
    if content_type == "tv":
        content_type = "series"
    season = record.get("season")
    episode = record.get("episode")
    is_episode = content_type == "series" and season is not None and episode is not None
    if content_type == "movie":
        media_type = MediaType.movie
    elif is_episode:
        media_type = MediaType.episode
    elif content_type == "series":
        media_type = MediaType.series
    else:
        return None

    content_id = str(record["content_id"])
    source_id = f"{profile_id}:{content_id}"
    if is_episode:
        source_id = f"{source_id}:s{season}e{episode}"
    last_played = _nuvio_datetime(record.get("watched_at") or record.get("last_watched"))
    title = record.get("title") or record.get("name") or content_id

    item = {
        "Id": source_id,
        "Name": title,
        "ProviderIds": {} if is_episode else {"Tmdb": str(tmdb_id)},
        "MediaStreams": [],
        "Path": None,
        "SeriesId": content_id if is_episode else None,
        "SeriesName": title if is_episode else None,
        "ParentIndexNumber": int(season) if season is not None else None,
        "IndexNumber": int(episode) if episode is not None else None,
        "UserData": {
            "Played": watched,
            "PlayCount": 1 if watched else 0,
            "LastPlayedDate": last_played.isoformat() if last_played else None,
            "SharedWatchDate": bool(record.get('date_shared')),
        },
    }
    return media_type, item


async def _apply_nuvio_watch_history(
    db: AsyncSession,
    user_id: int,
    rows: list[dict],
    show_map: dict[str, int],
    tmdb_ids: dict[str, int],
    *,
    include_unknown_dates: bool = False,
    dedupe_by_media_id_only: bool = False,
) -> set[int]:
    # Only movies are "standalone" watch targets. A show-level row (no
    # season/episode) is a rollup of its episodes and is skipped below, so it
    # never needs a Media lookup here. See #358.
    standalone_tmdb_ids = {
        tmdb_id
        for row in rows
        if str(row.get("content_type") or "").lower() == "movie"
        if (tmdb_id := tmdb_ids.get(str(row.get("content_id") or ""))) is not None
    }
    standalone_by_key: dict[tuple[MediaType, int], Media] = {}
    if standalone_tmdb_ids:
        rows_found = await _select_in_chunks(
            db,
            lambda chunk: select(Media).where(
                Media.media_type == MediaType.movie,
                Media.tmdb_id.in_(chunk),
            ),
            list(standalone_tmdb_ids),
        )
        standalone_by_key = {
            (media.media_type, media.tmdb_id): media
            for media in rows_found
            if media.tmdb_id is not None
        }

    show_ids = set(show_map.values())
    episodes_by_key: dict[tuple[int, int, int], Media] = {}
    if show_ids:
        rows_found = await _select_in_chunks(
            db,
            lambda chunk: select(Media).where(
                Media.media_type == MediaType.episode,
                Media.show_id.in_(chunk),
            ),
            list(show_ids),
        )
        episodes_by_key = {
            (media.show_id, media.season_number, media.episode_number): media
            for media in rows_found
            if media.show_id is not None
            and media.season_number is not None
            and media.episode_number is not None
        }

    candidates: list[tuple[Media, datetime | None, bool]] = []
    for row in rows:
        content_id = str(row.get("content_id") or "")
        tmdb_id = tmdb_ids.get(content_id)
        watched_at = _nuvio_datetime(row.get("watched_at"))
        if tmdb_id is None or (watched_at is None and not include_unknown_dates):
            continue
        content_type = str(row.get("content_type") or "").lower()
        season = row.get("season")
        episode = row.get("episode")
        if content_type == "movie":
            media = standalone_by_key.get((MediaType.movie, tmdb_id))
        elif season is None or episode is None:
            # A show-level "watched" row (no season/episode) is a rollup of its
            # episodes, not a watchable item - recording it would create a
            # bogus series-level watch event alongside the real episode ones.
            # See #358.
            continue
        else:
            show_id = show_map.get(content_id)
            media = (
                episodes_by_key.get((show_id, int(season), int(episode)))
                if show_id is not None
                else None
            )
        if media is not None:
            candidates.append((media, watched_at, bool(row.get('date_shared'))))

    if not candidates:
        return set()
    media_ids = {media.id for media, _, _ in candidates}
    from core.watch_dates import inferred_watch_datetime, reconcile_inferred_watch_date
    existing_by_media: dict[int, list[datetime | None]] = {}
    media_id_list = list(media_ids)
    for i in range(0, len(media_id_list), _MAX_IN_PARAMS):
        existing_result = await db.execute(
            select(WatchEvent.media_id, WatchEvent.watched_at).where(
                WatchEvent.user_id == user_id,
                WatchEvent.media_id.in_(media_id_list[i : i + _MAX_IN_PARAMS]),
                WatchEvent.completed.is_(True),
            )
        )
        for existing_media_id, existing_watched_at in existing_result.all():
            existing_by_media.setdefault(existing_media_id, []).append(existing_watched_at)

    window_minutes = await get_dedup_window_minutes(db, user_id)
    added_media_ids: set[int] = set()
    new_events: list[WatchEvent] = []
    for media, watched_at, shared_date in candidates:
        times = existing_by_media.get(media.id, [])
        if not shared_date and watched_at is not None and await reconcile_inferred_watch_date(
            db, user_id, media.id, watched_at, authoritative=True,
        ):
            existing_by_media.setdefault(media.id, []).append(watched_at)
            continue
        if dedupe_by_media_id_only:
            if times:
                continue
        elif watched_at is None:
            # An undated watched flag does not prove a new rewatch when any
            # completed play already exists for this title.
            if times:
                continue
        elif is_duplicate_watch_time({media.id: [t for t in times if t is not None]}, media.id, watched_at, window_minutes):
            continue
        event = WatchEvent(
            user_id=user_id,
            media_id=media.id,
            watched_at=watched_at or inferred_watch_datetime(),
            date_inferred=watched_at is None or shared_date,
            date_shared=shared_date,
            completed=True,
            play_count=1,
            progress_percent=1.0,
        )
        db.add(event)
        new_events.append(event)
        # Keep in sync - a duplicate row later in the same batch must still be
        # caught, or it'd create a second WatchEvent for it in one sync run.
        existing_by_media.setdefault(media.id, []).append(event.watched_at)
        added_media_ids.add(media.id)
    await db.commit()
    for event in new_events:
        await record_rewatch_progress(db, user_id, event.media_id, event.id)
    if new_events:
        await db.commit()
    return added_media_ids


async def _apply_nuvio_progress(
    db: AsyncSession,
    user_id: int,
    rows: list[dict],
    show_map: dict[str, int],
    tmdb_ids: dict[str, int],
    conn: MediaServerConnection | None = None,
    *,
    fresh_import: bool = False,
) -> None:
    from models.tracking import StreamBaseline
    baseline = await db.get(StreamBaseline, conn.id) if conn else None
    previous_progress = {
        (str(row.get('content_id')), row.get('season'), row.get('episode')): row
        for row in (baseline.snapshot or {}).get('records', {}).get('progress', [])
    } if baseline else {}
    movie_tmdb_ids = {
        tmdb_id
        for row in rows
        if str(row.get("content_type") or "").lower() == "movie"
        if (tmdb_id := tmdb_ids.get(str(row.get("content_id") or ""))) is not None
    }
    movies_by_tmdb: dict[int, Media] = {}
    if movie_tmdb_ids:
        rows_found = await _select_in_chunks(
            db,
            lambda chunk: select(Media).where(Media.media_type == MediaType.movie, Media.tmdb_id.in_(chunk)),
            list(movie_tmdb_ids),
        )
        movies_by_tmdb = {media.tmdb_id: media for media in rows_found if media.tmdb_id is not None}

    show_ids = set(show_map.values())
    episodes_by_key: dict[tuple[int, int, int], Media] = {}
    if show_ids:
        rows_found = await _select_in_chunks(
            db,
            lambda chunk: select(Media).where(Media.media_type == MediaType.episode, Media.show_id.in_(chunk)),
            list(show_ids),
        )
        episodes_by_key = {
            (media.show_id, media.season_number, media.episode_number): media
            for media in rows_found
            if media.show_id is not None and media.season_number is not None and media.episode_number is not None
        }

    media_rows: list[tuple[dict, Media]] = []
    for row in rows:
        content_id = str(row.get("content_id") or "")
        tmdb_id = tmdb_ids.get(content_id)
        if tmdb_id is None:
            continue
        if str(row.get("content_type") or "").lower() == "movie":
            media = movies_by_tmdb.get(tmdb_id)
        else:
            season = row.get("season")
            episode = row.get("episode")
            show_id = show_map.get(content_id)
            media = (
                episodes_by_key.get((show_id, int(season), int(episode)))
                if show_id is not None and season is not None and episode is not None
                else None
            )
        if media is not None:
            media_rows.append((row, media))

    if not media_rows:
        return

    media_ids = {media.id for _, media in media_rows}
    existing_rows = await _select_in_chunks(
        db,
        lambda chunk: select(PlaybackProgress).where(
            PlaybackProgress.user_id == user_id,
            PlaybackProgress.media_id.in_(chunk),
        ),
        list(media_ids),
    )
    existing = {progress.media_id: progress for progress in existing_rows}

    for row, media in media_rows:
        if baseline:
            key = (str(row.get('content_id')), row.get('season'), row.get('episode'))
            prior = previous_progress.get(key)
            if prior and all(prior.get(field) == row.get(field)
                             for field in ('position', 'duration', 'last_watched')):
                continue
        try:
            position_ms = max(0, int(row.get("position") or 0))
            duration_ms = max(0, int(row.get("duration") or 0))
        except (TypeError, ValueError):
            continue
        if duration_ms <= 0:
            continue
        progress_percent = min(1.0, position_ms / duration_ms)
        progress = existing.get(media.id)
        provider_at = _nuvio_datetime(row.get('last_watched'))
        if progress:
            if baseline is None or fresh_import:
                # A new connection can fill gaps, but cannot replace local
                # Continue Watching data during its first or full re-import.
                continue
            if conn and not conn.push_playback and progress_percent <= progress.progress_percent:
                continue
            if provider_at is None and progress_percent <= progress.progress_percent:
                continue
            if provider_at and progress.updated_at and provider_at <= progress.updated_at:
                continue
        if 0.05 <= progress_percent < 0.90:
            updated_at = provider_at or datetime.utcnow()
            if progress:
                progress.progress_percent = progress_percent
                progress.progress_seconds = position_ms // 1000
                progress.updated_at = updated_at
            else:
                progress = PlaybackProgress(
                    user_id=user_id,
                    media_id=media.id,
                    progress_percent=progress_percent,
                    progress_seconds=position_ms // 1000,
                    updated_at=updated_at,
                )
                db.add(progress)
                existing[media.id] = progress
        elif progress and conn and conn.push_playback and provider_at:
            await db.delete(progress)
            existing.pop(media.id, None)
    await db.commit()


async def run_nuvio_sync(
    user_id: int,
    job_id: int,
    movie_limit: int,
    show_limit: int,
    connection_id: int | None = None,
    full_resync: bool = False,
):
    async with _sync_semaphore:
        await _run_nuvio_sync(user_id, job_id, movie_limit, show_limit, connection_id, full_resync)


async def _run_nuvio_sync(
    user_id: int,
    job_id: int,
    movie_limit: int,
    show_limit: int,
    connection_id: int | None = None,
    full_resync: bool = False,
):
    logger.info("Starting Nuvio sync for user %s, job %s", user_id, job_id)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        try:
            if not await mark_job_running_unless_cancelled(
                db, job_id, processed_items=0, total_items=0, current_step="Pulling from Nuvio",
            ):
                logger.info("Nuvio sync job %s was cancelled before it started - skipping", job_id)
                return

            settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
            settings = settings_result.scalar_one_or_none()
            tmdb_api_key = await settings_store.get_effective_tmdb_key(db, settings)

            conn_query = select(MediaServerConnection).where(
                MediaServerConnection.user_id == user_id,
                MediaServerConnection.type == "nuvio",
            )
            if connection_id:
                conn_query = conn_query.where(MediaServerConnection.id == connection_id)
            else:
                conn_query = conn_query.order_by(MediaServerConnection.id.asc()).limit(1)
            conn_result = await db.execute(conn_query)
            conn = conn_result.scalar_one_or_none()
            if not conn or not tmdb_api_key:
                raise RuntimeError("Missing Nuvio connection or TMDB API key")

            if full_resync and (movie_limit or show_limit):
                raise RuntimeError("A full Nuvio resync requires an unbounded complete provider snapshot")

            try:
                profile_id = int(conn.server_user_id or "")
            except ValueError:
                raise RuntimeError("Invalid Nuvio profile index")
            if profile_id < 1 or profile_id > 6:
                raise RuntimeError("Invalid Nuvio profile index")

            from core.streaming_clear import pull_blocked_by_clear
            if not full_resync and await pull_blocked_by_clear(db, conn):
                raise RuntimeError("A provider clear is incomplete; retry Clear data or run a full resync before syncing")

            # Supabase refresh tokens rotate; on_refresh persists the replacement
            # the moment it's issued, inside pull_sync_data, before the pull RPCs
            # run — a failed pull can no longer strand the connection on a
            # refresh token that's already been redeemed and rejected.
            async def _persist_refresh(refreshed: nuvio.NuvioSession) -> None:
                from sqlalchemy import update
                from sqlalchemy.orm.attributes import set_committed_value

                from db import AsyncSessionLocal
                async with AsyncSessionLocal() as token_db:
                    result = await token_db.execute(update(MediaServerConnection).where(
                        MediaServerConnection.id == conn.id,
                        MediaServerConnection.user_id == user_id,
                        MediaServerConnection.url == conn.url,
                        MediaServerConnection.server_user_id == conn.server_user_id,
                        MediaServerConnection.token == conn.token,
                    ).values(token=refreshed.refresh_token))
                    if result.rowcount != 1:
                        raise RuntimeError("Nuvio connection profile changed during sync")
                    await token_db.commit()
                set_committed_value(conn, "token", refreshed.refresh_token)

            pull_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
            async with nuvio.connection_lock(conn.id):
                # See core/nuvio.py's connection_lock docstring - conn may
                # have been loaded before another request already rotated
                # this single-use refresh token while this one waited.
                await refresh_stream_connection(db, conn)
                _, data = await nuvio.pull_sync_data(
                    conn.url, conn.token, profile_id, on_refresh=_persist_refresh
                )

            library_records = data["library"] if conn.sync_collection else []
            watched_records = data["watched"] if conn.sync_watched else []
            progress_records = data["progress"] if conn.sync_playback else []
            complete_snapshot = len(data["progress"]) < 200
            if full_resync and not complete_snapshot:
                raise RuntimeError("Nuvio returned the maximum watch-progress page; refusing an incomplete full resync")
            logger.info(
                "Nuvio profile %s (index #%s): pulled %s library, %s watched, "
                "and %s progress records; enabled for this sync: %s library, "
                "%s watched, %s progress",
                conn.server_username or f"#{profile_id}",
                profile_id,
                len(data["library"]),
                len(data["watched"]),
                len(data["progress"]),
                len(library_records),
                len(watched_records),
                len(progress_records),
            )

            all_nuvio_records = [*library_records, *watched_records, *progress_records]
            tmdb_ids = await _resolve_nuvio_tmdb_ids(
                all_nuvio_records,
                db,
                user_id,
                tmdb_api_key,
            )
            normalized_library = [
                normalized
                for record in library_records
                if (
                    normalized := _normalize_nuvio_item(
                        record,
                        profile_id,
                        tmdb_id=tmdb_ids.get(str(record.get("content_id") or "")),
                    )
                )
                is not None
            ]
            unresolved_library_records = len(library_records) - len(normalized_library)
            normalized_watched = [
                normalized
                for record in watched_records
                if (
                    normalized := _normalize_nuvio_item(
                        record,
                        profile_id,
                        watched=True,
                        tmdb_id=tmdb_ids.get(str(record.get("content_id") or "")),
                    )
                )
                is not None
            ]
            normalized_progress = [
                normalized
                for record in progress_records
                if (
                    normalized := _normalize_nuvio_item(
                        record,
                        profile_id,
                        tmdb_id=tmdb_ids.get(str(record.get("content_id") or "")),
                    )
                )
                is not None
            ]
            skipped_nuvio_records = len(all_nuvio_records) - (
                len(normalized_library) + len(normalized_watched) + len(normalized_progress)
            )

            if movie_limit:
                normalized_library = [
                    *[entry for entry in normalized_library if entry[0] == MediaType.movie][:movie_limit],
                    *[entry for entry in normalized_library if entry[0] != MediaType.movie],
                ]
            if show_limit:
                normalized_library = [
                    *[entry for entry in normalized_library if entry[0] != MediaType.series],
                    *[entry for entry in normalized_library if entry[0] == MediaType.series][:show_limit],
                ]

            series_tmdb_map = {
                str(record.get("content_id")): tmdb_id
                for record in [*library_records, *watched_records, *progress_records]
                if str(record.get("content_type") or "").lower() == "series"
                if (tmdb_id := tmdb_ids.get(str(record.get("content_id") or ""))) is not None
            }
            if series_tmdb_map:
                show_map, show_id_to_tmdb = await sync_shows_batch(
                    series_tmdb_map,
                    db,
                    api_key=tmdb_api_key,
                )
            else:
                show_map, show_id_to_tmdb = {}, {}

            all_entries = [*normalized_library, *normalized_watched, *normalized_progress]
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(total_items=len(all_entries))
            )
            await db.commit()

            stats = {"movies": 0, "series": 0, "episodes": 0, "skipped": skipped_nuvio_records, "errors": 0}
            warnings: list[dict] = []
            new_watched_ids: set[int] = set()
            new_collected_ids: set[int] = set()

            async def sync_group(
                entries: list[tuple[MediaType, dict]],
                media_type: MediaType,
                *,
                sync_collection: bool,
                sync_watched: bool,
            ) -> None:
                items = [item for item_type, item in entries if item_type == media_type]
                if not items:
                    return
                group_warnings = await sync_items(
                    items,
                    media_type,
                    CollectionSource.nuvio,
                    db,
                    stats,
                    user_id,
                    job_id,
                    show_map if media_type == MediaType.episode else {},
                    api_key=tmdb_api_key,
                    show_id_to_tmdb=show_id_to_tmdb if media_type == MediaType.episode else {},
                    sync_collection=sync_collection,
                    sync_watched=sync_watched,
                    sync_ratings=False,
                    new_watched_ids=new_watched_ids,
                    new_collected_ids=new_collected_ids,
                    connection_id=conn.id,
                    snapshot_started_at=pull_started_at,
                    full_resync=full_resync,
                )
                warnings.extend(group_warnings)

            for media_type in (MediaType.movie, MediaType.series, MediaType.episode):
                await sync_group(
                    normalized_library,
                    media_type,
                    sync_collection=True,
                    sync_watched=False,
                )
            for media_type in (MediaType.movie, MediaType.series, MediaType.episode):
                await sync_group(
                    normalized_watched,
                    media_type,
                    sync_collection=False,
                    sync_watched=False,
                )
            for media_type in (MediaType.movie, MediaType.series, MediaType.episode):
                await sync_group(
                    normalized_progress,
                    media_type,
                    sync_collection=False,
                    sync_watched=False,
                )

            if conn.sync_watched and watched_records:
                from core.streaming_clear import assert_pull_snapshot_current
                await assert_pull_snapshot_current(
                    db, user_id, conn, pull_started_at, full_resync=full_resync,
                )
                from core.tracking_snapshot import changed_watch_rows_from_source
                history_rows = watched_records if full_resync else await changed_watch_rows_from_source(
                    db, conn, watched_records,
                )
                new_watched_ids.update(
                    await _apply_nuvio_watch_history(
                        db,
                        user_id,
                        history_rows,
                        show_map,
                        tmdb_ids,
                        include_unknown_dates=True,
                    )
                )

            if progress_records:
                from core.streaming_clear import assert_pull_snapshot_current
                await assert_pull_snapshot_current(
                    db, user_id, conn, pull_started_at, full_resync=full_resync,
                )
                await _apply_nuvio_progress(
                    db, user_id, progress_records, show_map, tmdb_ids, conn,
                    fresh_import=full_resync,
                )

            complete_library_source_ids = (
                {str(item["Id"]) for _, item in normalized_library}
                if conn.sync_collection
                and not unresolved_library_records
                and not movie_limit
                and not show_limit
                and not full_resync
                else None
            )
            from core.streaming_clear import assert_pull_snapshot_current
            await assert_pull_snapshot_current(
                db, user_id, conn, pull_started_at, full_resync=full_resync,
            )
            removed_collected_ids = await _remove_stream_collection_sources(
                db,
                user_id,
                conn.id,
                source=CollectionSource.nuvio,
                removed_ids=set(),
                complete_snapshot_source_ids=complete_library_source_ids,
            )
            # Release the user-row gate only after the source-specific removals
            # commit. Clear jobs acquire this lock before taking a provider lock.
            await db.commit()

            if (new_collected_ids or removed_collected_ids) and not full_resync:
                # Commit local truth before attempting any external write.
                await db.commit()
                await outbound_sync.fan_out_streaming_library(
                    db,
                    user_id,
                    conn.id,
                    new_collected_ids=new_collected_ids,
                    removed_collected_ids=removed_collected_ids,
                    api_key=tmdb_api_key,
                )

            changed_media_ids = set(new_collected_ids) | set(removed_collected_ids)
            if (conn.sync_playback or conn.sync_watched or full_resync) and not stats['errors']:
                from core.tracking_snapshot import observe_stream_snapshot
                removed_watch_ids = set()
                propagated_watch_ids = await observe_stream_snapshot(
                    db, conn, library_records, watched_records, progress_records, tmdb_ids,
                    complete=complete_snapshot,
                    sync_playback=conn.sync_playback,
                    sync_watched=conn.sync_watched,
                    removed_watched_ids=removed_watch_ids,
                    fresh_import=full_resync,
                    source_started_at=pull_started_at,
                    changed_media_ids=changed_media_ids,
                    cw_visibility=data.get("cw_visibility"),
                )
                if (propagated_watch_ids or removed_watch_ids) and not full_resync:
                    from core.pull_propagation import propagate_media_server_pull
                    await propagate_media_server_pull(
                        db, conn=conn, watched_ids=propagated_watch_ids, ratings={},
                        removed_watched_ids=removed_watch_ids,
                    )
                if not full_resync:
                    from core.stream_actions import dispatch_stream_actions
                    await dispatch_stream_actions(db, user_id)
            if not full_resync:
                from core.streaming_library import retry_pending_library_deliveries
                await retry_pending_library_deliveries(db, user_id, conn.id)

            # Tracking/watch changes remain local until their own confirmation rules
            # allow export. Verified streaming-library deltas are mirrored separately.
            stats["succeeded"] = len(changed_media_ids)
            stats["failed"] = stats["errors"]
            warnings = await _stamp_matched_show_warnings(db, user_id, warnings)
            await db.execute(
                update(SyncJob)
                .where(SyncJob.id == job_id)
                .values(
                    status=SyncStatus.completed,
                    stats=stats,
                    warnings=warnings or None,
                    updated_at=func.now(),
                )
            )
            await db.commit()
            asyncio.create_task(pre_cache_all_collected_bg())
            logger.info("Nuvio sync job %s completed. Stats: %s", job_id, stats)
        except SyncCancelled:
            logger.info("Nuvio sync job %s cancelled", job_id)
            await db.rollback()
            await db.execute(
                update(SyncJob)
                .where(SyncJob.id == job_id)
                .values(status=SyncStatus.cancelled, stats=stats, updated_at=func.now())
            )
            await db.commit()
        except Exception as exc:
            logger.exception("Nuvio sync job %s failed", job_id)
            await db.rollback()
            await db.execute(
                update(SyncJob)
                .where(SyncJob.id == job_id)
                .values(status=SyncStatus.failed, error_message=str(exc)[:900])
            )
            await db.commit()


def _stremio_valid_content_id(content_id: object) -> bool:
    value = str(content_id or "")
    return bool(re.fullmatch(r"tt\d+", value, flags=re.IGNORECASE)) or _parse_nuvio_tmdb_id(value) is not None


def _stremio_series_imdb_id(item: dict) -> str | None:
    """Cinemeta only understands IMDb ids, but a series' own `_id` may be a
    tmdb:<id> catalog id (e.g. items added via a TMDB-based addon). Episode
    identifiers embedded in state always carry the IMDb-prefixed episode id
    regardless of the catalog the series was added from, so fall back to
    those to find an IMDb id to key the Cinemeta lookup by."""
    content_id = str(item.get("_id") or "")
    if re.fullmatch(r"tt\d+", content_id, flags=re.IGNORECASE):
        return content_id
    state = item.get("state") if isinstance(item.get("state"), dict) else {}
    candidates = [str(state.get("video_id") or "")]
    watched = str(state.get("watched") or "")
    if watched:
        candidates.append(watched.rsplit(":", 2)[0])
    for candidate in candidates:
        imdb_id = candidate.split(":", 1)[0]
        if re.fullmatch(r"tt\d+", imdb_id, flags=re.IGNORECASE):
            return imdb_id
    return None


async def _stremio_records(
    items: list[dict],
) -> tuple[list[dict], list[dict], list[dict], set[str]]:
    records = [
        item
        for item in items
        if str(item.get("type") or "") in ("movie", "series")
        and _stremio_valid_content_id(item.get("_id"))
    ]
    removed_ids = {
        str(item.get("_id"))
        for item in items
        if item.get("removed") and _stremio_valid_content_id(item.get("_id"))
    }
    series_needing_meta = [
        item
        for item in records
        if item.get("type") == "series"
        and (
            (item.get("state") or {}).get("watched")
            or (item.get("state") or {}).get("video_id")
        )
    ]
    series_imdb_ids = {
        str(item["_id"]): imdb_id
        for item in series_needing_meta
        if (imdb_id := _stremio_series_imdb_id(item)) is not None
    }
    metas = await stremio.get_series_metadata(set(series_imdb_ids.values()))

    library_records: list[dict] = []
    watched_records: list[dict] = []
    progress_records: list[dict] = []
    for item in records:
        content_id = str(item["_id"])
        content_type = str(item["type"])
        title = str(item.get("name") or content_id)
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        base = {
            "content_id": content_id,
            "content_type": content_type,
            "title": title,
            "modified_at": item.get("_mtime"),
        }
        if not item.get("removed") and not item.get("temp"):
            library_records.append(base)
        last_watched = stremio_payloads.epoch_ms(state.get("lastWatched"))

        if content_type == "movie":
            try:
                times_watched = int(state.get("timesWatched") or 0)
            except (TypeError, ValueError):
                times_watched = 0
            if times_watched > 0:
                watched_records.append({**base, "watched_at": last_watched})
            try:
                position = int(state.get("timeOffset") or 0)
                duration = int(state.get("duration") or 0)
            except (TypeError, ValueError):
                position = duration = 0
            if position > 0 and duration > 0:
                progress_records.append(
                    {
                        **base,
                        "position": position,
                        "duration": duration,
                        "last_watched": last_watched,
                    }
                )
            continue

        videos = stremio_payloads.sorted_videos(metas.get(series_imdb_ids.get(content_id, content_id), {}))
        video_ids = [str(video["id"]) for video in videos]
        watched_ids = stremio.decode_watched_bitfield(state.get("watched"), video_ids)
        current_video_id = str(state.get("video_id") or "")
        for video in videos:
            video_id = str(video["id"])
            if video_id not in watched_ids:
                continue
            parts = stremio_payloads.video_parts(video)
            if parts is None:
                continue
            season, episode = parts
            watched_records.append(
                {
                    **base,
                    "title": str(video.get("name") or title),
                    "season": season,
                    "episode": episode,
                    "watched_at": last_watched,
                    "date_shared": video_id != current_video_id,
                }
            )

        current_video = next(
            (video for video in videos if str(video.get("id")) == current_video_id),
            {"id": current_video_id},
        )
        current_parts = stremio_payloads.video_parts(current_video)
        try:
            position = int(state.get("timeOffset") or 0)
            duration = int(state.get("duration") or 0)
        except (TypeError, ValueError):
            position = duration = 0
        # Stremio can retain a stale timeOffset after the same episode has
        # already been marked watched. That offset is historical residue, not
        # active Continue Watching progress, so it must not recreate Watching.
        if current_parts and current_video_id not in watched_ids and position > 0 and duration > 0:
            season, episode = current_parts
            progress_records.append(
                {
                    **base,
                    "title": str(current_video.get("name") or title),
                    "season": season,
                    "episode": episode,
                    "position": position,
                    "duration": duration,
                    "last_watched": last_watched,
                }
            )
    return library_records, watched_records, progress_records, removed_ids


async def _remove_stream_collection_sources(
    db: AsyncSession,
    user_id: int,
    connection_id: int,
    *,
    source: CollectionSource,
    removed_ids: set[str],
    complete_snapshot_source_ids: set[str] | None,
) -> set[int]:
    """Remove membership proven absent for this exact source connection."""
    result = await db.execute(
        select(CollectionFile, Collection.media_id)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .where(
            Collection.user_id == user_id,
            CollectionFile.source == source,
            CollectionFile.connection_id == connection_id,
        )
    )
    removed_media_ids: set[int] = set()
    expected = complete_snapshot_source_ids
    explicit = {f"{connection_id}:{content_id}" for content_id in removed_ids}
    for collection_file, media_id in result.all():
        should_remove = collection_file.source_id in explicit
        if expected is not None and collection_file.source_id not in expected:
            should_remove = True
        if not should_remove:
            continue
        collection_id = collection_file.collection_id
        await db.delete(collection_file)
        await db.flush()
        remaining = await db.execute(
            select(func.count(CollectionFile.id)).where(
                CollectionFile.collection_id == collection_id
            )
        )
        if remaining.scalar_one() == 0:
            collection = await db.get(Collection, collection_id)
            if collection:
                await db.delete(collection)
                removed_media_ids.add(media_id)
    return removed_media_ids


async def _remove_stremio_collection_sources(
    db: AsyncSession,
    user_id: int,
    connection_id: int,
    *,
    removed_ids: set[str],
    complete_snapshot_ids: set[str] | None,
) -> set[int]:
    """Compatibility wrapper for Stremio's connection-prefixed source IDs."""
    expected = (
        {f"{connection_id}:{content_id}" for content_id in complete_snapshot_ids}
        if complete_snapshot_ids is not None
        else None
    )
    return await _remove_stream_collection_sources(
        db,
        user_id,
        connection_id,
        source=CollectionSource.stremio,
        removed_ids=removed_ids,
        complete_snapshot_source_ids=expected,
    )


async def _pull_stremio_items(
    conn: MediaServerConnection,
    *,
    full_resync: bool,
) -> tuple[list[dict], bool, datetime]:
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    complete_snapshot = full_resync or not conn.stremio_full_sync_done or conn.stremio_pull_cursor_at is None
    if complete_snapshot:
        return await stremio.datastore_get(conn.token, all_items=True), True, started_at

    cutoff = conn.stremio_pull_cursor_at - timedelta(minutes=5)
    meta = await stremio.datastore_meta(conn.token)
    changed_ids = []
    for row in meta:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        item_id = str(row[0] or "")
        modified_at = _nuvio_datetime(stremio_payloads.epoch_ms(row[1]))
        if item_id and modified_at is not None and modified_at >= cutoff:
            changed_ids.append(item_id)
    return await stremio.datastore_get(conn.token, ids=changed_ids), False, started_at


async def run_stremio_sync(
    user_id: int,
    job_id: int,
    movie_limit: int,
    show_limit: int,
    connection_id: int | None = None,
    full_resync: bool = False,
) -> None:
    async with _sync_semaphore:
        await _run_stremio_sync(
            user_id,
            job_id,
            movie_limit,
            show_limit,
            connection_id,
            full_resync,
        )


async def _run_stremio_sync(
    user_id: int,
    job_id: int,
    movie_limit: int,
    show_limit: int,
    connection_id: int | None = None,
    full_resync: bool = False,
) -> None:
    logger.info("Starting Stremio sync for user %s, job %s", user_id, job_id)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        try:
            if not await mark_job_running_unless_cancelled(
                db, job_id, processed_items=0, total_items=0, current_step="Pulling from Stremio",
            ):
                logger.info("Stremio sync job %s was cancelled before it started - skipping", job_id)
                return
            settings_result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == user_id)
            )
            settings = settings_result.scalar_one_or_none()
            tmdb_api_key = await settings_store.get_effective_tmdb_key(db, settings)
            conn_query = select(MediaServerConnection).where(
                MediaServerConnection.user_id == user_id,
                MediaServerConnection.type == "stremio",
            )
            if connection_id:
                conn_query = conn_query.where(MediaServerConnection.id == connection_id)
            conn = (await db.execute(conn_query.limit(1))).scalar_one_or_none()
            if not conn or not tmdb_api_key:
                raise RuntimeError("Missing Stremio connection or TMDB API key")

            if full_resync and (movie_limit or show_limit):
                raise RuntimeError("A full Stremio resync requires an unbounded complete provider snapshot")

            from core.streaming_clear import pull_blocked_by_clear
            if not full_resync and await pull_blocked_by_clear(db, conn):
                raise RuntimeError("A provider clear is incomplete; retry Clear data or run a full resync before syncing")

            async with stremio.connection_lock(conn.id):
                await refresh_stream_connection(db, conn)
                items, complete_snapshot, pull_started_at = await _pull_stremio_items(
                    conn,
                    full_resync=full_resync,
                )
            library_records, watched_records, progress_records, removed_ids = await _stremio_records(items)
            if full_resync:
                removed_ids = set()
            if not conn.sync_collection:
                library_records = []
                removed_ids = set()
            if not conn.sync_watched:
                watched_records = []
            if not conn.sync_playback:
                progress_records = []

            all_records = [*library_records, *watched_records, *progress_records]
            tmdb_ids = await _resolve_nuvio_tmdb_ids(
                all_records,
                db,
                user_id,
                tmdb_api_key,
                source=CollectionSource.stremio,
            )
            normalized_library = [
                normalized
                for record in library_records
                if (
                    normalized := _normalize_nuvio_item(
                        record,
                        conn.id,
                        tmdb_id=tmdb_ids.get(str(record.get("content_id") or "")),
                    )
                )
            ]
            normalized_watched = [
                normalized
                for record in watched_records
                if (
                    normalized := _normalize_nuvio_item(
                        record,
                        conn.id,
                        watched=True,
                        tmdb_id=tmdb_ids.get(str(record.get("content_id") or "")),
                    )
                )
            ]
            normalized_progress = [
                normalized
                for record in progress_records
                if (
                    normalized := _normalize_nuvio_item(
                        record,
                        conn.id,
                        tmdb_id=tmdb_ids.get(str(record.get("content_id") or "")),
                    )
                )
            ]
            if movie_limit:
                normalized_library = [
                    *[entry for entry in normalized_library if entry[0] == MediaType.movie][:movie_limit],
                    *[entry for entry in normalized_library if entry[0] != MediaType.movie],
                ]
            if show_limit:
                normalized_library = [
                    *[entry for entry in normalized_library if entry[0] != MediaType.series],
                    *[entry for entry in normalized_library if entry[0] == MediaType.series][:show_limit],
                ]

            series_tmdb_map = {
                str(record["content_id"]): tmdb_ids[str(record["content_id"])]
                for record in all_records
                if record.get("content_type") == "series"
                and str(record.get("content_id")) in tmdb_ids
            }
            show_map, show_id_to_tmdb = (
                await sync_shows_batch(series_tmdb_map, db, api_key=tmdb_api_key)
                if series_tmdb_map
                else ({}, {})
            )
            all_entries = [*normalized_library, *normalized_watched, *normalized_progress]
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(total_items=len(all_entries))
            )
            await db.commit()

            stats = {"movies": 0, "series": 0, "episodes": 0, "skipped": 0, "errors": 0}
            warnings: list[dict] = []
            new_watched_ids: set[int] = set()
            new_collected_ids: set[int] = set()

            async def sync_group(
                entries: list[tuple[MediaType, dict]],
                media_type: MediaType,
                *,
                sync_collection: bool,
            ) -> None:
                group = [item for item_type, item in entries if item_type == media_type]
                if not group:
                    return
                warnings.extend(
                    await sync_items(
                        group,
                        media_type,
                        CollectionSource.stremio,
                        db,
                        stats,
                        user_id,
                        job_id,
                        show_map if media_type == MediaType.episode else {},
                        api_key=tmdb_api_key,
                        show_id_to_tmdb=show_id_to_tmdb if media_type == MediaType.episode else {},
                        sync_collection=sync_collection,
                        sync_watched=False,
                        sync_ratings=False,
                        new_watched_ids=new_watched_ids,
                        new_collected_ids=new_collected_ids,
                        connection_id=conn.id,
                        snapshot_started_at=pull_started_at,
                        full_resync=full_resync,
                    )
                )

            for media_type in (MediaType.movie, MediaType.series):
                await sync_group(normalized_library, media_type, sync_collection=True)
            for media_type in (MediaType.movie, MediaType.episode):
                await sync_group(normalized_watched, media_type, sync_collection=False)
                await sync_group(normalized_progress, media_type, sync_collection=False)

            if conn.sync_watched and watched_records:
                from core.streaming_clear import assert_pull_snapshot_current
                await assert_pull_snapshot_current(
                    db, user_id, conn, pull_started_at, full_resync=full_resync,
                )
                from core.tracking_snapshot import changed_watch_rows_from_source
                history_rows = watched_records if full_resync else await changed_watch_rows_from_source(
                    db, conn, watched_records,
                )
                new_watched_ids.update(
                    await _apply_nuvio_watch_history(
                        db,
                        user_id,
                        history_rows,
                        show_map,
                        tmdb_ids,
                        include_unknown_dates=True,
                        dedupe_by_media_id_only=True,
                    )
                )
            if progress_records:
                from core.streaming_clear import assert_pull_snapshot_current
                await assert_pull_snapshot_current(
                    db, user_id, conn, pull_started_at, full_resync=full_resync,
                )
                await _apply_nuvio_progress(
                    db, user_id, progress_records, show_map, tmdb_ids, conn,
                    fresh_import=full_resync,
                )

            complete_snapshot_ids = (
                {str(record["content_id"]) for record in library_records}
                if complete_snapshot and conn.sync_collection and not full_resync
                else None
            )
            from core.streaming_clear import assert_pull_snapshot_current
            await assert_pull_snapshot_current(
                db, user_id, conn, pull_started_at, full_resync=full_resync,
            )
            removed_collected_ids = await _remove_stremio_collection_sources(
                db,
                user_id,
                conn.id,
                removed_ids=removed_ids,
                complete_snapshot_ids=complete_snapshot_ids,
            )
            await db.commit()
            if (new_collected_ids or removed_collected_ids) and not full_resync:
                # Commit local truth before attempting any external write.
                await db.commit()
                await outbound_sync.fan_out_streaming_library(
                    db,
                    user_id,
                    conn.id,
                    new_collected_ids=new_collected_ids,
                    removed_collected_ids=removed_collected_ids,
                    api_key=tmdb_api_key,
                )
            changed_media_ids = set(new_collected_ids) | set(removed_collected_ids)
            if (conn.sync_playback or conn.sync_watched or full_resync) and not stats['errors']:
                from core.tracking_snapshot import observe_stream_snapshot
                removed_watch_ids = set()
                propagated_watch_ids = await observe_stream_snapshot(
                    db, conn, library_records, watched_records, progress_records, tmdb_ids,
                    complete=complete_snapshot, touched={str(item['_id']) for item in items},
                    sync_playback=conn.sync_playback,
                    sync_watched=conn.sync_watched,
                    removed_watched_ids=removed_watch_ids,
                    fresh_import=full_resync,
                    source_started_at=pull_started_at,
                    changed_media_ids=changed_media_ids,
                )
                if (propagated_watch_ids or removed_watch_ids) and not full_resync:
                    from core.pull_propagation import propagate_media_server_pull
                    await propagate_media_server_pull(
                        db, conn=conn, watched_ids=propagated_watch_ids, ratings={},
                        removed_watched_ids=removed_watch_ids,
                    )
                if not full_resync:
                    from core.stream_actions import dispatch_stream_actions
                    await dispatch_stream_actions(db, user_id)
            if not full_resync:
                from core.streaming_library import retry_pending_library_deliveries
                await retry_pending_library_deliveries(db, user_id, conn.id)
            # Tracking/watch changes remain local until their own confirmation rules
            # allow export. Verified streaming-library deltas are mirrored separately.
            conn.stremio_pull_cursor_at = pull_started_at
            conn.stremio_full_sync_done = True
            stats["succeeded"] = len(changed_media_ids)
            stats["failed"] = stats["errors"]
            warnings = await _stamp_matched_show_warnings(db, user_id, warnings)
            await db.execute(
                update(SyncJob)
                .where(SyncJob.id == job_id)
                .values(
                    status=SyncStatus.completed,
                    stats=stats,
                    warnings=warnings or None,
                    updated_at=func.now(),
                )
            )
            await db.commit()
            asyncio.create_task(pre_cache_all_collected_bg())
        except Exception as exc:
            logger.exception("Stremio sync job %s failed", job_id)
            await db.rollback()
            await db.execute(
                update(SyncJob)
                .where(SyncJob.id == job_id)
                .values(status=SyncStatus.failed, error_message=str(exc)[:900])
            )
            await db.commit()


async def _apply_arvio_watched_movie(
    db: AsyncSession,
    user_id: int,
    item: dict[str, Any] | int | str,
    tmdb_api_key: str | None,
) -> bool:
    if isinstance(item, (int, str)):
        item = {"tmdbId": item}
    elif not isinstance(item, dict):
        return False

    tmdb_id_raw = item.get("tmdbId") or item.get("tmdb_id") or item.get("id")
    if not tmdb_id_raw:
        return False
    try:
        tmdb_id = int(tmdb_id_raw)
    except (TypeError, ValueError):
        return False

    # updatedAt (in addition to updatedAtMs) matters here now too: a completed
    # continue-watching movie routed in via _apply_arvio_playback_progress's
    # high-completion branch may only carry that field, same as the episode
    # version of this fallback chain below.
    watched_at = arvio_payloads.parse_timestamp(item.get("watchedAt") or item.get("timestamp") or item.get("updatedAtMs") or item.get("updatedAt"))

    result = await db.execute(
        select(Media).where(
            Media.tmdb_id == tmdb_id,
            Media.media_type == MediaType.movie,
        )
    )
    media = result.scalars().first()
    if not media:
        title = str(item.get("title") or f"Movie {tmdb_id}")
        media = Media(tmdb_id=tmdb_id, media_type=MediaType.movie, title=title)
        db.add(media)
        await db.flush()
        if tmdb_api_key:
            await enrich_media(media, api_key=tmdb_api_key)

    window_minutes = await get_dedup_window_minutes(db, user_id)
    from core.watch_dates import inferred_watch_datetime, reconcile_inferred_watch_date
    if watched_at is not None and await reconcile_inferred_watch_date(db, user_id, media.id, watched_at):
        return False
    existing = await find_duplicate_watch_event(db, user_id, media.id, watched_at, window_minutes)
    if not existing:
        event = WatchEvent(
            user_id=user_id,
            media_id=media.id,
            completed=True,
            watched_at=watched_at or inferred_watch_datetime(),
            date_inferred=watched_at is None,
        )
        db.add(event)
        await db.commit()
        return True
    return False


async def _apply_arvio_watched_episode(
    db: AsyncSession,
    user_id: int,
    item: dict[str, Any] | int | str,
    tmdb_api_key: str | None,
) -> bool:
    info = arvio_payloads.parse_episode_info(item)
    if not info:
        return False

    show_tmdb_id, season, episode = info

    watched_at = None
    if isinstance(item, dict):
        watched_at = arvio_payloads.parse_timestamp(item.get("watchedAt") or item.get("timestamp") or item.get("updatedAtMs") or item.get("updatedAt"))

    show_res = await db.execute(select(Show).where(Show.tmdb_id == show_tmdb_id))
    show = show_res.scalars().first()
    if not show:
        show_title = f"Show {show_tmdb_id}"
        if isinstance(item, dict):
            show_title = str(item.get("title") or item.get("showTitle") or item.get("seriesTitle") or show_title)
        show = Show(tmdb_id=show_tmdb_id, title=show_title)
        db.add(show)
        await db.flush()

    ep_res = await db.execute(
        select(Media).where(
            Media.show_id == show.id,
            Media.season_number == season,
            Media.episode_number == episode,
            Media.media_type == MediaType.episode,
        )
    )
    media = ep_res.scalars().first()
    if not media:
        ep_title = f"S{season:02d}E{episode:02d}"
        if isinstance(item, dict):
            ep_title = str(item.get("episodeTitle") or item.get("title") or ep_title)
        media = Media(
            show_id=show.id,
            season_number=season,
            episode_number=episode,
            media_type=MediaType.episode,
            title=ep_title,
        )
        db.add(media)
        await db.flush()
        if tmdb_api_key:
            await enrich_media(media, api_key=tmdb_api_key)

    window_minutes = await get_dedup_window_minutes(db, user_id)
    from core.watch_dates import inferred_watch_datetime, reconcile_inferred_watch_date
    if watched_at is not None and await reconcile_inferred_watch_date(db, user_id, media.id, watched_at):
        return False
    existing = await find_duplicate_watch_event(db, user_id, media.id, watched_at, window_minutes)
    if not existing:
        event = WatchEvent(
            user_id=user_id,
            media_id=media.id,
            completed=True,
            watched_at=watched_at or inferred_watch_datetime(),
            date_inferred=watched_at is None,
        )
        db.add(event)
        await db.commit()
        return True
    return False


async def _apply_arvio_playback_progress(
    db: AsyncSession,
    user_id: int,
    item: dict[str, Any] | int | str,
    tmdb_api_key: str | None,
) -> bool:
    if isinstance(item, str):
        try:
            item = json.loads(item)
        except json.JSONDecodeError:
            return False
    if not isinstance(item, dict):
        return False

    media_type_str = str(item.get("mediaType") or "").upper()
    progress_val = item.get("progress", 0)
    try:
        progress_pct = float(progress_val)
        if progress_pct <= 1.0 and progress_pct > 0:
            progress_pct *= 100.0
    except (TypeError, ValueError):
        progress_pct = 0.0

    is_completed = item.get("completed") is True or progress_pct >= 85.0

    if is_completed:
        ep_info = arvio_payloads.parse_episode_info(item)
        if ep_info:
            return await _apply_arvio_watched_episode(db, user_id, item, tmdb_api_key)
        else:
            return await _apply_arvio_watched_movie(db, user_id, item, tmdb_api_key)

    if progress_pct < 1.0:
        return False

    pos_sec = item.get("resumePositionSeconds") or item.get("positionSeconds") or item.get("position")
    dur_sec = item.get("durationSeconds") or item.get("duration")

    try:
        position_seconds = float(pos_sec) if pos_sec is not None else 0.0
    except (TypeError, ValueError):
        position_seconds = 0.0

    try:
        duration_seconds = float(dur_sec) if dur_sec is not None else 0.0
    except (TypeError, ValueError):
        duration_seconds = 0.0

    if position_seconds <= 0 and duration_seconds > 0 and progress_pct > 0:
        position_seconds = (progress_pct / 100.0) * duration_seconds

    updated_at = arvio_payloads.parse_timestamp(item.get("updatedAtMs") or item.get("updatedAt")) or datetime.now(timezone.utc).replace(tzinfo=None)

    season_raw = item.get("season")
    episode_raw = item.get("episode")
    is_episode = (
        media_type_str in ("TV", "EPISODE", "SERIES")
        or (season_raw is not None and episode_raw is not None)
        or arvio_payloads.parse_episode_info(item) is not None
    )

    media: Media | None = None
    if is_episode:
        ep_info = arvio_payloads.parse_episode_info(item)
        if not ep_info:
            return False
        show_tmdb_id, season, episode = ep_info

        show_res = await db.execute(select(Show).where(Show.tmdb_id == show_tmdb_id))
        show = show_res.scalars().first()
        if not show:
            show_title = str(item.get("seriesTitle") or item.get("title") or f"Show {show_tmdb_id}")
            show = Show(tmdb_id=show_tmdb_id, title=show_title)
            db.add(show)
            await db.flush()

        ep_res = await db.execute(
            select(Media).where(
                Media.show_id == show.id,
                Media.season_number == season,
                Media.episode_number == episode,
                Media.media_type == MediaType.episode,
            )
        )
        media = ep_res.scalars().first()
        if not media:
            ep_title = str(item.get("episodeTitle") or item.get("title") or f"S{season:02d}E{episode:02d}")
            media = Media(
                show_id=show.id,
                season_number=season,
                episode_number=episode,
                media_type=MediaType.episode,
                title=ep_title,
            )
            db.add(media)
            await db.flush()
            if tmdb_api_key:
                await enrich_media(media, api_key=tmdb_api_key)
    else:
        tmdb_raw = item.get("tmdbId") or item.get("tmdb_id") or item.get("id")
        if not tmdb_raw:
            return False
        try:
            tmdb_id = int(tmdb_raw)
        except (TypeError, ValueError):
            return False

        m_res = await db.execute(
            select(Media).where(
                Media.tmdb_id == tmdb_id,
                Media.media_type == MediaType.movie,
            )
        )
        media = m_res.scalars().first()
        if not media:
            title = str(item.get("title") or f"Movie {tmdb_id}")
            media = Media(tmdb_id=tmdb_id, media_type=MediaType.movie, title=title)
            db.add(media)
            await db.flush()
            if tmdb_api_key:
                await enrich_media(media, api_key=tmdb_api_key)

    if not media:
        return False

    pp_res = await db.execute(
        select(PlaybackProgress).where(
            PlaybackProgress.user_id == user_id,
            PlaybackProgress.media_id == media.id,
        )
    )
    pp = pp_res.scalars().first()
    if not pp:
        pp = PlaybackProgress(
            user_id=user_id,
            media_id=media.id,
            progress_seconds=int(position_seconds),
            progress_percent=progress_pct,
            updated_at=updated_at,
        )
        db.add(pp)
    else:
        pp.progress_seconds = int(position_seconds)
        pp.progress_percent = progress_pct
        pp.updated_at = updated_at

    await db.commit()
    return True


async def run_arvio_sync(
    user_id: int,
    job_id: int,
    movie_limit: int,
    show_limit: int,
    connection_id: int | None = None,
) -> None:
    async with _sync_semaphore:
        await _run_arvio_sync(user_id, job_id, movie_limit, show_limit, connection_id)


async def _run_arvio_sync(
    user_id: int,
    job_id: int,
    movie_limit: int,
    show_limit: int,
    connection_id: int | None = None,
) -> None:
    logger.info("Starting ARVIO sync for user %s, job %s", user_id, job_id)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        try:
            if not await mark_job_running_unless_cancelled(
                db, job_id, processed_items=0, total_items=0, updated_at=func.now(),
            ):
                logger.info("ARVIO sync job %s was cancelled before it started - skipping", job_id)
                return

            settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
            settings = settings_result.scalar_one_or_none()
            tmdb_api_key = await settings_store.get_effective_tmdb_key(db, settings)

            conn_query = select(MediaServerConnection).where(
                MediaServerConnection.user_id == user_id,
                MediaServerConnection.type == "arvio",
            )
            if connection_id:
                conn_query = conn_query.where(MediaServerConnection.id == connection_id)
            else:
                conn_query = conn_query.order_by(MediaServerConnection.id.asc()).limit(1)
            conn_result = await db.execute(conn_query)
            conn = conn_result.scalar_one_or_none()
            if not conn:
                raise RuntimeError("Missing ARVIO connection")

            profile_id = str(conn.server_user_id) if conn.server_user_id is not None else ""
            if not profile_id:
                try:
                    # validate_connection redeems (rotates) conn.token, so the
                    # resulting session must be persisted here - discarding it
                    # would leave conn.token pointing at an already-used
                    # token, breaking the pull_sync_data call below.
                    async with arvio.connection_lock(conn.id):
                        await db.refresh(conn)
                        session, profiles = await arvio.validate_connection(conn.url, conn.token)
                        conn.token = session.refresh_token
                        if profiles:
                            profile_id = profiles[0]["id"]
                            conn.server_user_id = profile_id
                        else:
                            profile_id = "0"
                        await db.commit()
                except Exception:
                    profile_id = "0"

            async def _persist_refresh(refreshed: arvio.ArvioSession) -> None:
                conn.token = refreshed.refresh_token
                await db.commit()

            async with arvio.connection_lock(conn.id):
                # See core/nuvio.py's connection_lock docstring - conn may
                # have been loaded before another request already rotated
                # this single-use refresh token while this one waited.
                await db.refresh(conn)
                session, sync_data = await arvio.pull_sync_data(
                    conn.url,
                    conn.token,
                    profile_id,
                    on_refresh=_persist_refresh,
                )
                conn.token = session.refresh_token
                await db.commit()

            watched_movies = sync_data.get("watched_movies", [])
            watched_episodes = sync_data.get("watched_episodes", [])
            progress_items = sync_data.get("progress", [])

            total_items = len(watched_movies) + len(watched_episodes) + len(progress_items)
            await db.execute(
                update(SyncJob)
                .where(SyncJob.id == job_id)
                .values(total_items=total_items, updated_at=func.now())
            )
            await db.commit()

            processed = 0

            if conn.sync_watched:
                for movie_item in watched_movies:
                    await raise_if_cancelled(db, job_id)
                    await _apply_arvio_watched_movie(db, user_id, movie_item, tmdb_api_key)
                    processed += 1
                    if processed % 10 == 0:
                        await db.execute(
                            update(SyncJob)
                            .where(SyncJob.id == job_id)
                            .values(processed_items=processed, updated_at=func.now())
                        )
                        await db.commit()

                for ep_item in watched_episodes:
                    await raise_if_cancelled(db, job_id)
                    await _apply_arvio_watched_episode(db, user_id, ep_item, tmdb_api_key)
                    processed += 1
                    if processed % 10 == 0:
                        await db.execute(
                            update(SyncJob)
                            .where(SyncJob.id == job_id)
                            .values(processed_items=processed, updated_at=func.now())
                        )
                        await db.commit()

            if conn.sync_playback:
                for cw_item in progress_items:
                    await raise_if_cancelled(db, job_id)
                    await _apply_arvio_playback_progress(db, user_id, cw_item, tmdb_api_key)
                    processed += 1
                    if processed % 10 == 0:
                        await db.execute(
                            update(SyncJob)
                            .where(SyncJob.id == job_id)
                            .values(processed_items=processed, updated_at=func.now())
                        )
                        await db.commit()

            await db.execute(
                update(SyncJob)
                .where(SyncJob.id == job_id)
                .values(
                    status=SyncStatus.completed,
                    processed_items=processed,
                    updated_at=func.now(),
                )
            )
            await db.commit()
            logger.info("ARVIO sync completed for user %s, job %s: movies=%s episodes=%s cw=%s", user_id, job_id, len(watched_movies), len(watched_episodes), len(progress_items))

        except SyncCancelled:
            logger.info("ARVIO sync job %s cancelled", job_id)
        except Exception as exc:
            logger.error("ARVIO sync job %s failed: %s", job_id, exc, exc_info=True)
            await db.execute(
                update(SyncJob)
                .where(SyncJob.id == job_id)
                .values(
                    status=SyncStatus.failed,
                    error_message=str(exc)[:900],
                    updated_at=func.now(),
                )
            )
            await db.commit()


WATCHED_LOOKUP_FAILED_REASON = (
    "Not found on this server - no matching library item for this watch"
)


def watched_lookup_failed_warning(media_id: int, media: Media | None, series_name: str | None = None) -> dict:
    """Warning dict for a watch the full-push slow path could not resolve.

    series_name (episodes only) lets Connections group these the same way it
    already groups pull-side unmatched warnings - a large legacy watch
    history against a partial library can produce thousands of these for a
    handful of shows, and one row per episode makes the panel unusable (#400).
    """
    is_episode = bool(media and media.media_type == MediaType.episode)
    return {
        "type": "watched_lookup_failed",
        "media_id": media_id,
        "title": media.title if media else None,
        "media_type": media.media_type.value if media and media.media_type else None,
        "series_name": series_name if is_episode else None,
        "reason": WATCHED_LOOKUP_FAILED_REASON,
    }


async def _run_full_push(user_id: int, connection_id: int, job_id: int) -> None:
    import httpx as _httpx

    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        if not await mark_job_running_unless_cancelled(db, job_id):
            print(f"Full push job {job_id} was cancelled before it started - skipping")
            return

        try:
            conn_result = await db.execute(
                select(MediaServerConnection).where(
                    MediaServerConnection.id == connection_id,
                    MediaServerConnection.user_id == user_id,
                )
            )
            conn = conn_result.scalar_one_or_none()
            if not conn:
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed, error_message="Connection not found"))
                await db.commit()
                return

            from core.tracking_snapshot import require_stream_reconciliation
            await require_stream_reconciliation(db, conn)

            if conn.type == "stremio":
                settings_result = await db.execute(
                    select(UserSettings).where(UserSettings.user_id == user_id)
                )
                user_settings = settings_result.scalar_one_or_none()
                api_key = await settings_store.get_effective_tmdb_key(db, user_settings)
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(current_step="Pushing to Stremio"))
                await db.commit()
                changed = await stremio_delivery.push_connection(
                    db,
                    conn,
                    user_id,
                    api_key=api_key,
                )
                await db.execute(
                    update(SyncJob)
                    .where(SyncJob.id == job_id)
                    .values(
                        status=SyncStatus.completed,
                        total_items=changed,
                        processed_items=changed,
                        stats={"succeeded": changed, "failed": 0},
                    )
                )
                await db.commit()
                logger.info(
                    "Full Stremio push for connection %s: %s changed items",
                    connection_id,
                    changed,
                )
                return

            if conn.type == "nuvio":
                from models.tracking import StreamBaseline
                baseline = await db.get(StreamBaseline, conn.id)
                if not isinstance(getattr(baseline, "snapshot", None), dict):
                    baseline = None
                settings_result = await db.execute(
                    select(UserSettings).where(UserSettings.user_id == user_id)
                )
                user_settings = settings_result.scalar_one_or_none()
                api_key = await settings_store.get_effective_tmdb_key(db, user_settings)
                library_items = (
                    await nuvio_projection.build_library_items(db, user_id, api_key=api_key, baseline=baseline)
                    if conn.push_collection
                    else []
                )
                current_library_ids = {
                    str(item["content_id"])
                    for item in library_items
                }
                previously_managed_library_ids = set(
                    conn.stremio_pushed_library_ids or []
                )
                removed_library_ids = (
                    previously_managed_library_ids - current_library_ids
                    if conn.push_collection
                    and conn.stremio_pushed_library_ids is not None
                    else set()
                )
                watched_items = (
                    await nuvio_projection.build_watched_items(db, user_id, api_key=api_key,
                        baseline=baseline, tracked_only=True)
                    if conn.push_watched
                    else []
                )
                progress_items = (
                    await nuvio_projection.build_progress_items(db, user_id, api_key=api_key,
                        baseline=baseline, next_up_for_watched_series=True)
                    if conn.push_playback
                    else []
                )
                total = len(library_items) + len(watched_items) + len(progress_items)
                await db.execute(
                    update(SyncJob)
                    .where(SyncJob.id == job_id)
                    .values(total_items=total, processed_items=0, current_step="Pushing to Nuvio")
                )
                await db.commit()

                async def _persist_refresh(session: nuvio.NuvioSession) -> None:
                    conn.token = session.refresh_token
                    await db.commit()

                watched_to_push: list[dict] = []
                async with nuvio.connection_lock(conn.id):
                    # See core/nuvio.py's connection_lock docstring - conn may
                    # have been loaded before another request already rotated
                    # this single-use refresh token while this one waited.
                    await refresh_stream_connection(db, conn)
                    if conn.push_collection:
                        # Remove only IDs a previous successful AnyList push
                        # managed. Remote-only rows remain untouched, while a failed
                        # real-time delta remains retryable on this scheduled push.
                        await nuvio.merge_library(
                            conn.url,
                            conn.token,
                            nuvio_payloads.profile_id(conn),
                            additions=library_items,
                            removed_content_ids=removed_library_ids,
                            on_refresh=_persist_refresh,
                        )
                        conn.stremio_pushed_library_ids = sorted(current_library_ids)
                    if conn.push_watched or conn.push_playback:
                        async with nuvio.httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                            session = await nuvio.refresh_session(conn.url, conn.token, client=client)
                            await _persist_refresh(session)
                            profile = nuvio_payloads.profile_id(conn)
                            def watch_identity(row: dict) -> tuple[str, str, int | None, int | None]:
                                return (str(row.get("content_id") or ""),
                                    str(row.get("content_type") or ""),
                                    row.get("season"), row.get("episode"))
                            remote_watches = (
                                await nuvio._pull_watched_items(client, conn.url, session.access_token, profile)
                                if conn.push_watched or conn.push_playback else []
                            )
                            if conn.push_watched or conn.push_playback:
                                from core.nuvio_visibility import next_up_visibility
                                hidden, visible, seeds = await next_up_visibility(db, user_id, conn.id,
                                    [*remote_watches, *watched_items, *progress_items])
                                await nuvio.update_next_up_dismissals(client, conn.url,
                                    session.access_token, profile, hide=hidden, show=visible, seeds=seeds,
                                    on_written=lambda written: _record_full_push_visibility_echo(
                                        db, conn, written))
                            remote_watch_by_key = {watch_identity(row): row for row in remote_watches}
                            watched_to_push = [item for item in watched_items
                                if watch_identity(item) not in remote_watch_by_key
                                or not isinstance(remote_watch_by_key[watch_identity(item)].get("watched_at"), int)]
                            if any(not isinstance(item.get("watched_at"), int) for item in watched_to_push):
                                raise nuvio.NuvioAPIError("Cannot push a watched item without a numeric watch date")
                            remote_progress = (
                                await nuvio._pull_watch_progress(client, conn.url, session.access_token, profile)
                                if conn.push_playback else []
                            )
                            clear_keys = (
                                await nuvio_projection.progress_keys_to_clear(db, user_id, conn.id, remote_progress)
                                if conn.push_playback else []
                            )
                            if conn.push_playback and progress_items:
                                clear_keys = list(dict.fromkeys([
                                    *clear_keys,
                                    *nuvio_payloads.obsolete_progress_keys(remote_progress, progress_items),
                                ]))
                            if clear_keys:
                                await nuvio._rpc(client, conn.url, session.access_token,
                                    "sync_delete_watch_progress", {"p_profile_id": profile, "p_keys": clear_keys})
                            for offset in range(0, len(watched_to_push), nuvio._PAGE_SIZE):
                                await nuvio._rpc(client, conn.url, session.access_token,
                                    "sync_push_watched_items", {"p_profile_id": profile,
                                        "p_items": watched_to_push[offset:offset + nuvio._PAGE_SIZE]})
                            if watched_items:
                                confirmed_watches = await nuvio._pull_watched_items(
                                    client, conn.url, session.access_token, profile,
                                )
                                desired_watch_keys = {watch_identity(item) for item in watched_items}
                                confirmed_keys = {watch_identity(row) for row in confirmed_watches}
                                if not desired_watch_keys.issubset(confirmed_keys):
                                    raise nuvio.NuvioAPIError(
                                        "Nuvio did not confirm the pushed watched episodes"
                                    )
                                if any(not isinstance(row.get("watched_at"), int)
                                       for row in confirmed_watches
                                       if watch_identity(row) in desired_watch_keys):
                                    raise nuvio.NuvioAPIError(
                                        "Nuvio returned watched items without usable watch dates"
                                    )
                            pushed_progress = []
                            synthetic_progress_keys: set[str] = set()
                            for item in progress_items:
                                payload = dict(item)
                                payload.setdefault("progress_key", f"{payload['content_id']}_s{payload['season']}e{payload['episode']}"
                                    if payload.get("season") is not None else payload["content_id"])
                                if payload.pop("synthetic_resume", False):
                                    synthetic_progress_keys.add(str(payload["progress_key"]))
                                pushed_progress.append(payload)
                            for offset in range(0, len(pushed_progress), nuvio._PAGE_SIZE):
                                await nuvio._rpc(client, conn.url, session.access_token,
                                    "sync_push_watch_progress", {"p_profile_id": profile,
                                        "p_entries": pushed_progress[offset:offset + nuvio._PAGE_SIZE]})
                            if clear_keys or pushed_progress:
                                confirmed_rows = await nuvio._pull_watch_progress(
                                    client, conn.url, session.access_token, profile,
                                )
                                confirmed_by_key: dict[str, list[dict]] = {}
                                for row in confirmed_rows:
                                    if row.get("progress_key"):
                                        confirmed_by_key.setdefault(str(row["progress_key"]), []).append(row)
                                if any(key in confirmed_by_key for key in clear_keys):
                                    raise nuvio.NuvioAPIError("Nuvio did not confirm removal of stale progress")
                                for payload in pushed_progress:
                                    matches = confirmed_by_key.get(str(payload["progress_key"]), [])
                                    if len(matches) != 1 or not nuvio_payloads.progress_matches(matches[0], payload):
                                        raise nuvio.NuvioAPIError("Nuvio did not confirm the pushed progress")
                                from models.tracking import StreamBaseline
                                managed_mappings = await nuvio_projection.progress_mappings_for_items(
                                    db, user_id, pushed_progress,
                                ) if pushed_progress else {}
                                baseline = await db.get(StreamBaseline, conn.id)
                                if baseline is not None:
                                    snapshot = dict(baseline.snapshot or {})
                                    mappings = dict(snapshot.get("mappings", {}))
                                    mappings.update(managed_mappings)
                                    outbound = dict(snapshot.get("outbound", {}))
                                    clear_content_ids = {
                                        str(row.get("content_id")) for row in remote_progress
                                        if str(row.get("progress_key") or "") in set(clear_keys)
                                    }
                                    for content_id in clear_content_ids:
                                        outbound.pop(content_id, None)
                                    for payload in pushed_progress:
                                        outbound[str(payload["content_id"])] = {
                                            field: payload.get(field)
                                            for field in ("position", "duration", "season", "episode", "observed_at")
                                        }
                                        outbound[str(payload["content_id"])]["progress_key"] = payload["progress_key"]
                                        outbound[str(payload["content_id"])]["action"] = "upsert"
                                        outbound[str(payload["content_id"])]["synthetic_resume"] = (
                                            str(payload["progress_key"]) in synthetic_progress_keys
                                        )
                                        if payload.get("last_watched") is not None:
                                            outbound[str(payload["content_id"])]["last_watched"] = payload["last_watched"]
                                    snapshot["mappings"] = mappings
                                    snapshot["outbound"] = outbound
                                    baseline.snapshot = snapshot

                await db.execute(
                    update(SyncJob)
                    .where(SyncJob.id == job_id)
                    .values(
                        status=SyncStatus.completed,
                        processed_items=total,
                        stats={
                            "succeeded": len(library_items) + len(watched_to_push) + len(progress_items),
                            "failed": 0,
                            "collection": len(library_items),
                            "watched": len(watched_to_push),
                            "progress": len(progress_items),
                            "already_watched": len(watched_items) - len(watched_to_push),
                        },
                    )
                )
                await db.commit()
                logger.info(
                    "Full Nuvio push for connection %s: %s collection, %s watched, "
                    "and %s progress items",
                    connection_id,
                    len(library_items),
                    len(watched_to_push),
                    len(progress_items),
                )
                return

            conn_source = CollectionSource(conn.type)

            if conn.type == "plex" and conn.plex_push_watchlist:
                # The reconcile can import remote-only items when the pull
                # direction is also enabled, so it needs a TMDB key here too.
                wl_settings_result = await db.execute(
                    select(UserSettings).where(UserSettings.user_id == user_id)
                )
                wl_tmdb_key = await settings_store.get_effective_tmdb_key(db, wl_settings_result.scalar_one_or_none())
                await plex_watchlist.reconcile_watchlist(user_id, conn.id, wl_tmdb_key)

            watched_ids: set[int] = set()
            ratings_map: RatingChanges = {}

            if conn.push_watched:
                # completed only - a WatchEvent can also be a started-but-
                # unfinished play (#253) or a manually logged partial watch,
                # and pushing one of those marks the item fully watched.
                watched_result = await db.execute(
                    select(WatchEvent.media_id).where(
                        WatchEvent.user_id == user_id,
                        WatchEvent.completed == True,  # noqa: E712
                    ).distinct()
                )
                watched_ids = {row[0] for row in watched_result.all()}

                # A show mid-rewatch has deliberately unwatched episodes on
                # the server again (seasons not yet reached this cycle) -
                # pushing full history would re-mark all of them watched and
                # destroy the server's own Next Up/Continue Watching position
                # for that show (#306). Scope those shows' pushed set to what
                # has actually been (re)watched this cycle instead; shows
                # with no active rewatch (or a completed one) keep today's
                # full-history behaviour. The excluded episodes are simply
                # omitted from this push, not actively unmarked, so anyone
                # whose server legitimately has them watched from before the
                # rewatch keeps that - same "don't push_watched=False"
                # asymmetry _handle_unwatch_toggle already relies on.
                show_id_by_media: dict[int, int] = {}
                watched_list = list(watched_ids)
                for i in range(0, len(watched_list), _MAX_IN_PARAMS):
                    chunk = watched_list[i : i + _MAX_IN_PARAMS]
                    rows = await db.execute(
                        select(Media.id, Media.show_id).where(Media.id.in_(chunk), Media.show_id.isnot(None))
                    )
                    show_id_by_media.update(dict(rows.all()))

                show_ids = list(set(show_id_by_media.values()))
                active_rewatches_by_show_id = await get_active_rewatches_for_shows(db, user_id, show_ids) if show_ids else {}
                if active_rewatches_by_show_id:
                    progress_q = await db.execute(
                        select(RewatchProgress.media_id).where(
                            RewatchProgress.rewatch_id.in_([r.id for r in active_rewatches_by_show_id.values()])
                        )
                    )
                    rewatch_progressed_media_ids = {row[0] for row in progress_q.all()}
                    watched_ids = {
                        mid for mid in watched_ids
                        if show_id_by_media.get(mid) not in active_rewatches_by_show_id
                        or mid in rewatch_progressed_media_ids
                    }

            if conn.push_ratings:
                ratings_result = await db.execute(
                    select(Rating.media_id, Rating.season_number, Rating.rating).where(
                        Rating.user_id == user_id,
                        Rating.rating.isnot(None),
                        Rating.episode_order.is_(None),
                    )
                )
                ratings_map = {
                    (media_id, season_number): float(rating)
                    for media_id, season_number, rating in ratings_result.all()
                }

            watched_at_by_media = (
                await db_queries.latest_watched_at(db, user_id, list(watched_ids))
                if watched_ids and conn.type in ("jellyfin", "emby") else {}
            )
            all_media_ids = watched_ids | {media_id for media_id, _ in ratings_map}
            if not all_media_ids:
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.completed, total_items=0, processed_items=0))
                await db.commit()
                print(f"Full push for connection {connection_id}: nothing to push")
                return

            # Fast path: items we've already synced from this server have a known source_id
            source_ids_map: dict[int, list[str]] = {}
            all_media_list = list(all_media_ids)
            for i in range(0, len(all_media_list), _MAX_IN_PARAMS):
                chunk = all_media_list[i : i + _MAX_IN_PARAMS]
                files_chunk = await db.execute(
                    select(CollectionFile.source_id, Collection.media_id)
                    .join(Collection, Collection.id == CollectionFile.collection_id)
                    .where(
                        Collection.user_id == user_id,
                        Collection.media_id.in_(chunk),
                        CollectionFile.source == conn_source,
                        # Not just the source type - a ratingKey/item ID from a
                        # different connection of the same type (e.g. another
                        # Plex server) is meaningless here and would push to
                        # the wrong server. Items missing a connection_id
                        # (pre-migration data) fall through to the slow path.
                        CollectionFile.connection_id == conn.id,
                        CollectionFile.source_id.isnot(None),
                    )
                )
                for source_id, media_id in files_chunk.all():
                    source_ids_map.setdefault(media_id, []).append(source_id)

            # Slow path: unknown items and Plex season ratings need media metadata.
            missing_ids = all_media_ids - set(source_ids_map)
            season_rating_ids = {
                media_id
                for media_id, season_number in ratings_map
                if season_number is not None
            }
            lookup_media_ids = missing_ids | season_rating_ids
            media_info: dict[int, Media] = {}
            show_tmdb_map: dict[int, int] = {}  # show.id → show.tmdb_id
            show_tvdb_map: dict[int, int] = {}  # show.id → show.tvdb_id, fallback for TVDB-only shows (#436)
            show_title_map: dict[int, str] = {}  # show.id → show.title, for grouping lookup-failed warnings (#400)

            if lookup_media_ids:
                media_rows_list = await _select_in_chunks(
                    db,
                    lambda chunk: select(Media).where(Media.id.in_(chunk)),
                    list(lookup_media_ids),
                )
                for media in media_rows_list:
                    media_info[media.id] = media

                show_ids_needed = {m.show_id for m in media_info.values() if m.show_id is not None}
                if show_ids_needed:
                    show_ids_list = list(show_ids_needed)
                    for i in range(0, len(show_ids_list), _MAX_IN_PARAMS):
                        chunk = show_ids_list[i : i + _MAX_IN_PARAMS]
                        show_rows = await db.execute(select(Show.id, Show.tmdb_id, Show.tvdb_id, Show.title).where(Show.id.in_(chunk)))
                        for row in show_rows.all():
                            show_tmdb_map[row[0]] = row[1]
                            if row[2] is not None:
                                show_tvdb_map[row[0]] = row[2]
                            show_title_map[row[0]] = row[3]

            # For Jellyfin/Emby, AnyProviderIdEquals can't be trusted to
            # narrow results on every server version - a per-item lookup can
            # silently degrade into a full library scan (#300). When this job
            # actually needs live lookups against one of those connections,
            # build a movie/series tmdb_id -> item_id index once up front
            # instead, so every per-item lookup below is a dict lookup (or,
            # for episodes, one cheap SeriesId-scoped request) rather than a
            # request against the unreliable filter.
            jellyfin_movie_index: dict[int, str] = {}
            jellyfin_series_index: dict[int, str] = {}
            jellyfin_series_tvdb_index: dict[int, str] = {}
            if conn.type in ("jellyfin", "emby") and media_info:
                client_mod = jellyfin if conn.type == "jellyfin" else emby
                if any(m.media_type == MediaType.movie for m in media_info.values()):
                    jellyfin_movie_index = await client_mod.build_tmdb_index(conn.url, conn.token, "Movie")
                if any(m.media_type == MediaType.episode for m in media_info.values()):
                    jellyfin_series_index = await client_mod.build_tmdb_index(conn.url, conn.token, "Series")
                    # Shows Scrob only ever matched via TVDB (e.g. a legacy-agent
                    # Plex library) have no Show.tmdb_id at all, so the index
                    # above can never resolve them - only build this second,
                    # TVDB-keyed index when at least one such show is actually
                    # in play (#436).
                    if any(
                        m.media_type == MediaType.episode and m.show_id and not show_tmdb_map.get(m.show_id) and show_tvdb_map.get(m.show_id)
                        for m in media_info.values()
                    ):
                        jellyfin_series_tvdb_index = await client_mod.build_tvdb_index(conn.url, conn.token, "Series")

            # Build push list: (action, source_id, [rating])
            push_items: list[tuple] = []

            # Jellyfin/Emby's UserDataSaved webhook can echo a mark-watched push
            # straight back and, without this, land as a brand new WatchEvent
            # stamped at push time (see #247/#251). Its echo-suppression token
            # is armed in _push_watched_group below, right before the actual
            # mark_watched call - not here, up front: a full push of a large
            # library runs well past the token's 10-minute TTL, so anything
            # armed at the start of the job would have expired before its own
            # echo came back (#372).
            echoes_watched = conn.type in ("jellyfin", "emby")

            # A combined multi-episode file (Jellyfin/Emby's IndexNumber..
            # IndexNumberEnd) is ONE server item shared by N local media rows.
            # Pushing watched N times - once per row, as the code used to -
            # makes the server echo N times, and each echo (correctly) expands
            # over all N rows, since the payload can't say which sub-episode
            # triggered it. N echoes x N rows raced the 5-minute duplicate
            # guard below into writing spurious WatchEvent rows even though N
            # tokens were armed for exactly N echoes (#298). Grouping every
            # media row by its resolved server item id and sending exactly one
            # mark_watched per group - after arming a token for every row in
            # it - balances the books: one echo, expanded over exactly the
            # rows whose tokens were armed for it.
            watched_sid_to_mids: dict[str, set[int]] = {}
            # Jellyfin/Emby's own watched state for every sid in
            # watched_sid_to_mids, batch-fetched right before the push loop
            # below runs (see #362) - _already_watched_on_server then reads
            # this instead of making its own request per item.
            jellyfin_watched_state: dict[str, bool] = {}

            if conn.push_watched:
                for mid in watched_ids:
                    for sid in source_ids_map.get(mid, []):
                        if echoes_watched:
                            watched_sid_to_mids.setdefault(sid, set()).add(mid)
                        else:
                            push_items.append(("watched", sid, mid))

            if conn.push_ratings:
                for (mid, season_number), rating in ratings_map.items():
                    if season_number is not None:
                        continue
                    for sid in source_ids_map.get(mid, []):
                        push_items.append(("rating", sid, rating))

            # Items that need live lookup: defer as coroutines resolved during push.
            lookup_items: list[tuple] = []
            # Watched items still needing a live server-item lookup (Jellyfin/
            # Emby only) - resolved up front, below, so a combined file with
            # some rows already known and some still missing still lands in
            # one shared group instead of being pushed twice.
            watched_lookup_mids: list[int] = []

            if missing_ids:
                if conn.push_watched:
                    for mid in watched_ids & missing_ids:
                        if mid in media_info:
                            if echoes_watched:
                                watched_lookup_mids.append(mid)
                            else:
                                lookup_items.append(("watched", mid))
                if conn.push_ratings:
                    for key, rating in ratings_map.items():
                        mid, season_number = key
                        if season_number is None and mid in missing_ids and mid in media_info:
                            lookup_items.append(("rating", mid, rating))
            if conn.type == "plex" and conn.push_ratings:
                for (mid, season_number), rating in ratings_map.items():
                    if season_number is not None and mid in media_info:
                        lookup_items.append(("season_rating", mid, season_number, rating))

            watched_group_row_count = sum(len(mids) for mids in watched_sid_to_mids.values())
            total = len(push_items) + len(lookup_items) + watched_group_row_count + len(watched_lookup_mids)
            if total == 0:
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.completed, total_items=0, processed_items=0))
                await db.commit()
                print(f"Full push for connection {connection_id}: no items found for this server")
                return

            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(total_items=total, processed_items=0, current_step="Pushing watched status & ratings"))
            await db.commit()
            print(f"Full push for connection {connection_id}: pushing {total} items ({len(push_items)} known, {len(lookup_items)} via live lookup, {watched_group_row_count} watched rows in {len(watched_sid_to_mids)} groups, {len(watched_lookup_mids)} watched rows pending lookup)...")

            sem = asyncio.Semaphore(10)
            # Separate, higher limit for _resolve_watched_lookup only - a
            # read-only "does this exist" check, unlike everything else
            # sharing `sem` (which also issues mutating mark_watched/
            # set_rating calls and is kept conservative on purpose). A large
            # legacy watch history against a partial library can mean
            # thousands of these, almost all misses, and gating them behind
            # the same limit as writes made a full push take minutes longer
            # than it needed to (#400).
            lookup_sem = asyncio.Semaphore(25)
            _PROGRESS_INTERVAL = 20

            def _extract_source_id(item_dict: dict | None) -> str | None:
                if not item_dict:
                    return None
                if conn.type == "plex":
                    rk = item_dict.get("ratingKey")
                    return str(rk) if rk else None
                return item_dict.get("Id")

            async def _find_source_id(mid: int) -> str | None:
                m = media_info.get(mid)
                if not m:
                    return None
                if m.media_type == MediaType.movie:
                    if not m.tmdb_id:
                        return None
                    if conn.type == "plex":
                        found = await plex.find_movie_by_tmdb_id(conn.url, conn.token, m.tmdb_id)
                    else:
                        # Resolved from the job's pre-built index (#300) -
                        # already the item id itself, no request needed.
                        return jellyfin_movie_index.get(m.tmdb_id)
                elif m.media_type == MediaType.episode:
                    show_tmdb = show_tmdb_map.get(m.show_id) if m.show_id else None
                    show_tvdb = show_tvdb_map.get(m.show_id) if m.show_id else None
                    if (not show_tmdb and not show_tvdb) or m.season_number is None or m.episode_number is None:
                        return None
                    if conn.type == "plex":
                        if not show_tmdb:
                            return None
                        found = await plex.find_episode_by_ids(conn.url, conn.token, show_tmdb, m.season_number, m.episode_number)
                    else:
                        series_id = jellyfin_series_index.get(show_tmdb) if show_tmdb else None
                        if not series_id and show_tvdb:
                            series_id = jellyfin_series_tvdb_index.get(show_tvdb)
                        if not series_id:
                            return None
                        client_mod = jellyfin if conn.type == "jellyfin" else emby
                        found = await client_mod.find_episode_in_series(
                            conn.url, conn.token, series_id, m.season_number, m.episode_number, user_id=conn.server_user_id
                        )
                else:
                    return None
                return _extract_source_id(found)

            async def _already_watched_on_server(sid: str) -> bool | None:
                """Plex's /:/scrobble (and Jellyfin/Emby's mark-watched call)
                are not idempotent - calling them on an item the server
                already shows as watched still bumps its last-viewed
                timestamp and mints a fresh watch-history/activity entry
                dated today, with no way to backdate it. Unconditionally
                re-pushing a user's entire watched history on every full
                push was silently corrupting the server's own watch
                history/activity feed on every run (#302). Always check the
                server's own current state first and skip the push
                entirely when it already agrees.

                Returns None when the check itself couldn't be completed
                (network error, item not found) - callers must treat that
                the same as "don't push": guessing wrong here risks the
                exact corruption this exists to prevent, whereas skipping a
                genuinely-new watch just means it's retried on the next
                full push instead.
                """
                try:
                    if conn.type == "plex":
                        item = await plex.get_item(conn.url, conn.token, sid)
                        if item is None:
                            return None
                        return int(item.get("viewCount") or 0) > 0
                    else:
                        # Batch-fetched up front into jellyfin_watched_state
                        # (#362) instead of a get_item call per sid here - a
                        # sid missing from it means "not found", the same
                        # signal get_item's None used to carry.
                        return jellyfin_watched_state.get(sid)
                except Exception:
                    return None

            async def _push_known(client: _httpx.AsyncClient, item: tuple) -> bool:
                async with sem:
                    try:
                        if item[0] == "watched":
                            sid = item[1]
                            already = await _already_watched_on_server(sid)
                            if already is None:
                                return False
                            if already:
                                return True
                            if conn.type == "plex":
                                ok = await plex.mark_watched(conn.url, conn.token, sid, client=client)
                                if ok:
                                    await outbound_sync.record_plex_pending_push(user_id, item[2])
                                return ok
                            elif conn.type == "jellyfin":
                                return await jellyfin.mark_watched(conn.url, conn.token, conn.server_user_id, sid, client=client, played_at=watched_at_by_media.get(item[2]))
                            else:
                                return await emby.mark_watched(conn.url, conn.token, conn.server_user_id, sid, client=client, played_at=watched_at_by_media.get(item[2]))
                        else:
                            sid, rating = item[1], item[2]
                            if conn.type == "plex":
                                return await plex.set_rating(conn.url, conn.token, sid, rating, client=client)
                            elif conn.type == "jellyfin":
                                return await jellyfin.set_rating(conn.url, conn.token, conn.server_user_id, sid, rating, client=client)
                            else:
                                return await emby.set_rating(conn.url, conn.token, conn.server_user_id, sid, rating, client=client)
                    except Exception:
                        return False

            async def _push_lookup(client: _httpx.AsyncClient, item: tuple) -> bool:
                async with sem:
                    try:
                        mid = item[1]
                        if item[0] == "season_rating":
                            media = media_info.get(mid)
                            if not media or not media.tmdb_id:
                                return False
                            sid = await plex.resolve_season_rating_key(
                                conn.url,
                                conn.token,
                                media.tmdb_id,
                                item[2],
                            )
                            if not sid:
                                return False
                            return await plex.set_rating(
                                conn.url,
                                conn.token,
                                sid,
                                item[3],
                                client=client,
                            )
                        sid = await _find_source_id(mid)
                        if not sid:
                            return False
                        if item[0] == "watched":
                            already = await _already_watched_on_server(sid)
                            if already is None:
                                return False
                            if already:
                                return True
                            if conn.type == "plex":
                                ok = await plex.mark_watched(conn.url, conn.token, sid, client=client)
                                if ok:
                                    await outbound_sync.record_plex_pending_push(user_id, mid)
                                return ok
                            elif conn.type == "jellyfin":
                                watch_echo.mark_pushed_watched(user_id, mid)
                                return await jellyfin.mark_watched(conn.url, conn.token, conn.server_user_id, sid, client=client, played_at=watched_at_by_media.get(mid))
                            else:
                                watch_echo.mark_pushed_watched(user_id, mid)
                                return await emby.mark_watched(conn.url, conn.token, conn.server_user_id, sid, client=client, played_at=watched_at_by_media.get(mid))
                        else:
                            rating = item[2]
                            if conn.type == "plex":
                                return await plex.set_rating(conn.url, conn.token, sid, rating, client=client)
                            elif conn.type == "jellyfin":
                                return await jellyfin.set_rating(conn.url, conn.token, conn.server_user_id, sid, rating, client=client)
                            else:
                                return await emby.set_rating(conn.url, conn.token, conn.server_user_id, sid, rating, client=client)
                    except Exception:
                        return False

            async def _resolve_watched_lookup(mid: int) -> tuple[int, str | None]:
                async with lookup_sem:
                    return mid, await _find_source_id(mid)

            async def _push_watched_group(client: _httpx.AsyncClient, sid: str, mids: set[int]) -> bool:
                # One deduped mark_watched per server item, expanded over the N
                # local rows that share it (#298). The echo-suppression token
                # for every one of those rows is armed here, immediately before
                # the call - not when this group was queued: on a long push
                # that was minutes ago, past the token's 10-minute TTL (#372).
                # Arming only once the already-watched checks have passed also
                # means a skipped push leaves no stray token behind.
                async with sem:
                    try:
                        already = await _already_watched_on_server(sid)
                        if already is None:
                            return False
                        if already:
                            return True
                        for mid in mids:
                            watch_echo.mark_pushed_watched(user_id, mid)
                        group_played_at = max(
                            (watched_at_by_media[mid] for mid in mids if watched_at_by_media.get(mid) is not None),
                            default=None,
                        )
                        if conn.type == "jellyfin":
                            return await jellyfin.mark_watched(conn.url, conn.token, conn.server_user_id, sid, client=client, played_at=group_played_at)
                        else:
                            return await emby.mark_watched(conn.url, conn.token, conn.server_user_id, sid, client=client, played_at=group_played_at)
                    except Exception:
                        return False

            done = 0
            succeeded = 0
            failed_count = 0
            # Items where the server-side item couldn't be resolved at all
            # (as opposed to a transient push/network failure) - the "silent
            # drop" the job's own stats.failed count doesn't call out on its
            # own (#300).
            lookup_warnings: list[dict] = []

            async with _httpx.AsyncClient(timeout=_httpx.Timeout(15.0), follow_redirects=False) as client:
                if watched_lookup_mids:
                    resolved = await asyncio.gather(*[_resolve_watched_lookup(mid) for mid in watched_lookup_mids])
                    newly_failed = 0
                    for mid, sid in resolved:
                        if sid:
                            watched_sid_to_mids.setdefault(sid, set()).add(mid)
                        else:
                            newly_failed += 1
                            m = media_info.get(mid)
                            series_name = show_title_map.get(m.show_id) if m and m.show_id else None
                            lookup_warnings.append(watched_lookup_failed_warning(mid, m, series_name=series_name))
                    if newly_failed:
                        done += newly_failed
                        failed_count += newly_failed
                        await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(processed_items=done))
                        await db.commit()
                        await raise_if_cancelled(db, job_id)

                # Every sid that will need an already-watched check below is
                # now known (watched_sid_to_mids is fully populated, including
                # anything just resolved above) - fetch Jellyfin/Emby's own
                # watched state for all of them in one batched pass instead of
                # a get_item call per sid inside _already_watched_on_server
                # (#362). Plex still checks per-item (plex.get_item), which
                # this doesn't touch.
                if conn.type in ("jellyfin", "emby") and watched_sid_to_mids:
                    client_mod = jellyfin if conn.type == "jellyfin" else emby
                    jellyfin_watched_state = await client_mod.get_items_watched_state(
                        conn.url, conn.token, list(watched_sid_to_mids.keys()),
                        user_id=conn.server_user_id, client=client,
                    )

                # (coroutine, weight) pairs - a grouped watched push counts as
                # every media row it covers once it resolves, not as 1, so
                # processed_items still sums to total at completion.
                weighted: list[tuple] = (
                    [(_push_known(client, item), 1) for item in push_items]
                    + [(_push_lookup(client, item), 1) for item in lookup_items]
                    + [(_push_watched_group(client, sid, mids), len(mids)) for sid, mids in watched_sid_to_mids.items()]
                )

                async def _weighted(coro, weight: int) -> tuple[bool, int]:
                    return await coro, weight

                for future in asyncio.as_completed([_weighted(c, w) for c, w in weighted]):
                    result, weight = await future
                    prev_done = done
                    done += weight
                    if result is True:
                        succeeded += weight
                    else:
                        failed_count += weight
                    if done // _PROGRESS_INTERVAL != prev_done // _PROGRESS_INTERVAL:
                        await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(processed_items=done))
                        await db.commit()
                        await raise_if_cancelled(db, job_id)

            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(
                status=SyncStatus.completed,
                processed_items=total,
                stats={"succeeded": succeeded, "failed": failed_count},
                warnings=lookup_warnings or None,
            ))
            await db.commit()
            print(f"Full push for connection {connection_id}: {succeeded}/{total} succeeded, {failed_count} failed"
                  f"{f', {len(lookup_warnings)} unresolved lookups' if lookup_warnings else ''}")

        except SyncCancelled:
            print(f"Full push for connection {connection_id} cancelled")
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.cancelled))
            await db.commit()

        except Exception as e:
            import traceback
            traceback.print_exc()
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed, error_message=str(e)[:900]))
            await db.commit()


async def run_heal(user_id: int, api_key: str, job_id: int | None = None):
    from models.show import Show
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        async def _update_job(**kwargs):
            if job_id is None:
                return
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(updated_at=func.now(), **kwargs))
            await db.commit()

        try:
            await _update_job(status=SyncStatus.running)
            await raise_if_cancelled(db, job_id)

            # ── Phase 1: Re-enrich items that have show linkage but missing poster ──
            coll_q = await db.execute(
                select(Media)
                .join(Collection, Collection.media_id == Media.id)
                .where(
                    Collection.user_id == user_id,
                    Media.poster_path.is_(None),
                )
            )
            items = coll_q.scalars().all()

            movies = [m for m in items if m.media_type == MediaType.movie and m.tmdb_id]
            # Episodes enriched from TVDB (see #101) have no real TMDB
            # counterpart to re-fetch — retrying would just 404 every time.
            episodes = [
                m for m in items
                if m.media_type == MediaType.episode and m.show_id and m.season_number is not None
                and m.episode_number is not None and not is_unmapped_tvdb_episode(m)
            ]

            if movies or episodes:
                print(f"Heal: {len(movies)} movies, {len(episodes)} episodes to re-enrich for user {user_id}")

                show_ids = list({m.show_id for m in episodes})
                show_tmdb_map: dict[int, int] = {}
                if show_ids:
                    shows_q = await db.execute(select(Show).where(Show.id.in_(show_ids)))
                    for s in shows_q.scalars().all():
                        if s.tmdb_id:
                            show_tmdb_map[s.id] = s.tmdb_id

                to_enrich = [(m, None) for m in movies] + [
                    (m, show_tmdb_map[m.show_id]) for m in episodes if m.show_id in show_tmdb_map
                ]
                await _update_job(total_items=len(to_enrich), processed_items=0, current_step="Re-enriching metadata")
                await batch_enrich_items(db, to_enrich, api_key=api_key, user_id=user_id)
                await db.commit()
                await _update_job(processed_items=len(to_enrich))
                print(f"Heal: re-enriched {len(to_enrich)} items for user {user_id}")
            else:
                print(f"Heal: nothing to re-enrich for user {user_id}")
                await _update_job(total_items=0, processed_items=0, current_step="Re-enriching metadata")

            await raise_if_cancelled(db, job_id)

            # ── Phase 2: Recover orphaned episodes via Jellyfin/Emby ─────────────
            # Webhook-created episodes may have show_id=None if the show wasn't in
            # the DB yet. Look them up by their source ID to re-link and enrich them.
            orphan_q = await db.execute(
                select(Media, CollectionFile, MediaServerConnection)
                .join(Collection, Collection.media_id == Media.id)
                .join(CollectionFile, CollectionFile.collection_id == Collection.id)
                .join(MediaServerConnection, MediaServerConnection.id == CollectionFile.connection_id)
                .where(
                    Collection.user_id == user_id,
                    Media.media_type == MediaType.episode,
                    Media.show_id.is_(None),
                    Media.season_number.isnot(None),
                    Media.episode_number.isnot(None),
                    CollectionFile.source.in_([CollectionSource.jellyfin, CollectionSource.emby]),
                    CollectionFile.connection_id.isnot(None),
                )
            )
            orphan_rows = orphan_q.all()

            if orphan_rows:
                await _update_job(current_step="Recovering orphaned episodes")
                recovered = 0
                seen: set[int] = set()
                for orphan_media, coll_file, conn in orphan_rows:
                    if orphan_media.id in seen:
                        continue
                    seen.add(orphan_media.id)
                    try:
                        # user_id is required here - Jellyfin's admin-only Items/{id}
                        # endpoint (no Users/ prefix) throws server-side for a
                        # non-admin token (see #179).
                        item_data = await jellyfin.get_item(conn.url, conn.token, coll_file.source_id, user_id=conn.server_user_id)
                        if not item_data:
                            continue
                        series_id = item_data.get("SeriesId")
                        if not series_id:
                            continue
                        series_data = await jellyfin.get_item(conn.url, conn.token, series_id, user_id=conn.server_user_id)
                        if not series_data:
                            continue
                        series_tmdb_raw = series_data.get("ProviderIds", {}).get("Tmdb")
                        if not series_tmdb_raw:
                            continue
                        series_tmdb_id = int(series_tmdb_raw)
                        show = await show_metadata.find_or_create_show(db, series_tmdb_id, api_key)
                        orphan_media.show_id = show.id
                        orphan_media = await enrich_media_safely(db, orphan_media, api_key=api_key, series_tmdb_id=series_tmdb_id)
                        recovered += 1
                    except Exception as e:
                        print(f"Heal: failed to recover orphan '{orphan_media.title}' (id={orphan_media.id}): {e}")
                if recovered:
                    await db.commit()
                print(f"Heal: recovered {recovered}/{len(seen)} orphaned episode(s) for user {user_id}")

            await _update_job(status=SyncStatus.completed, stats={"healed": True})
            asyncio.create_task(pre_cache_all_collected_bg())

        except SyncCancelled:
            print(f"Heal job {job_id} cancelled for user {user_id}")
            await _update_job(status=SyncStatus.cancelled)

        except Exception as e:
            print(f"Heal failed for user {user_id}: {e}")
            import traceback
            traceback.print_exc()
            await _update_job(status=SyncStatus.failed, error_message=str(e)[:900])


async def _stamp_matched_show_warnings(db: AsyncSession, user_id: int, warnings: list[dict]) -> list[dict]:
    """Auto-stamp warnings for shows that have already been TVDB-matched by this user.

    On every sync, series/episode warnings are regenerated fresh without matched state.
    This helper checks each warning title against already-matched Media rows and stamps
    matched:true + tvdb/show info so the panel renders the correct badge without requiring
    the user to re-run the match action.
    """
    from sqlalchemy import func as sa_func

    titles = set()
    for w in warnings:
        t = w.get("title") or w.get("series_name")
        if t:
            titles.add(t.lower())

    if not titles:
        return warnings

    # Find any episode Media row per matched title (show_id set, show has tvdb_id)
    matched_ep_result = await db.execute(
        select(Media, Show)
        .join(Collection, Collection.media_id == Media.id)
        .join(Show, Show.id == Media.show_id)
        .where(
            Collection.user_id == user_id,
            Media.media_type == MediaType.episode,
            Media.show_id.isnot(None),
            Show.tvdb_id.isnot(None),
            sa_func.lower(Media.tmdb_data["show_title"].astext).in_(list(titles)),
        )
        .limit(len(titles) * 5)
    )
    title_to_show: dict[str, Show] = {}
    for media, show in matched_ep_result.all():
        key = (media.tmdb_data or {}).get("show_title", "").lower()
        if key and key not in title_to_show:
            title_to_show[key] = show

    if not title_to_show:
        return warnings

    stamped = []
    for w in warnings:
        raw_title = w.get("title") or w.get("series_name") or ""
        show = title_to_show.get(raw_title.lower())
        if show and not w.get("matched"):
            stamped.append({
                **w,
                "matched": True,
                "matched_tvdb_id": show.tvdb_id,
                "matched_show_id": show.tmdb_id,
                "matched_show_title": show.title,
            })
        else:
            stamped.append(w)
    return stamped
