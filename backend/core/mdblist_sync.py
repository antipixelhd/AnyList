"""MDBList import and export jobs."""

from __future__ import annotations

import logging
from core.sync_reconciliation import add_watch_event
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from dateutil import parser as dt_parser
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import (
    catalog_import,
    db_queries,
    mdblist_payloads,
    settings_store,
    trakt_sync,
)
from core import mdblist as mdblist_client
from core.catalog_import import (
    get_or_create_movie_media as _get_or_create_movie_media,
)
from core.catalog_import import (
    get_or_create_show as _get_or_create_show,
)
from core.cloud_reconciliation import require_cloud_reconciliation
from core.enrichment import create_media_safely, enrich_media
from core.rewatch import record_rewatch_progress
from core.watch_dates import inferred_watch_datetime, reconcile_inferred_watch_date
from core.watch_dedup import get_dedup_window_minutes
from db import engine
from models.base import MediaType
from models.collection import Collection
from models.events import WatchEvent
from models.lists import List as ListModel
from models.lists import ListItem
from models.media import Media
from models.ratings import Rating, RatingChanges
from models.show import Show
from models.sync import SyncJob, SyncStatus
from models.users import UserSettings

logger = logging.getLogger(__name__)


WATCHLIST_SLUG = "__watchlist__"


WATCH_DEDUP_WINDOW = timedelta(minutes=10)


def _utc_naive_optional(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = dt_parser.isoparse(value)
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _entry_data(kind: str, entry: dict[str, Any]) -> dict[str, Any]:
    singular = {"movies": "movie", "shows": "show", "seasons": "season", "episodes": "episode"}[kind]
    nested = entry.get(singular)
    return nested if isinstance(nested, dict) else entry


def _describe_not_found(entry: dict[str, Any], show_titles: dict[int, str] | None = None) -> str:
    """One-line human summary of an item MDBList reported as not found, for the
    push job's WARNING log (#340). Defensive about MDBList's echo shape - it
    carries at least the ids we sent, sometimes a title and season/episode.

    An episode entry with no id of its own instead carries the show tmdb
    id(s) core.mdblist._push recovered locally (see _index_episode_shows) in
    "_candidate_show_tmdb_ids" - show_titles resolves those to a name where
    known, so "episode no-id S6" becomes "episode no-id S6 (show: Poirot)"
    or, when more than one show shares that season number in this batch,
    "(possibly: Poirot, Marple)" (#368)."""
    kind = entry.get("kind") or "item"
    item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
    ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
    tmdb = ids.get("tmdb") or item.get("tmdb") or item.get("tmdb_id")
    imdb = ids.get("imdb") or item.get("imdb")
    ref = f"tmdb:{tmdb}" if tmdb is not None else (f"imdb:{imdb}" if imdb else "no-id")

    sxe = ""
    seasons = item.get("seasons")
    if isinstance(seasons, list) and seasons and isinstance(seasons[0], dict):
        s0 = seasons[0]
        sxe = f" S{s0.get('number')}"
        eps = s0.get("episodes")
        if isinstance(eps, list) and eps and isinstance(eps[0], dict) and eps[0].get("number") is not None:
            sxe += f"E{eps[0]['number']}"
    elif item.get("season") is not None:
        sxe = f" S{item['season']}"
        if item.get("episode") is not None:
            sxe += f"E{item['episode']}"

    show_ref = ""
    candidates = item.get("_candidate_show_tmdb_ids")
    if ref == "no-id" and isinstance(candidates, list) and candidates:
        names = [(show_titles or {}).get(c) or f"tmdb:{c}" for c in candidates]
        label = "show" if len(names) == 1 else "possibly"
        show_ref = f" ({label}: {', '.join(names)})"

    title = item.get("title")
    return f"{kind} {ref}{sxe}" + (f' "{title}"' if title else "") + show_ref


def _tmdb_id(data: dict[str, Any]) -> int | None:
    ids = data.get("ids")
    ids = ids if isinstance(ids, dict) else {}
    return _integer(ids.get("tmdb") or data.get("tmdb_id"))


async def _resolve_external_tmdb_id(
    data: dict[str, Any],
    media_type: str,
    api_key: str | None,
    cache: dict[tuple[str, str], int | None],
) -> int | None:
    direct_id = _tmdb_id(data)
    if direct_id:
        return direct_id

    ids = data.get("ids")
    ids = ids if isinstance(ids, dict) else {}
    from core import tmdb

    for provider, external_source in (("imdb", "imdb_id"), ("tvdb", "tvdb_id")):
        external_id = ids.get(provider) or data.get(f"{provider}_id")
        if external_id is None:
            continue
        cache_key = (external_source, str(external_id))
        if cache_key in cache:
            return cache[cache_key]
        try:
            result = await tmdb.find_by_external_id(
                str(external_id),
                external_source,
                api_key=api_key,
            )
            result_key = "movie_results" if media_type == "movie" else "tv_results"
            matches = result.get(result_key) or []
            resolved = _integer(matches[0].get("id")) if matches else None
        except Exception as exc:
            logger.warning(
                "Could not resolve MDBList %s=%s through TMDB: %s",
                provider,
                external_id,
                exc,
            )
            resolved = None
        cache[cache_key] = resolved
        if resolved:
            return resolved
    return None


def _episode_identity(entry: dict[str, Any]) -> tuple[int | None, int | None, int | None, str]:
    episode = _entry_data("episodes", entry)
    show_data = entry.get("show") or episode.get("show") or {}
    show_data = show_data if isinstance(show_data, dict) else {}
    show_tmdb_id = _tmdb_id(show_data)
    ids = episode.get("ids") if isinstance(episode.get("ids"), dict) else {}
    show_tmdb_id = show_tmdb_id or _integer(ids.get("show_tmdb") or episode.get("show_tmdb_id"))

    season = episode.get("season", entry.get("season"))
    if isinstance(season, dict):
        season = season.get("number")
    episode_number = episode.get("number", episode.get("episode", entry.get("episode")))
    title = str(episode.get("title") or episode.get("name") or "")
    return show_tmdb_id, _integer(season), _integer(episode_number), title


def _season_identity(
    entry: dict[str, Any],
) -> tuple[dict[str, Any], int | None]:
    season = _entry_data("seasons", entry)
    show_data = entry.get("show") or season.get("show") or {}
    show_data = show_data if isinstance(show_data, dict) else {}
    number = season.get("number", entry.get("number"))
    return show_data, _integer(number)


async def _get_or_create_series_media(
    db: AsyncSession,
    tmdb_id: int,
    title: str,
    api_key: str | None,
) -> Media | None:
    result = await db.execute(
        select(Media).where(Media.tmdb_id == tmdb_id, Media.media_type == MediaType.series)
    )
    media = result.scalars().first()
    if media:
        return media

    from core import tmdb

    try:
        data = await tmdb.get_show(tmdb_id, api_key=api_key)
        media, _created = await create_media_safely(
            db, tmdb_id, MediaType.series, title=data.get("name") or title
        )
        await enrich_media(media, api_key=api_key)
        return media
    except Exception as exc:
        logger.warning("Could not fetch MDBList show tmdb=%s: %s", tmdb_id, exc)
        return None


async def _resolve_media(
    db: AsyncSession,
    kind: str,
    entry: dict[str, Any],
    api_key: str | None,
    external_cache: dict[tuple[str, str], int | None],
    *,
    notify_unmatched: bool = True,
) -> Media | None:
    from core.provider_matching import provider_override, record_unmatched_import
    user_id = external_cache.get(("__media_tracker__", "user_id"))
    if user_id is not None:
        override, ignored, _external_key, _external_title = await provider_override(
            db, user_id=user_id, provider="mdblist", kind=kind, entry=entry
        )
        if override or ignored:
            return override
    data = _entry_data(kind, entry)
    title = str(data.get("title") or data.get("name") or "")
    media = None
    if kind == "movies":
        tmdb_id = await _resolve_external_tmdb_id(data, "movie", api_key, external_cache)
        media = await _get_or_create_movie_media(db, tmdb_id, title, api_key) if tmdb_id else None
    elif kind == "shows":
        tmdb_id = await _resolve_external_tmdb_id(data, "tv", api_key, external_cache)
        media = await _get_or_create_series_media(db, tmdb_id, title, api_key) if tmdb_id else None
    elif kind == "episodes":
        show_tmdb_id, season, episode, _ = _episode_identity(entry)
        if show_tmdb_id is None:
            episode_data = _entry_data("episodes", entry)
            show_data = entry.get("show") or episode_data.get("show") or {}
            if isinstance(show_data, dict):
                show_tmdb_id = await _resolve_external_tmdb_id(
                    show_data, "tv", api_key, external_cache
                )
        if show_tmdb_id is not None and season is not None and episode is not None:
            show = await _get_or_create_show(db, show_tmdb_id, "", api_key)
            if show:
                media = await catalog_import.get_or_create_episode_media(
                    db, show.id, show_tmdb_id, season, episode, api_key
                )
    if media is None and user_id is not None:
        await record_unmatched_import(
            db, user_id=user_id, provider="mdblist", kind=kind,
            entry=entry, notify=notify_unmatched,
        )
    return media


async def _import_watched(
    db: AsyncSession,
    user_id: int,
    payload: dict[str, Any],
    api_key: str | None,
    external_cache: dict[tuple[str, str], int | None],
    stats: dict[str, int],
) -> set[int]:
    existing_result = await db.execute(
        select(WatchEvent.media_id, WatchEvent.watched_at).where(
            WatchEvent.user_id == user_id,
            WatchEvent.completed.is_(True),
        )
    )
    existing: dict[int, list[datetime]] = defaultdict(list)
    for media_id, watched_at in existing_result.all():
        existing[media_id].append(watched_at)
    changed: set[int] = set()

    # The user's configured duplicate-watch window (#390) only ever widens
    # this beyond its tuned default, never narrows it - see WATCH_DEDUP_WINDOW.
    dedup_window = max(WATCH_DEDUP_WINDOW, timedelta(minutes=await get_dedup_window_minutes(db, user_id)))

    # MDBList's /sync/watched "shows" entries are rollup wrappers (a show's
    # own last_watched_at just mirrors its most recently watched episode) —
    # they carry no per-episode data of their own. Importing them as watch
    # events creates a spurious series-level WatchEvent alongside the real
    # episode-level one for every watched show.
    for kind in ("movies", "episodes"):
        for entry in payload.get(kind, []):
            try:
                async with db.begin_nested():
                    media = await _resolve_media(
                        db, kind, entry, api_key, external_cache,
                        notify_unmatched=False,
                    )
                    if not media:
                        stats["skipped"] += 1
                        continue
                    watched_at = _utc_naive_optional(entry.get("watched_at") or entry.get("last_watched_at"))
                    from core.sync_reconciliation import collect_watch
                    collect_watch(media.id, watched_at)
                    if watched_at is not None and await reconcile_inferred_watch_date(
                        db, user_id, media.id, watched_at,
                    ):
                        stats["skipped"] += 1
                        continue
                    if watched_at is None and existing.get(media.id):
                        stats["skipped"] += 1
                        continue
                    if any(
                        watched_at is not None and existing_at is not None
                        and abs(watched_at - existing_at) <= dedup_window
                        for existing_at in existing.get(media.id, [])
                    ):
                        stats["skipped"] += 1
                        continue
                    event = WatchEvent(
                        user_id=user_id,
                        media_id=media.id,
                        watched_at=watched_at or inferred_watch_datetime(),
                        date_inferred=watched_at is None,
                        completed=True,
                        play_count=max(_integer(entry.get("plays")) or 1, 1),
                    )
                    add_watch_event(db, event)
                    await db.flush()
                    await record_rewatch_progress(db, user_id, media.id, event.id)
                    existing[media.id].append(event.watched_at)
                    changed.add(media.id)
                    stats["watched"] += 1
            except Exception as exc:
                logger.warning("Error importing MDBList %s watch item: %s", kind, exc)
                stats["errors"] += 1

    stats["skipped"] += len(payload.get("seasons", [])) + len(payload.get("shows", []))
    return changed


async def _import_ratings(
    db: AsyncSession,
    user_id: int,
    payload: dict[str, Any],
    api_key: str | None,
    external_cache: dict[tuple[str, str], int | None],
    stats: dict[str, int],
) -> RatingChanges:
    ratings_result = await db.execute(
        select(Rating).where(
            Rating.user_id == user_id,
            Rating.episode_order.is_(None),
        )
    )
    existing = {
        (rating.media_id, rating.season_number): rating
        for rating in ratings_result.scalars().all()
    }
    changed: RatingChanges = {}

    for kind in ("movies", "shows", "seasons", "episodes"):
        for entry in payload.get(kind, []):
            rating_value = entry.get("rating")
            try:
                rating = float(rating_value)
            except (TypeError, ValueError):
                stats["skipped"] += 1
                continue
            try:
                async with db.begin_nested():
                    season_number: int | None = None
                    if kind == "seasons":
                        from core.provider_matching import (
                            provider_override,
                            record_unmatched_import,
                        )
                        media, ignored, _external_key, _external_title = await provider_override(
                            db, user_id=user_id, provider="mdblist", kind=kind, entry=entry
                        )
                        show_data, season_number = _season_identity(entry)
                        if media is None and not ignored:
                            show_tmdb_id = await _resolve_external_tmdb_id(
                                show_data,
                                "tv",
                                api_key,
                                external_cache,
                            )
                            media = (
                                await _get_or_create_series_media(
                                    db,
                                    show_tmdb_id,
                                    str(show_data.get("title") or ""),
                                    api_key,
                                )
                                if show_tmdb_id and season_number is not None
                                else None
                            )
                            if media is None:
                                await record_unmatched_import(db, user_id=user_id, provider="mdblist", kind=kind, entry=entry)
                    else:
                        media = await _resolve_media(
                            db,
                            kind,
                            entry,
                            api_key,
                            external_cache,
                        )
                    if not media:
                        stats["skipped"] += 1
                        continue

                    from core.cloud_rating_reconciliation import reconcile_cloud_rating
                    outcome = await reconcile_cloud_rating(
                        db,
                        provider="mdblist",
                        user_id=user_id,
                        media=media,
                        season_number=season_number,
                        remote_score=rating,
                        remote_rated_at=_utc_naive_optional(entry.get("rated_at")),
                        existing=existing,
                        changed=changed,
                    )
                    if outcome == "applied":
                        stats["ratings"] += 1
                    elif outcome == "conflict":
                        stats["rating_conflicts"] += 1
                    else:
                        stats["skipped"] += 1
            except Exception as exc:
                logger.warning("Error importing MDBList %s rating: %s", kind, exc)
                stats["errors"] += 1

    return changed


async def _import_watchlist(
    db: AsyncSession,
    user_id: int,
    payload: dict[str, Any],
    api_key: str | None,
    external_cache: dict[tuple[str, str], int | None],
    stats: dict[str, int],
) -> None:
    list_result = await db.execute(
        select(ListModel).where(
            ListModel.user_id == user_id,
            ListModel.mdblist_slug == WATCHLIST_SLUG,
        )
    )
    watchlist = list_result.scalar_one_or_none()
    if not watchlist:
        watchlist = ListModel(
            user_id=user_id,
            name="MDBList - Watchlist",
            mdblist_slug=WATCHLIST_SLUG,
        )
        db.add(watchlist)
        await db.flush()
        stats["lists"] += 1

    existing_result = await db.execute(
        select(ListItem.media_id).where(ListItem.list_id == watchlist.id)
    )
    existing = {row[0] for row in existing_result.all()}
    remote_ids: set[int] = set()

    for kind in ("movies", "shows"):
        for entry in payload.get(kind, []):
            try:
                async with db.begin_nested():
                    media = await _resolve_media(db, kind, entry, api_key, external_cache)
                    if not media:
                        stats["skipped"] += 1
                        continue
                    remote_ids.add(media.id)
                    if media.id not in existing:
                        db.add(ListItem(list_id=watchlist.id, media_id=media.id))
                        existing.add(media.id)
                        stats["watchlist_added"] += 1
            except Exception as exc:
                logger.warning("Error importing MDBList watchlist %s: %s", kind, exc)
                stats["errors"] += 1

    stale = existing - remote_ids
    if stale:
        await db.execute(
            delete(ListItem).where(
                ListItem.list_id == watchlist.id,
                ListItem.media_id.in_(stale),
            )
        )
        stats["watchlist_removed"] += len(stale)


async def run_mdblist_sync(user_id: int, job_id: int) -> None:
    from core.sync_jobs import SyncCancelled, raise_if_cancelled, short_error
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as db:
        try:
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.running, current_step="Pulling from MDBList")
            )
            await db.commit()

            settings_result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == user_id)
            )
            settings = settings_result.scalar_one_or_none()
            if not settings or not settings.mdblist_api_key:
                raise RuntimeError("MDBList API key is not configured")

            requests = []
            labels = []
            if settings.mdblist_sync_watched:
                labels.append("watched")
                requests.append(mdblist_client.get_watched(settings.mdblist_api_key))
            if settings.mdblist_sync_ratings:
                labels.append("ratings")
                requests.append(mdblist_client.get_ratings(settings.mdblist_api_key))
            if settings.mdblist_sync_watchlist:
                labels.append("watchlist")
                requests.append(mdblist_client.get_watchlist(settings.mdblist_api_key))
            if settings.mdblist_sync_dropped:
                labels.append("dropped")
                requests.append(mdblist_client.get_dropped(settings.mdblist_api_key))

            import asyncio

            responses = await asyncio.gather(*requests)
            snapshots = dict(zip(labels, responses, strict=True))
            total_items = sum(
                len(values)
                for snapshot in snapshots.values()
                for values in snapshot.values()
                if isinstance(values, list)
            )
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(total_items=total_items)
            )
            await db.commit()

            tmdb_key = await settings_store.get_effective_tmdb_key(db, settings)
            stats = {
                "watched": 0,
                "ratings": 0,
                "rating_conflicts": 0,
                "lists": 0,
                "watchlist_added": 0,
                "watchlist_removed": 0,
                "skipped": 0,
                "errors": 0,
            }
            new_watched: set[int] = set()
            new_ratings: RatingChanges = {}
            external_cache: dict[tuple[str, str], int | None] = {
                ("__media_tracker__", "user_id"): user_id,
            }

            if "watched" in snapshots:
                new_watched = await _import_watched(
                    db, user_id, snapshots["watched"], tmdb_key, external_cache, stats
                )
                await raise_if_cancelled(db, job_id)
            if "ratings" in snapshots:
                new_ratings = await _import_ratings(
                    db, user_id, snapshots["ratings"], tmdb_key, external_cache, stats
                )
                await raise_if_cancelled(db, job_id)
            if "watchlist" in snapshots:
                await _import_watchlist(
                    db, user_id, snapshots["watchlist"], tmdb_key, external_cache, stats
                )
            if "dropped" in snapshots:
                stats["dropped"] = await trakt_sync._apply_dropped_shows_import(
                    db, user_id, snapshots["dropped"].get("shows", [])
                )
            await db.commit()

            # Import and reconcile before exporting only approved changed fields.
            from core.tracking_import import import_tracking_history
            from models.tracking import CloudBaseline
            first_import = await db.scalar(select(CloudBaseline.id).where(
                CloudBaseline.user_id == user_id, CloudBaseline.provider == "mdblist")) is None
            stats["tracked_entries"] = await import_tracking_history(db, user_id, initial_import=first_import)
            from core.cloud_history_reconciliation import reconcile_cloud_watch_events
            accepted_watched: set[int] = set()
            history_reconciliation = await reconcile_cloud_watch_events(
                db, user_id=user_id, provider="mdblist", new_media_ids=new_watched,
                applied_media_ids=accepted_watched,
            )
            stats["tracking_updates"] = history_reconciliation["applied"]
            stats["tracking_conflicts"] = history_reconciliation["conflicts"]
            from core.cloud_reconciliation import record_cloud_import
            await record_cloud_import(db, user_id, "mdblist", stats)
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.completed,
                    processed_items=total_items,
                    errors=stats["errors"],
                    stats=stats,
                )
            )
            await db.commit()
            from core.pull_propagation import propagate_cloud_pull
            await propagate_cloud_pull(
                db, user_id=user_id, provider="mdblist", watched_ids=accepted_watched,
                ratings=new_ratings, complete=not stats["errors"],
            )
        except SyncCancelled:
            logger.info("MDBList pull job %s cancelled", job_id)
            await db.rollback()
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.cancelled)
            )
            await db.commit()
        except Exception as exc:
            logger.exception("MDBList pull job %s failed", job_id)
            await db.rollback()
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.failed,
                    error_message=short_error(exc),
                )
            )
            await db.commit()


async def _load_payload_media(db: AsyncSession, media_ids: set[int]) -> dict[int, Media]:
    if not media_ids:
        return {}


    media = await db_queries.select_in_chunks(
        db,
        lambda chunk: select(Media).where(Media.id.in_(chunk)),
        list(media_ids),
    )
    return {item.id: item for item in media}


async def _load_shows_for_episodes(db: AsyncSession, media_by_id: dict[int, Media]) -> dict[int, Show]:
    """Load parent Show rows for every episode Media, keyed by Show.id (== Media.show_id)."""
    show_ids = {m.show_id for m in media_by_id.values() if m.media_type == MediaType.episode and m.show_id}
    if not show_ids:
        return {}


    shows = await db_queries.select_in_chunks(
        db,
        lambda chunk: select(Show).where(Show.id.in_(chunk)),
        list(show_ids),
    )
    return {show.id: show for show in shows}


async def run_mdblist_push(user_id: int, job_id: int) -> None:
    from core.sync_jobs import SyncCancelled, raise_if_cancelled, short_error
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as db:
        try:
            await require_cloud_reconciliation(db, user_id, "mdblist")
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.running)
            )
            await db.commit()

            settings_result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == user_id)
            )
            settings = settings_result.scalar_one_or_none()
            if not settings or not settings.mdblist_api_key:
                raise RuntimeError("MDBList API key is not configured")

            # Reconcile dropped shows MDBList is missing - the one-shot push at
            # drop time is best-effort and nothing else ever retried it (#329).
            dropped_to_push: list[int] = []
            if settings.mdblist_push_dropped:
                try:
                    local_dropped = await trakt_sync._local_dropped_show_tmdb_ids(db, settings)
                    if local_dropped:
                        remote = await mdblist_client.get_dropped(settings.mdblist_api_key)
                        remote_tmdb = {
                            (it.get("show") or {}).get("ids", {}).get("tmdb")
                            for it in remote.get("shows", [])
                        }
                        remote_tmdb.discard(None)
                        dropped_to_push = sorted(local_dropped - remote_tmdb)
                except Exception as exc:
                    logger.warning("MDBList push job %s: could not reconcile dropped shows: %s", job_id, exc)

            watched_rows: list[tuple[int, datetime]] = []
            rating_rows: list[tuple[int, int | None, float, datetime | None]] = []
            watchlist_ids: set[int] = set()
            collected_rows: list[tuple[int, datetime]] = []

            if settings.mdblist_push_watched:
                watched_result = await db.execute(
                    select(WatchEvent.media_id, func.max(WatchEvent.watched_at))
                    .where(WatchEvent.user_id == user_id, WatchEvent.completed.is_(True))
                    .group_by(WatchEvent.media_id)
                )
                watched_rows = list(watched_result.all())
            if settings.mdblist_push_collection:
                collected_result = await db.execute(
                    select(Collection.media_id, Collection.added_at).where(Collection.user_id == user_id)
                )
                collected_rows = list(collected_result.all())
            if settings.mdblist_push_ratings:
                ratings_result = await db.execute(
                    select(Rating.media_id, Rating.season_number, Rating.rating, Rating.rated_at).where(
                        Rating.user_id == user_id,
                        Rating.rating.isnot(None),
                        Rating.episode_order.is_(None),
                    )
                )
                rating_rows = [
                    (media_id, season_number, float(rating), rated_at)
                    for media_id, season_number, rating, rated_at in ratings_result.all()
                ]
            if settings.mdblist_push_watchlist:
                watchlist_result = await db.execute(
                    select(ListModel).where(
                        ListModel.user_id == user_id,
                        ListModel.mdblist_slug == WATCHLIST_SLUG,
                    )
                )
                watchlist = watchlist_result.scalar_one_or_none()
                if watchlist:
                    item_result = await db.execute(
                        select(ListItem.media_id).where(ListItem.list_id == watchlist.id)
                    )
                    watchlist_ids = {row[0] for row in item_result.all()}

            all_ids = (
                {row[0] for row in watched_rows}
                | {row[0] for row in rating_rows}
                | watchlist_ids
                | {row[0] for row in collected_rows}
            )
            media_by_id = await _load_payload_media(db, all_ids)
            shows_by_id = await _load_shows_for_episodes(db, media_by_id)
            watched_payload = mdblist_payloads.empty_payload()
            ratings_payload = mdblist_payloads.empty_payload()
            watchlist_payload = mdblist_payloads.empty_payload()
            collection_payload = mdblist_payloads.empty_payload()

            for media_id, watched_at in watched_rows:
                media = media_by_id.get(media_id)
                item = (
                    mdblist_payloads.payload_item(media, show=shows_by_id.get(media.show_id), watched_at=watched_at)
                    if media
                    else None
                )
                if item:
                    watched_payload[item[0]].append(item[1])
            for media_id, added_at in collected_rows:
                media = media_by_id.get(media_id)
                item = (
                    mdblist_payloads.payload_item(media, show=shows_by_id.get(media.show_id), collected_at=added_at)
                    if media
                    else None
                )
                if item:
                    collection_payload[item[0]].append(item[1])
            for media_id, season_number, rating, rated_at in rating_rows:
                media = media_by_id.get(media_id)
                item = (
                    mdblist_payloads.payload_item(
                        media,
                        show=shows_by_id.get(media.show_id),
                        rating=rating,
                        rated_at=rated_at,
                        season_number=season_number,
                    )
                    if media
                    else None
                )
                if item:
                    ratings_payload[item[0]].append(item[1])
            for media_id in watchlist_ids:
                media = media_by_id.get(media_id)
                item = mdblist_payloads.payload_item(media) if media else None
                if item and item[0] in ("movies", "shows"):
                    watchlist_payload[item[0]].append(item[1])

            watched_payload["shows"] = mdblist_payloads.merge_show_entries(watched_payload["shows"])
            collection_payload["shows"] = mdblist_payloads.merge_show_entries(collection_payload["shows"])

            total_items = sum(
                mdblist_client._count_leaf_items(payload)
                for payload in (watched_payload, ratings_payload, watchlist_payload, collection_payload)
            ) + len(dropped_to_push)
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(total_items=total_items)
            )
            await db.commit()

            print(
                f"MDBList push job {job_id}: queued "
                f"{len(watched_rows)} watched, {len(rating_rows)} ratings, "
                f"{len(watchlist_ids)} watchlist, {len(collected_rows)} collection, "
                f"{len(dropped_to_push)} dropped "
                f"({total_items} payload entries after merging by show)."
            )

            processed_so_far = 0

            async def _report_progress(batch_count: int) -> None:
                nonlocal processed_so_far
                processed_so_far += batch_count
                await db.execute(
                    update(SyncJob).where(SyncJob.id == job_id).values(processed_items=processed_so_far)
                )
                await db.commit()
                await raise_if_cancelled(db, job_id)

            results: dict[str, Any] = {}
            if settings.mdblist_push_watched:
                results["watched"] = await mdblist_client.push_watched(
                    settings.mdblist_api_key, watched_payload, on_batch=_report_progress
                )
            if settings.mdblist_push_ratings:
                ratings_payload["shows"] = mdblist_payloads.merge_show_entries(ratings_payload["shows"])
                results["ratings"] = await mdblist_client.push_ratings(
                    settings.mdblist_api_key, ratings_payload, on_batch=_report_progress
                )
            if settings.mdblist_push_watchlist:
                results["watchlist"] = await mdblist_client.push_watchlist(
                    settings.mdblist_api_key, watchlist_payload, on_batch=_report_progress
                )
            if settings.mdblist_push_collection:
                results["collection"] = await mdblist_client.push_collection(
                    settings.mdblist_api_key, collection_payload, on_batch=_report_progress
                )
            if dropped_to_push:
                await mdblist_client.push_dropped_batch(
                    settings.mdblist_api_key, dropped_to_push, mdblist_payloads.iso_utc(None)
                )
                results["dropped"] = {"submitted": len(dropped_to_push), "not_found": 0, "batches": 1}

            submitted = sum(result["submitted"] for result in results.values())
            not_found = sum(result["not_found"] for result in results.values())

            # MDBList echoes the items it couldn't match back in each response.
            # Pull them out of the per-target results (keeps SyncJob.stats
            # lean) so they can be logged and stored once, flat, with the
            # target name attached (#340).
            not_found_items: list[dict[str, Any]] = []
            for name, r in results.items():
                for entry in r.pop("not_found_items", []) or []:
                    not_found_items.append({"target": name, **entry})

            breakdown = ", ".join(
                f"{name}: {r['submitted']} submitted"
                + (f" ({r['not_found']} not found on MDBList)" if r["not_found"] else "")
                + f" in {r['batches']} request(s)"
                for name, r in results.items()
            ) or "nothing enabled"
            print(
                f"MDBList push job {job_id} completed. {breakdown}. "
                f"Total: {submitted} submitted, {not_found} not found."
            )
            if not_found_items:
                # Resolve whatever show tmdb ids _index_episode_shows recovered
                # for the id-less episode entries above to a title, so the log
                # reads "(show: Poirot)" instead of a second tmdb id (#368).
                candidate_ids = {
                    tmdb_id
                    for it in not_found_items
                    for tmdb_id in (it.get("item") or {}).get("_candidate_show_tmdb_ids") or []
                }
                show_titles: dict[int, str] = {}
                if candidate_ids:
                    rows = await db.execute(
                        select(Show.tmdb_id, Show.title).where(Show.tmdb_id.in_(candidate_ids))
                    )
                    show_titles = dict(rows.all())
                shown = "; ".join(_describe_not_found(it, show_titles) for it in not_found_items[:50])
                more = f" (+{not_found - len(not_found_items[:50])} more)" if not_found > 50 else ""
                logger.warning(
                    "MDBList push job %s: %d item(s) not found on MDBList: %s%s",
                    job_id, not_found, shown, more,
                )

            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.completed,
                    processed_items=submitted,
                    errors=not_found,
                    stats={
                        "submitted": submitted,
                        "not_found": not_found,
                        "not_found_items": not_found_items,
                        "targets": results,
                    },
                )
            )
            await db.commit()
        except SyncCancelled:
            logger.info("MDBList push job %s cancelled", job_id)
            await db.rollback()
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.cancelled,
                    processed_items=processed_so_far,
                )
            )
            await db.commit()
        except Exception as exc:
            logger.exception("MDBList push job %s failed", job_id)
            await db.rollback()
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.failed,
                    error_message=short_error(exc),
                )
            )
            await db.commit()
