"""Trakt import and export jobs."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Coroutine

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from core import catalog_import, outbound_sync, settings_store, trakt_auth
from core import trakt as trakt_client
from core.catalog_import import (
    get_or_create_movie_media as _get_or_create_movie_media,
)
from core.catalog_import import (
    get_or_create_series_media as _get_or_create_series_media,
)
from core.catalog_import import (
    get_or_create_show as _get_or_create_show,
)
from core.cloud_reconciliation import require_cloud_reconciliation
from core.enrichment import create_media_safely, is_unmapped_tvdb_episode
from core.import_ratings import apply_imported_rating as _apply_imported_rating
from core.import_ratings import optional_imported_rated_at
from core.rewatch import record_rewatch_progress
from core.trakt_export import TraktExportData
from core.watch_dates import inferred_watch_datetime, reconcile_inferred_watch_date
from core.watch_dedup import (
    DEFAULT_DEDUP_WINDOW_MINUTES,
    dedup_window_from_settings,
    is_duplicate_watch_time,
    load_existing_watch_times,
)
from db import engine
from models.base import MediaType
from models.collection import Collection
from models.comments import Comment
from models.events import WatchEvent
from models.global_settings import GlobalSettings
from models.lists import List as ListModel
from models.lists import ListItem
from models.media import Media
from models.ratings import Rating, RatingChanges
from models.show import Show
from models.sync import SyncJob, SyncStatus
from models.users import UserSettings

logger = logging.getLogger(__name__)


TRAKT_HISTORY_OVERLAP = timedelta(minutes=5)


TRAKT_HISTORY_PUSH_BATCH_SIZE = 100


TRAKT_RATINGS_PUSH_BATCH_SIZE = 100


TRAKT_PUSH_REQUEST_GAP = 1.0


def _normalize_history_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def _history_window(
    cursor: datetime | None,
    full_resync: bool,
    cutoff: datetime,
) -> tuple[datetime | None, datetime]:
    start_at = None if full_resync or cursor is None else cursor - TRAKT_HISTORY_OVERLAP
    return start_at, cutoff


def _remote_history_times(
    movies: list[dict],
    episodes: list[dict],
) -> dict[tuple, list[datetime | None]]:
    """Group remote play timestamps by item identity (no timestamp in the key),
    so the push dedup can match a local watch against a remote play of the same
    item that lands a few seconds off - Trakt records its own receipt time, not
    the media server's, so an exact-timestamp match misses and the event is
    re-sent every push (#327)."""
    out: dict[tuple, list[datetime | None]] = {}
    for item in movies:
        tmdb_id = item.get("movie", {}).get("ids", {}).get("tmdb")
        if tmdb_id:
            out.setdefault(("movie", int(tmdb_id)), []).append(
                _normalize_history_time(_parse_trakt_datetime(item.get("watched_at")))
            )
    for item in episodes:
        show_tmdb_id = item.get("show", {}).get("ids", {}).get("tmdb")
        episode = item.get("episode", {})
        season = episode.get("season")
        number = episode.get("number")
        if show_tmdb_id and season is not None and number is not None:
            out.setdefault(("episode", int(show_tmdb_id), int(season), int(number)), []).append(
                _normalize_history_time(_parse_trakt_datetime(item.get("watched_at")))
            )
    return out


def _history_play_seen(
    remote_times: dict[tuple, list[datetime | None]],
    identity: tuple,
    local_at: datetime | None,
) -> bool:
    """True if Trakt already has a play of `identity` that should count as the
    same watch as a local one at `local_at`. An unknown-dated local watch
    matches any remote play of the same item; a dated one matches a remote play
    within TRAKT_HISTORY_OVERLAP (so second-level clock drift doesn't re-send it,
    while a genuine rewatch hours/days later still gets pushed)."""
    seen = remote_times.get(identity)
    if not seen:
        return False
    if local_at is None:
        return True
    tolerance = TRAKT_HISTORY_OVERLAP.total_seconds()
    return any(
        remote_at is not None and abs((remote_at - local_at).total_seconds()) <= tolerance
        for remote_at in seen
    )


def _remote_collection_keys(collection: dict) -> set[tuple]:
    """Identity keys for everything already in the user's Trakt collection, so
    the outbound collection push can skip them instead of re-POSTing the whole
    library every run (#327)."""
    keys: set[tuple] = set()
    for item in collection.get("movies", []):
        tmdb_id = (item.get("movie", {}).get("ids", {}) or {}).get("tmdb")
        if tmdb_id is not None:
            keys.add(("movie", int(tmdb_id)))
    for item in collection.get("shows", []):
        show_tmdb_id = (item.get("show", {}).get("ids", {}) or {}).get("tmdb")
        if show_tmdb_id is None:
            continue
        for season in item.get("seasons", []) or []:
            snum = season.get("number")
            if snum is None:
                continue
            for episode in season.get("episodes", []) or []:
                enum = episode.get("number")
                if enum is not None:
                    keys.add(("episode", int(show_tmdb_id), int(snum), int(enum)))
    return keys


_TRAKT_UNKNOWN_DATE_EPOCH = datetime(1970, 1, 1)


def _parse_trakt_datetime(value: str | None) -> datetime | None:
    if not value or value == "unknown":
        return None
    from dateutil import parser as dt_parser
    dt = dt_parser.isoparse(value)
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    # Trakt doesn't preserve a literal "unknown" marker on read — a history
    # entry submitted with watched_at="unknown" is silently stored (and
    # returned) as the Unix epoch. Treat that the same as unknown, rather
    # than importing/dedup-keying it as a real (wrong) 1970-01-01 watch date.
    if dt == _TRAKT_UNKNOWN_DATE_EPOCH:
        return None
    return dt


async def _resolve_trakt_title(
    db,
    user_id: int,
    kind: str,
    entry: dict,
    api_key: str | None,
    *,
    notify_unmatched: bool = True,
) -> Media | None:
    """Resolve a top-level Trakt movie/show, honoring durable user matches."""
    from core.provider_matching import provider_override, record_unmatched_import
    plural = "movies" if kind == "movie" else "shows"
    media, ignored, _key, _title = await provider_override(
        db, user_id=user_id, provider="trakt", kind=plural, entry=entry
    )
    if media or ignored:
        return media
    data = entry.get(kind, {})
    tmdb_id = data.get("ids", {}).get("tmdb")
    if tmdb_id:
        creator = _get_or_create_movie_media if kind == "movie" else _get_or_create_series_media
        media = await creator(db, tmdb_id, data.get("title", ""), api_key)
    if media is None:
        await record_unmatched_import(
            db, user_id=user_id, provider="trakt", kind=plural,
            entry=entry, notify=notify_unmatched,
        )
    return media


async def _get_or_create_person_media(db: AsyncSession, tmdb_id: int, name: str, api_key: str | None) -> Media | None:
    result = await db.execute(
        select(Media).where(Media.tmdb_id == tmdb_id, Media.media_type == MediaType.person)
    )
    media = result.scalars().first()
    if media:
        return media
    from core import tmdb
    try:
        data = await tmdb.get_person(tmdb_id, api_key=api_key)
        media, _created = await create_media_safely(
            db,
            tmdb_id,
            MediaType.person,
            title=data.get("name") or name or "Unknown",
            poster_path=tmdb.poster_url(data.get("profile_path"), size="w185"),
            overview=data.get("biography"),
        )
        return media
    except Exception as exc:
        logger.warning("Could not fetch person tmdb=%s: %s", tmdb_id, exc)
        return None


class LiveTraktSource:
    """Fetches watched history, ratings, and lists from the live Trakt API."""

    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id
        self.access_token = access_token

    async def get_history_movies(self, start_at: datetime | None, end_at: datetime):
        return await trakt_client.get_history_movies(self.client_id, self.access_token, start_at=start_at, end_at=end_at)

    async def get_history_episodes(self, start_at: datetime | None, end_at: datetime):
        return await trakt_client.get_history_episodes(self.client_id, self.access_token, start_at=start_at, end_at=end_at)

    async def get_ratings(self):
        return await trakt_client.get_ratings(self.client_id, self.access_token)

    async def get_watchlist(self):
        return await trakt_client.get_watchlist(self.client_id, self.access_token)

    async def get_user_lists(self):
        return await trakt_client.get_user_lists(self.client_id, self.access_token)

    async def get_list_items(self, list_slug: str):
        return await trakt_client.get_list_items(self.client_id, self.access_token, list_slug)


class ExportTraktSource:
    """Serves watched history, ratings, and lists from a parsed Trakt data export.

    An export is always a full snapshot, so start_at/end_at bounds are ignored.
    """

    def __init__(self, data: TraktExportData):
        self.data = data

    async def get_history_movies(self, start_at: datetime | None, end_at: datetime):
        return self.data.history_movies

    async def get_history_episodes(self, start_at: datetime | None, end_at: datetime):
        return self.data.history_episodes

    async def get_ratings(self):
        return self.data.ratings

    async def get_watchlist(self):
        return self.data.watchlist

    async def get_user_lists(self):
        return self.data.lists

    async def get_list_items(self, list_slug: str):
        return self.data.list_items.get(list_slug, [])

    async def get_comments(self):
        return self.data.comments


async def _apply_trakt_import(
    db: AsyncSession,
    job_id: int,
    user_id: int,
    source: LiveTraktSource | ExportTraktSource,
    api_key: str | None,
    sync_watched: bool,
    sync_ratings: bool,
    sync_lists: bool,
    split_watchlist: bool,
    history_start: datetime | None,
    history_end: datetime,
    window_minutes: int = DEFAULT_DEDUP_WINDOW_MINUTES,
    reconcile_provider: str | None = None,
) -> tuple[dict, int, bool, set[int], RatingChanges]:
    """Fetches (from `source`) and applies watched history, ratings, and lists.

    Shared by the live-API sync (run_trakt_sync) and the export-file import
    (run_trakt_export_sync) — the two differ only in how `source` is built and
    in what happens before/after this call (token refresh + cursor tracking is
    live-only). Returns (stats, watched_processed, history_had_errors, new_watched,
    new_ratings) for the caller to use in its own completion/fan-out handling.
    """
    from core.sync_jobs import raise_if_cancelled

    stats = {"movies": 0, "episodes": 0, "ratings": 0, "rating_conflicts": 0, "lists": 0, "list_items": 0, "skipped": 0, "errors": 0}
    _new_watched: set[int] = set()
    _new_ratings: RatingChanges = {}
    watched_processed = 0
    history_error_count = stats["errors"]
    history_had_errors = False
    stats["history_mode"] = "full" if history_start is None else "incremental"
    # Shared for the whole import/sync run: a (show_tmdb_id, season_number) TMDB
    # season fetch covers every episode in that season, so without this cache,
    # each already-uncreated episode from the same season (across both the watched
    # history and ratings sections below) would redundantly re-fetch the same
    # season from TMDB instead of once.
    season_cache: dict[tuple[int, int], dict] = {}

    def _is_duplicate_play(existing_times: dict[int, list[datetime]], media_id: int, watched_at: datetime | None) -> bool:
        # An unknown-dated play matches any existing play of the same item,
        # dated or not - there's nothing to compare a window against, and this
        # matches the identity-only convention used elsewhere for unknown
        # dates (see _history_play_seen).
        if watched_at is None:
            return bool(existing_times.get(media_id))
        return is_duplicate_watch_time(existing_times, media_id, watched_at, window_minutes)

    # ── Watched Movies ────────────────────────────────────────────────
    # Uses /sync/history (one row per play) rather than /sync/watched
    # (one aggregated row per title) so every distinct play of a movie
    # gets its own WatchEvent instead of only the most recent one.
    if sync_watched:
        print("  Fetching movie watch history from Trakt...")
        history_movies = await source.get_history_movies(
            start_at=history_start,
            end_at=history_end,
        )
        print(f"  {len(history_movies)} movie plays fetched from Trakt")
        await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(total_items=len(history_movies), current_step="Pulling watched movies"))
        await db.commit()

        # Pre-load every existing watch's effective time for this user, keyed
        # by media_id, so re-syncing doesn't duplicate a play that's already
        # been imported (from Trakt or anywhere else, within the dedup
        # window - #390) while still allowing genuinely distinct rewatches.
        existing_times = await load_existing_watch_times(db, user_id)

        for movie_index, item in enumerate(history_movies, start=1):
            movie_data = item.get("movie", {})
            tmdb_id = movie_data.get("ids", {}).get("tmdb")
            try:
                try:
                    async with db.begin_nested():
                        media = await _resolve_trakt_title(
                            db, user_id, "movie", item, api_key, notify_unmatched=False,
                        )
                        if not media:
                            stats["skipped"] += 1
                            continue
                        # Trakt returns an unknown-dated play as the Unix epoch.
                        # Store an inferred date locally so a later reliable
                        # observation can correct it in place.
                        watched_at = _parse_trakt_datetime(item.get("watched_at"))
                        corrected = bool(watched_at and await reconcile_inferred_watch_date(
                            db, user_id, media.id, watched_at,
                        ))
                        if corrected:
                            existing_times.setdefault(media.id, []).append(watched_at)
                            stats["skipped"] += 1
                        elif not _is_duplicate_play(existing_times, media.id, watched_at):
                            db.add(WatchEvent(
                                user_id=user_id,
                                media_id=media.id,
                                watched_at=watched_at or inferred_watch_datetime(),
                                date_inferred=watched_at is None,
                                completed=True,
                                play_count=1,
                            ))
                            existing_times.setdefault(media.id, []).append(watched_at or inferred_watch_datetime())
                            _new_watched.add(media.id)
                            stats["movies"] += 1
                        else:
                            stats["skipped"] += 1
                except Exception as exc:
                    logger.warning("Error processing Trakt movie tmdb=%s: %s", tmdb_id, exc)
                    stats["errors"] += 1
            finally:
                watched_processed = movie_index
                if movie_index % 25 == 0 or movie_index == len(history_movies):
                    await db.execute(
                        update(SyncJob)
                        .where(SyncJob.id == job_id)
                        .values(processed_items=watched_processed)
                    )
                    await db.commit()
                    await raise_if_cancelled(db, job_id)

        await db.commit()

    # ── Watched Shows / Episodes ──────────────────────────────────────
    # Same rationale as movies above: /sync/history/episodes returns
    # one row per play instead of one aggregated row per episode.
    if sync_watched:
        print("  Fetching episode watch history from Trakt...")
        history_episodes = await source.get_history_episodes(
            start_at=history_start,
            end_at=history_end,
        )
        print(f"  {len(history_episodes)} episode plays fetched from Trakt")

        await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(
            total_items=len(history_movies) + len(history_episodes),
            processed_items=watched_processed,
            current_step="Pulling watched shows",
        ))
        await db.commit()

        # Re-fetch watch times (may have grown from movie sync)
        existing_times = await load_existing_watch_times(db, user_id)

        # Group plays by show so _get_or_create_show only runs once per show
        plays_by_show: dict[int, list[dict]] = {}
        for entry in history_episodes:
            show_tmdb_id = entry.get("show", {}).get("ids", {}).get("tmdb")
            if not show_tmdb_id:
                from core.provider_matching import (
                    provider_override,
                    record_unmatched_import,
                )
                mapped, ignored, _key, _title = await provider_override(
                    db, user_id=user_id, provider="trakt", kind="shows", entry={"show": entry.get("show", {})}
                )
                show_tmdb_id = mapped.tmdb_id if mapped else None
                if not show_tmdb_id and not ignored:
                    await record_unmatched_import(
                        db, user_id=user_id, provider="trakt", kind="shows",
                        entry={"show": entry.get("show", {})}, notify=False,
                    )
            if show_tmdb_id:
                plays_by_show.setdefault(show_tmdb_id, []).append(entry)
            else:
                stats["skipped"] += 1

        async def process_show(show_tmdb_id: int, entries: list[dict]):
            show_title = entries[0].get("show", {}).get("title", "")
            try:
                async with db.begin_nested():
                    show = await _get_or_create_show(db, show_tmdb_id, show_title, api_key)
                    if not show:
                        stats["errors"] += 1
                        return
                    await db.flush()

                for entry in entries:
                    ep_data = entry.get("episode", {})
                    season_num = ep_data.get("season")
                    ep_num = ep_data.get("number")
                    if season_num is None or ep_num is None:
                        stats["skipped"] += 1
                        continue
                    try:
                        async with db.begin_nested():
                            media = await catalog_import.get_or_create_episode_media(
                                db, show.id, show_tmdb_id, season_num, ep_num, api_key, season_cache
                            )
                            if not media:
                                stats["errors"] += 1
                                continue
                            # See the movie branch: an unknown date remains
                            # replaceable when better evidence arrives.
                            watched_at = _parse_trakt_datetime(entry.get("watched_at"))
                            corrected = bool(watched_at and await reconcile_inferred_watch_date(
                                db, user_id, media.id, watched_at,
                            ))
                            if corrected:
                                existing_times.setdefault(media.id, []).append(watched_at)
                                stats["skipped"] += 1
                            elif not _is_duplicate_play(existing_times, media.id, watched_at):
                                event = WatchEvent(
                                    user_id=user_id,
                                    media_id=media.id,
                                    watched_at=watched_at or inferred_watch_datetime(),
                                    date_inferred=watched_at is None,
                                    completed=True,
                                    play_count=1,
                                )
                                db.add(event)
                                await db.flush()
                                await record_rewatch_progress(db, user_id, media.id, event.id)
                                existing_times.setdefault(media.id, []).append(watched_at or inferred_watch_datetime())
                                _new_watched.add(media.id)
                                stats["episodes"] += 1
                            else:
                                stats["skipped"] += 1
                    except Exception as exc:
                        logger.warning("Error processing episode s%se%s for show tmdb=%s: %s", season_num, ep_num, show_tmdb_id, exc)
                        stats["errors"] += 1
            except Exception as exc:
                logger.warning("Error processing Trakt show tmdb=%s: %s", show_tmdb_id, exc)
                stats["errors"] += 1

        show_plays = list(plays_by_show.items())
        for i, (show_tmdb_id, entries) in enumerate(show_plays):
            await process_show(show_tmdb_id, entries)
            watched_processed += len(entries)
            if (i + 1) % 10 == 0 or i + 1 == len(show_plays):
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(
                    processed_items=watched_processed
                ))
                await db.commit()
                await raise_if_cancelled(db, job_id)
        await db.commit()
        history_had_errors = stats["errors"] > history_error_count

    await raise_if_cancelled(db, job_id)

    # ── Ratings ───────────────────────────────────────────────────────
    if sync_ratings:
        print("  Fetching ratings from Trakt...")
        ratings_data = await source.get_ratings()

        ratings_result = await db.execute(
            select(Rating).where(
                Rating.user_id == user_id,
                Rating.episode_order.is_(None),
            )
        )
        existing_ratings = {
            (rating.media_id, rating.season_number): rating
            for rating in ratings_result.scalars().all()
        }

        for kind in ("movies", "shows", "seasons", "episodes"):
            for item in ratings_data.get(kind, []):
                try:
                    async with db.begin_nested():
                        season_number: int | None = None
                        if kind == "movies":
                            media = await _resolve_trakt_title(db, user_id, "movie", item, api_key)
                        elif kind == "episodes":
                            show_data = item.get("show", {})
                            show_tmdb_id = show_data.get("ids", {}).get("tmdb")
                            ep_data = item.get("episode", {})
                            ep_season = ep_data.get("season")
                            ep_number = ep_data.get("number")
                            media = None
                            if show_tmdb_id and ep_season is not None and ep_number is not None:
                                ep_show = await _get_or_create_show(db, show_tmdb_id, show_data.get("title", ""), api_key)
                                if ep_show:
                                    media = await catalog_import.get_or_create_episode_media(
                                        db, ep_show.id, show_tmdb_id, ep_season, ep_number, api_key, season_cache
                                    )
                        else:
                            show_data = item.get("show", {})
                            if kind == "seasons":
                                season_number = item.get("season", {}).get("number")
                            media = await _resolve_trakt_title(db, user_id, "show", item, api_key) if kind != "seasons" or season_number is not None else None

                        if not media:
                            stats["skipped"] += 1
                            continue
                        if reconcile_provider:
                            from core.cloud_rating_reconciliation import (
                                reconcile_cloud_rating,
                            )
                            outcome = await reconcile_cloud_rating(
                                db,
                                provider=reconcile_provider,
                                user_id=user_id,
                                media=media,
                                season_number=season_number,
                                remote_score=float(item["rating"]),
                                remote_rated_at=optional_imported_rated_at(item.get("rated_at")),
                                existing=existing_ratings,
                                changed=_new_ratings,
                            )
                            if outcome == "applied":
                                stats["ratings"] += 1
                            elif outcome == "conflict":
                                stats["rating_conflicts"] += 1
                            else:
                                stats["skipped"] += 1
                        elif _apply_imported_rating(
                            db,
                            user_id,
                            media,
                            season_number,
                            item,
                            existing_ratings,
                            _new_ratings,
                        ):
                            stats["ratings"] += 1
                        else:
                            stats["skipped"] += 1
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("Invalid Trakt %s rating: %s", kind, exc)
                    stats["errors"] += 1
                except Exception as exc:
                    logger.warning("Error processing Trakt %s rating: %s", kind, exc)
                    stats["errors"] += 1

        await db.commit()

    await raise_if_cancelled(db, job_id)

    # ── Lists (watchlist + personal lists) ───────────────────────────
    if sync_lists:
        WATCHLIST_SLUG         = "__watchlist__"
        WATCHLIST_MOVIES_SLUG  = "__watchlist_movies__"
        WATCHLIST_SHOWS_SLUG   = "__watchlist_shows__"

        print("  Fetching watchlist from Trakt...")
        watchlist_items = await source.get_watchlist()
        print(f"  {len(watchlist_items)} watchlist items fetched from Trakt")

        if split_watchlist:
            # ── Split mode: two lists keyed by media type ─────────────
            async def _get_or_create_split_list(slug: str, name: str) -> ListModel:
                r = await db.execute(
                    select(ListModel).where(ListModel.user_id == user_id, ListModel.trakt_slug == slug)
                )
                lst = r.scalar_one_or_none()
                if not lst:
                    lst = ListModel(user_id=user_id, name=name, trakt_slug=slug)
                    db.add(lst)
                    await db.flush()
                    stats["lists"] += 1
                return lst

            movies_list = await _get_or_create_split_list(WATCHLIST_MOVIES_SLUG, "Trakt - Watchlist (Movies)")
            shows_list  = await _get_or_create_split_list(WATCHLIST_SHOWS_SLUG,  "Trakt - Watchlist (Shows)")

            movies_existing = {row[0] for row in (await db.execute(
                select(ListItem.media_id).where(ListItem.list_id == movies_list.id)
            )).all()}
            shows_existing  = {row[0] for row in (await db.execute(
                select(ListItem.media_id).where(ListItem.list_id == shows_list.id)
            )).all()}

            # Reconcile: remove items no longer on Trakt watchlist
            trakt_movie_tmdb_ids = {
                e.get("movie", {}).get("ids", {}).get("tmdb")
                for e in watchlist_items if e.get("type") == "movie"
            } - {None}
            trakt_show_tmdb_ids = {
                e.get("show", {}).get("ids", {}).get("tmdb")
                for e in watchlist_items if e.get("type") == "show"
            } - {None}

            # Remove stale movies
            if movies_existing:
                stale_movies_result = await db.execute(
                    select(Media).where(
                        Media.id.in_(movies_existing),
                        Media.tmdb_id.notin_(trakt_movie_tmdb_ids),
                    )
                )
                for stale in stale_movies_result.scalars():
                    await db.execute(
                        ListItem.__table__.delete().where(
                            ListItem.list_id == movies_list.id,
                            ListItem.media_id == stale.id,
                        )
                    )
                    movies_existing.discard(stale.id)

            # Remove stale shows
            if shows_existing:
                stale_shows_result = await db.execute(
                    select(Media).where(
                        Media.id.in_(shows_existing),
                        Media.tmdb_id.notin_(trakt_show_tmdb_ids),
                    )
                )
                for stale in stale_shows_result.scalars():
                    await db.execute(
                        ListItem.__table__.delete().where(
                            ListItem.list_id == shows_list.id,
                            ListItem.media_id == stale.id,
                        )
                    )
                    shows_existing.discard(stale.id)

            for entry in watchlist_items:
                item_type = entry.get("type")
                media: Media | None = None
                try:
                    if item_type == "movie":
                        async with db.begin_nested():
                            media = await _resolve_trakt_title(db, user_id, "movie", entry, api_key)
                        if media and media.id not in movies_existing:
                            db.add(ListItem(list_id=movies_list.id, media_id=media.id))
                            movies_existing.add(media.id)
                            stats["list_items"] += 1
                    elif item_type == "show":
                        async with db.begin_nested():
                            media = await _resolve_trakt_title(db, user_id, "show", entry, api_key)
                        if media and media.id not in shows_existing:
                            db.add(ListItem(list_id=shows_list.id, media_id=media.id))
                            shows_existing.add(media.id)
                            stats["list_items"] += 1
                except Exception as exc:
                    logger.warning("Error processing Trakt watchlist item (%s): %s", item_type, exc)
                    stats["errors"] += 1

        else:
            # ── Unified mode: one list for movies + shows ─────────────
            wl_result = await db.execute(
                select(ListModel).where(
                    ListModel.user_id == user_id,
                    ListModel.trakt_slug == WATCHLIST_SLUG,
                )
            )
            watchlist = wl_result.scalar_one_or_none()
            if not watchlist:
                watchlist = ListModel(user_id=user_id, name="Trakt - Watchlist", trakt_slug=WATCHLIST_SLUG)
                db.add(watchlist)
                await db.flush()
                stats["lists"] += 1

            wl_items_result = await db.execute(
                select(ListItem.media_id).where(ListItem.list_id == watchlist.id)
            )
            wl_existing_ids: set[int] = {row[0] for row in wl_items_result}

            for entry in watchlist_items:
                item_type = entry.get("type")
                media: Media | None = None
                try:
                    if item_type == "movie":
                        async with db.begin_nested():
                            media = await _resolve_trakt_title(db, user_id, "movie", entry, api_key)
                    elif item_type == "show":
                        async with db.begin_nested():
                            media = await _resolve_trakt_title(db, user_id, "show", entry, api_key)
                    else:
                        continue

                    if media and media.id not in wl_existing_ids:
                        db.add(ListItem(list_id=watchlist.id, media_id=media.id))
                        wl_existing_ids.add(media.id)
                        stats["list_items"] += 1
                except Exception as exc:
                    logger.warning("Error processing Trakt watchlist item (%s): %s", item_type, exc)
                    stats["errors"] += 1

        await db.commit()

        print("  Fetching lists from Trakt...")
        trakt_lists = await source.get_user_lists()
        print(f"  {len(trakt_lists)} lists fetched from Trakt")

        for trakt_list in trakt_lists:
            list_name = trakt_list.get("name", "")
            list_slug = trakt_list.get("ids", {}).get("slug") or trakt_list.get("slug")
            if not list_slug or not list_name:
                continue

            local_name = f"Trakt - {list_name}"

            # Find or create the local list — keyed by trakt_slug, not name
            existing_list_result = await db.execute(
                select(ListModel).where(
                    ListModel.user_id == user_id,
                    ListModel.trakt_slug == list_slug,
                )
            )
            local_list = existing_list_result.scalar_one_or_none()
            if not local_list:
                local_list = ListModel(
                    user_id=user_id,
                    name=local_name,
                    description=trakt_list.get("description"),
                    trakt_slug=list_slug,
                )
                db.add(local_list)
                await db.flush()
                stats["lists"] += 1

            # Pre-load existing list item keys to avoid duplicates. Keyed by
            # (media_id, season_number) rather than just media_id, since a season
            # list item shares its media_id with the whole show's entry.
            existing_items_result = await db.execute(
                select(ListItem.media_id, ListItem.season_number).where(ListItem.list_id == local_list.id)
            )
            existing_item_keys: set[tuple[int, int | None]] = {(row[0], row[1]) for row in existing_items_result}

            try:
                items = await source.get_list_items(list_slug)
            except Exception as exc:
                logger.warning("Could not fetch items for Trakt list %s: %s", list_slug, exc)
                continue

            for entry in items:
                item_type = entry.get("type")
                media: Media | None = None
                season_number: int | None = None
                try:
                    if item_type == "movie":
                        async with db.begin_nested():
                            media = await _resolve_trakt_title(db, user_id, "movie", entry, api_key)
                    elif item_type == "show":
                        async with db.begin_nested():
                            media = await _resolve_trakt_title(db, user_id, "show", entry, api_key)
                    elif item_type == "season":
                        show_data = entry.get("show", {})
                        show_tmdb_id = show_data.get("ids", {}).get("tmdb")
                        season_number = entry.get("season", {}).get("number")
                        if season_number is None:
                            continue
                        async with db.begin_nested():
                            media = await _resolve_trakt_title(db, user_id, "show", entry, api_key)
                    elif item_type == "episode":
                        show_data = entry.get("show", {})
                        show_tmdb_id = show_data.get("ids", {}).get("tmdb")
                        ep_data = entry.get("episode", {})
                        ep_season = ep_data.get("season")
                        ep_number = ep_data.get("number")
                        if not show_tmdb_id or ep_season is None or ep_number is None:
                            continue
                        async with db.begin_nested():
                            show = await _get_or_create_show(db, show_tmdb_id, show_data.get("title", ""), api_key)
                            if show:
                                media = await catalog_import.get_or_create_episode_media(
                                    db, show.id, show_tmdb_id, ep_season, ep_number, api_key
                                )
                    elif item_type == "person":
                        person_data = entry.get("person", {})
                        tmdb_id = person_data.get("ids", {}).get("tmdb")
                        if not tmdb_id:
                            continue
                        async with db.begin_nested():
                            media = await _get_or_create_person_media(db, tmdb_id, person_data.get("name", ""), api_key)
                    else:
                        continue

                    key = (media.id, season_number) if media else None
                    if media and key not in existing_item_keys:
                        db.add(ListItem(list_id=local_list.id, media_id=media.id, season_number=season_number))
                        existing_item_keys.add(key)
                        stats["list_items"] += 1
                except Exception as exc:
                    logger.warning("Error processing Trakt list item (%s): %s", item_type, exc)
                    stats["errors"] += 1

            await db.commit()

    return stats, watched_processed, history_had_errors, _new_watched, _new_ratings


def _trakt_import_summary(job_id: int, label: str, stats: dict) -> str:
    return (
        f"Trakt {label} job {job_id} completed. "
        f"Movies: {stats['movies']} new, {stats.get('skipped', 0)} skipped. "
        f"Episodes: {stats['episodes']} new. "
        f"Ratings: {stats['ratings']} new. "
        f"Lists: {stats['lists']} new, {stats['list_items']} items added. "
        f"Comments: {stats.get('comments', 0)} new. "
        f"Errors: {stats['errors']}."
    )


async def _apply_dropped_shows_import(db: AsyncSession, user_id: int, dropped_items: list[dict]) -> int:
    """Merges a provider's dropped-shows list into the local dropped_shows
    setting (#117 follow-up). Shared by Trakt and MDBList - both return
    dropped items shaped {..., "show": {"ids": {"tmdb": ...}}}. Only shows
    already known locally can be mapped (dropped_shows stores local Show.id,
    not tmdb_id), so a show Scrob has never seen is silently skipped rather
    than force-creating a local Show row just to hide it.

    Additive only, like the rest of this pull: a show un-dropped on Trakt
    does not get removed from Scrob's local dropped_shows here.
    """
    tmdb_ids = {
        item.get("show", {}).get("ids", {}).get("tmdb")
        for item in dropped_items
    }
    tmdb_ids.discard(None)
    if not tmdb_ids:
        return 0

    shows_result = await db.execute(select(Show.id).where(Show.tmdb_id.in_(tmdb_ids)))
    local_show_ids = {row[0] for row in shows_result.all()}
    if not local_show_ids:
        return 0

    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    settings = settings_result.scalar_one_or_none()
    if not settings:
        return 0

    dropped = set(settings.dropped_shows or [])
    new_ids = local_show_ids - dropped
    if new_ids:
        settings.dropped_shows = list(dropped | new_ids)
        flag_modified(settings, "dropped_shows")
        await db.commit()
    return len(new_ids)


async def _local_dropped_show_tmdb_ids(
    db: AsyncSession, settings: UserSettings, *, watched_by_user_id: int | None = None
) -> set[int]:
    """TMDB ids of the shows the user has dropped locally. dropped_shows stores
    local Show.id values, so this resolves them; a dropped show with no tmdb_id
    (TVDB-only) can't be pushed to Trakt/MDBList and is dropped from the set.

    When watched_by_user_id is given, only shows with at least one completed
    WatchEvent for that user are returned. Trakt's hidden/dropped list is a
    view over the progress / Up Next section, which only ever contains shows
    you have started: POST /users/hidden/dropped accepts a never-watched show
    (added: 1) but it never materializes in the GET, so the reconcile would
    keep re-pushing it every run forever (#329)."""
    if not settings.dropped_shows:
        return set()
    query = select(Show.tmdb_id).where(
        Show.id.in_(settings.dropped_shows), Show.tmdb_id.isnot(None)
    )
    if watched_by_user_id is not None:
        query = (
            query.join(Media, Media.show_id == Show.id)
            .join(WatchEvent, WatchEvent.media_id == Media.id)
            .where(WatchEvent.user_id == watched_by_user_id, WatchEvent.completed == True)
            .distinct()
        )
    result = await db.execute(query)
    return {row[0] for row in result.all()}


async def run_trakt_sync(user_id: int, job_id: int, full_resync: bool = False):
    from core.sync_jobs import SyncCancelled, short_error
    print(f"Starting Trakt sync for user {user_id}, job {job_id}")
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        try:
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.running, processed_items=0, total_items=0
                )
            )
            await db.commit()

            result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
            settings = result.scalar_one_or_none()

            if not settings or not settings.trakt_access_token:
                err = "Trakt is not connected"
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed, error_message=err))
                await db.commit()
                return

            if not settings.trakt_client_id:
                err = "Trakt Client ID not configured. Add it in Settings → Sync → Trakt."
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed, error_message=err))
                await db.commit()
                return

            # Validate / refresh the access token (see trakt_auth.ensure_valid_trakt_token).
            try:
                access_token = await trakt_auth.ensure_valid_trakt_token(db, settings)
            except trakt_auth.TraktTokenError as exc:
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed, error_message=short_error(exc)))
                await db.commit()
                return

            client_id = settings.trakt_client_id
            _gs_result = await db.execute(select(GlobalSettings).where(GlobalSettings.id == 1))
            _gs = _gs_result.scalar_one_or_none()
            api_key = settings.tmdb_api_key or settings_store.get_server_tmdb_key(_gs)

            history_cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
            history_start, history_end = _history_window(
                getattr(settings, "trakt_history_cursor_at", None),
                full_resync,
                history_cutoff,
            )

            source = LiveTraktSource(client_id, access_token)
            stats, watched_processed, history_had_errors, _new_watched, _new_ratings = await _apply_trakt_import(
                db, job_id, user_id, source, api_key,
                settings.trakt_sync_watched,
                settings.trakt_sync_ratings,
                settings.trakt_sync_lists,
                getattr(settings, "trakt_watchlist_split", False),
                history_start,
                history_end,
                dedup_window_from_settings(settings),
                reconcile_provider="trakt",
            )

            if settings.trakt_sync_watched and not history_had_errors:
                settings.trakt_history_cursor_at = history_end

            if settings.trakt_sync_dropped:
                dropped_items = await trakt_client.get_dropped_shows(client_id, access_token)
                stats["dropped"] = await _apply_dropped_shows_import(db, user_id, dropped_items)

            print(_trakt_import_summary(job_id, "sync", stats))
            # Import and reconcile before exporting only approved changed fields.
            from core.tracking_import import import_tracking_history
            from models.tracking import CloudBaseline
            first_import = await db.scalar(select(CloudBaseline.id).where(
                CloudBaseline.user_id == user_id, CloudBaseline.provider == "trakt")) is None
            stats["tracked_entries"] = await import_tracking_history(db, user_id, initial_import=first_import)
            from core.cloud_history_reconciliation import reconcile_cloud_watch_events
            accepted_watched: set[int] = set()
            history_reconciliation = await reconcile_cloud_watch_events(
                db, user_id=user_id, provider="trakt", new_media_ids=_new_watched,
                applied_media_ids=accepted_watched,
            )
            stats["tracking_updates"] = history_reconciliation["applied"]
            stats["tracking_conflicts"] = history_reconciliation["conflicts"]
            from core.cloud_reconciliation import record_cloud_import
            await record_cloud_import(db, user_id, "trakt", stats)
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.completed,
                    stats=stats,
                    processed_items=watched_processed,
                )
            )
            await db.commit()
            from core.pull_propagation import propagate_cloud_pull
            await propagate_cloud_pull(
                db, user_id=user_id, provider="trakt", watched_ids=accepted_watched,
                ratings=_new_ratings, complete=not history_had_errors and not stats["errors"],
            )

        except SyncCancelled:
            print(f"Trakt sync job {job_id} cancelled")
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.cancelled)
            )
            await db.commit()

        except Exception as exc:
            print(f"Trakt sync job {job_id} failed: {exc}")
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.failed, error_message=short_error(exc)
                )
            )
            await db.commit()


def _extract_trakt_comment(kind: str, entry: dict) -> tuple[str, int | None, int | None, int | None, dict] | None:
    """Map a raw Trakt export comment entry to (media_type, tmdb_id, season_number,
    episode_number, comment_dict). tmdb_id is the show's tmdb_id for season/episode
    comments, matching Comment.tmdb_id's documented convention. Returns None if the
    entry can't be resolved to a tmdb id (comment.py has no other identity to fall
    back to)."""
    comment = entry.get("comment") or {}
    if kind == "movies":
        tmdb_id = entry.get("movie", {}).get("ids", {}).get("tmdb")
        return ("movie", tmdb_id, None, None, comment) if tmdb_id else None
    if kind == "shows":
        tmdb_id = entry.get("show", {}).get("ids", {}).get("tmdb")
        return ("series", tmdb_id, None, None, comment) if tmdb_id else None
    if kind == "seasons":
        tmdb_id = entry.get("show", {}).get("ids", {}).get("tmdb")
        season_number = entry.get("season", {}).get("number")
        return ("series", tmdb_id, season_number, None, comment) if tmdb_id else None
    if kind == "episodes":
        tmdb_id = entry.get("show", {}).get("ids", {}).get("tmdb")
        ep_data = entry.get("episode", {})
        return ("episode", tmdb_id, ep_data.get("season"), ep_data.get("number"), comment) if tmdb_id else None
    return None


async def _apply_trakt_comments_import(
    db: AsyncSession,
    user_id: int,
    comments_data: dict[str, list[dict]],
    stats: dict,
) -> None:
    """Applies Trakt export comments to the current user's account.

    Comments have no media-catalog dependency (Comment is keyed directly by
    tmdb_id/season/episode, not a Media row), so unlike watched history and
    ratings this needs no TMDB lookups at all.
    """
    existing_result = await db.execute(select(Comment).where(Comment.user_id == user_id))
    existing_keys = {
        (c.media_type, c.tmdb_id, c.season_number, c.episode_number, c.content)
        for c in existing_result.scalars().all()
    }

    for kind, entries in comments_data.items():
        for entry in entries:
            extracted = _extract_trakt_comment(kind, entry)
            if not extracted:
                stats["skipped"] += 1
                continue
            media_type, tmdb_id, season_number, episode_number, comment = extracted
            content = comment.get("comment") or ""
            key = (media_type, tmdb_id, season_number, episode_number, content)
            if key in existing_keys:
                stats["skipped"] += 1
                continue
            db.add(Comment(
                user_id=user_id,
                media_type=media_type,
                tmdb_id=tmdb_id,
                season_number=season_number,
                episode_number=episode_number,
                content=content,
                is_spoiler=bool(comment.get("spoiler")),
                created_at=_parse_trakt_datetime(comment.get("created_at")) or datetime.utcnow(),
            ))
            existing_keys.add(key)
            stats["comments"] = stats.get("comments", 0) + 1
    await db.commit()


async def run_trakt_export_sync(
    user_id: int,
    job_id: int,
    export_data: TraktExportData,
    sync_watched: bool = True,
    sync_ratings: bool = True,
    sync_lists: bool = True,
    sync_comments: bool = True,
):
    from core.sync_jobs import SyncCancelled, short_error
    print(f"Starting Trakt export import for user {user_id}, job {job_id}")
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        try:
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.running, processed_items=0, total_items=0
                )
            )
            await db.commit()

            result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
            settings = result.scalar_one_or_none()

            if not settings:
                err = "User settings not found"
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed, error_message=err))
                await db.commit()
                return

            _gs_result = await db.execute(select(GlobalSettings).where(GlobalSettings.id == 1))
            _gs = _gs_result.scalar_one_or_none()
            api_key = settings.tmdb_api_key or settings_store.get_server_tmdb_key(_gs)

            source = ExportTraktSource(export_data)
            history_end = datetime.now(timezone.utc).replace(tzinfo=None)

            # What to import is chosen per-upload (sync_watched/sync_ratings/sync_lists,
            # picked in the import confirmation modal), not read from the trakt_sync_*
            # preferences — those only gate the continuous OAuth pull, where
            # trakt_sync_lists defaults to False so existing users aren't surprised by
            # new lists appearing unprompted.
            stats, watched_processed, _history_had_errors, _new_watched, _new_ratings = await _apply_trakt_import(
                db, job_id, user_id, source, api_key,
                sync_watched,
                sync_ratings,
                sync_lists,
                getattr(settings, "trakt_watchlist_split", False),
                None,
                history_end,
                dedup_window_from_settings(settings),
            )

            if sync_comments:
                await _apply_trakt_comments_import(db, user_id, await source.get_comments(), stats)
                watched_processed += stats.get("comments", 0)

            print(_trakt_import_summary(job_id, "export import", stats))
            # An import only populates scrob's own data — it never automatically pushes
            # to other connections; users push explicitly per-service (the "Push" buttons).
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.completed,
                    stats=stats,
                    processed_items=watched_processed,
                )
            )
            await db.commit()

        except SyncCancelled:
            print(f"Trakt export import job {job_id} cancelled")
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.cancelled)
            )
            await db.commit()

        except Exception as exc:
            print(f"Trakt export import job {job_id} failed: {exc}")
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.failed, error_message=short_error(exc)
                )
            )
            await db.commit()


async def _run_trakt_push(user_id: int, job_id: int) -> None:
    from core.sync_jobs import SyncCancelled, raise_if_cancelled, short_error
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        try:
            # Background runners are also invoked by the scheduler. Keep the
            # reconciliation barrier here so no caller can bypass the API gate.
            await require_cloud_reconciliation(db, user_id, "trakt")
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.running)
            )
            await db.commit()

            settings_result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == user_id)
            )
            settings = settings_result.scalar_one_or_none()
            # Validate / refresh the token before pushing. Skipping this is what
            # made scheduled pushes 401-loop for days every 7 days (#326); the
            # helper mutates settings.trakt_access_token in place on refresh, so
            # the token reads further down pick up the new one automatically.
            try:
                await trakt_auth.ensure_valid_trakt_token(db, settings)
            except trakt_auth.TraktTokenError as exc:
                await db.execute(
                    update(SyncJob)
                    .where(SyncJob.id == job_id)
                    .values(status=SyncStatus.failed, error_message=short_error(exc))
                )
                await db.commit()
                return

            all_media_ids: set[int] = set()
            watch_events: list[tuple[int, datetime]] = []
            collected_ids: set[int] = set()
            ratings_map: RatingChanges = {}

            # Dropped shows Trakt's hidden/dropped list is missing. The one-shot
            # push at drop time (_push_show_dropped_to_providers) is best-effort;
            # if it failed at a bad moment nothing else ever retried it (#329).
            dropped_to_push: list[int] = []
            if settings.trakt_push_dropped:
                try:
                    local_dropped = await _local_dropped_show_tmdb_ids(
                        db, settings, watched_by_user_id=user_id
                    )
                    if local_dropped:
                        remote_dropped = await trakt_client.get_dropped_shows(
                            settings.trakt_client_id, settings.trakt_access_token
                        )
                        remote_tmdb = {
                            (it.get("show") or {}).get("ids", {}).get("tmdb") for it in remote_dropped
                        }
                        remote_tmdb.discard(None)
                        dropped_to_push = sorted(local_dropped - remote_tmdb)
                except Exception as exc:
                    logger.warning("Trakt push job %s: could not reconcile dropped shows: %s", job_id, exc)

            if settings.trakt_push_watched:
                watched_result = await db.execute(
                    select(WatchEvent.media_id, WatchEvent.watched_at).where(
                        WatchEvent.user_id == user_id,
                        WatchEvent.completed.is_(True),
                    )
                )
                watch_events = [(media_id, watched_at) for media_id, watched_at in watched_result.all()]
                all_media_ids |= {media_id for media_id, _ in watch_events}


            if settings.trakt_push_collection:
                collected_result = await db.execute(
                    select(Collection.media_id).where(Collection.user_id == user_id)
                )
                collected_ids = {row[0] for row in collected_result.all()}
                all_media_ids |= collected_ids

            if settings.trakt_push_ratings:
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
                all_media_ids |= {media_id for media_id, _ in ratings_map}

            if not all_media_ids and not dropped_to_push:
                await db.execute(
                    update(SyncJob)
                    .where(SyncJob.id == job_id)
                    .values(
                        status=SyncStatus.completed,
                        stats={"succeeded": 0, "failed": 0, "skipped": 0},
                        processed_items=0,
                        total_items=0,
                    )
                )
                await db.commit()
                return

            media_result = await db.execute(select(Media).where(Media.id.in_(all_media_ids)))
            media_by_id: dict[int, Media] = {media.id: media for media in media_result.scalars().all()}

            show_ids = {media.show_id for media in media_by_id.values() if media.show_id}
            shows_by_id: dict[int, Show] = {}
            if show_ids:
                shows_result = await db.execute(select(Show).where(Show.id.in_(show_ids)))
                shows_by_id = {show.id: show for show in shows_result.scalars().all()}

            push_tasks: list[tuple[str, int, "Coroutine"]] = []
            watched_already_present = 0
            ratings_already_present = 0
            collection_already_present = 0

            if settings.trakt_push_watched:
                movie_candidates: list[tuple[tuple, int, datetime]] = []
                episode_candidates: list[tuple[tuple, int, int, int, datetime]] = []
                local_keys: set[tuple] = set()

                for media_id, watched_at in watch_events:
                    media = media_by_id.get(media_id)
                    if not media:
                        continue
                    normalized_at = _normalize_history_time(watched_at)
                    if media.media_type == MediaType.movie and media.tmdb_id:
                        key = ("movie", media.tmdb_id, normalized_at)
                        if key not in local_keys:
                            local_keys.add(key)
                            movie_candidates.append((key, media.tmdb_id, watched_at))
                    elif (
                        media.media_type == MediaType.episode
                        and media.show_id
                        and media.season_number is not None
                        and media.episode_number is not None
                        and not is_unmapped_tvdb_episode(media)
                    ):
                        show = shows_by_id.get(media.show_id)
                        if show and show.tmdb_id:
                            key = (
                                "episode",
                                show.tmdb_id,
                                media.season_number,
                                media.episode_number,
                                normalized_at,
                            )
                            if key not in local_keys:
                                local_keys.add(key)
                                episode_candidates.append((
                                    key,
                                    show.tmdb_id,
                                    media.season_number,
                                    media.episode_number,
                                    watched_at,
                                ))

                if local_keys:
                    timestamps = [key[-1] for key in local_keys]
                    known_timestamps = [t for t in timestamps if t is not None]
                    if len(known_timestamps) < len(timestamps):
                        # At least one candidate has no known date — an unbounded
                        # fetch is required to find any existing "unknown" remote
                        # entry it might already match (there's no window to scope it to).
                        dedup_start_at, dedup_end_at = None, None
                    else:
                        dedup_start_at = min(known_timestamps) - TRAKT_HISTORY_OVERLAP
                        dedup_end_at = max(known_timestamps) + TRAKT_HISTORY_OVERLAP
                    remote_movies, remote_episodes = await asyncio.gather(
                        trakt_client.get_history_movies(
                            settings.trakt_client_id,
                            settings.trakt_access_token,
                            start_at=dedup_start_at,
                            end_at=dedup_end_at,
                        ),
                        trakt_client.get_history_episodes(
                            settings.trakt_client_id,
                            settings.trakt_access_token,
                            start_at=dedup_start_at,
                            end_at=dedup_end_at,
                        ),
                    )
                    remote_times = _remote_history_times(remote_movies, remote_episodes)
                    watched_already_present = sum(
                        1 for key in local_keys
                        if _history_play_seen(remote_times, key[:-1], key[-1])
                    )
                    pending: list[tuple[str, tuple]] = [
                        ("movie", (tmdb_id, watched_at))
                        for key, tmdb_id, watched_at in movie_candidates
                        if not _history_play_seen(remote_times, key[:-1], key[-1])
                    ]
                    pending.extend(
                        ("episode", (show_tmdb_id, season, episode_number, watched_at))
                        for key, show_tmdb_id, season, episode_number, watched_at in episode_candidates
                        if not _history_play_seen(remote_times, key[:-1], key[-1])
                    )

                    for index in range(0, len(pending), TRAKT_HISTORY_PUSH_BATCH_SIZE):
                        chunk = pending[index:index + TRAKT_HISTORY_PUSH_BATCH_SIZE]
                        movies = [payload for kind, payload in chunk if kind == "movie"]
                        episodes = [payload for kind, payload in chunk if kind == "episode"]
                        push_tasks.append((
                            "watched",
                            len(chunk),
                            trakt_client.add_to_history_batch(
                                settings.trakt_client_id,
                                settings.trakt_access_token,
                                movies,
                                episodes,
                            ),
                        ))

            if settings.trakt_push_collection:
                # Dedup against what Trakt already has - otherwise the entire
                # library (often tens of thousands of episodes) is re-POSTed on
                # every scheduled push, burning the write quota that real-time
                # scrobbles share (#327). A failed fetch just means no dedup this
                # run: still correct, Trakt no-ops the duplicates.
                remote_collection_keys: set[tuple] = set()
                try:
                    remote_collection_keys = _remote_collection_keys(
                        await trakt_client.get_collection(
                            settings.trakt_client_id, settings.trakt_access_token
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "Trakt push job %s: could not fetch remote collection: %s", job_id, exc
                    )

                collection_movies: list[int] = []
                collection_episodes: list[tuple[int, int, int]] = []
                for media_id in collected_ids:
                    media = media_by_id.get(media_id)
                    if not media:
                        continue
                    if media.media_type == MediaType.movie and media.tmdb_id:
                        if ("movie", media.tmdb_id) in remote_collection_keys:
                            collection_already_present += 1
                            continue
                        collection_movies.append(media.tmdb_id)
                    elif (
                        media.media_type == MediaType.episode
                        and media.show_id
                        and media.season_number is not None
                        and media.episode_number is not None
                        and not is_unmapped_tvdb_episode(media)
                    ):
                        show = shows_by_id.get(media.show_id)
                        if show and show.tmdb_id:
                            if (
                                "episode", show.tmdb_id, media.season_number, media.episode_number
                            ) in remote_collection_keys:
                                collection_already_present += 1
                                continue
                            collection_episodes.append((
                                show.tmdb_id,
                                media.season_number,
                                media.episode_number,
                            ))
                collection_count = len(collection_movies) + len(collection_episodes)
                if collection_count:
                    push_tasks.append((
                        "collection",
                        collection_count,
                        trakt_client.add_to_collection_batch(
                            settings.trakt_client_id,
                            settings.trakt_access_token,
                            collection_movies,
                            collection_episodes,
                        ),
                    ))

            if settings.trakt_push_ratings and ratings_map:

                season_tmdb_ids = await outbound_sync.resolve_tmdb_season_ids(
                    media_by_id,
                    set(ratings_map),
                    await settings_store.get_effective_tmdb_key(db, settings),
                )

                # Dedup against what Trakt already has, keyed by (kind, tmdb id,
                # season number, rounded rating) - so a steady-state push sends
                # nothing instead of re-POSTing every rating hourly (#327).
                remote_rating_keys: set[tuple] = set()
                try:
                    remote_ratings = await trakt_client.get_ratings(
                        settings.trakt_client_id, settings.trakt_access_token
                    )
                    for it in remote_ratings.get("movies", []):
                        tid = ((it.get("movie") or {}).get("ids") or {}).get("tmdb")
                        if tid is not None:
                            remote_rating_keys.add(("movie", tid, None, int(it["rating"])))
                    for it in remote_ratings.get("shows", []):
                        tid = ((it.get("show") or {}).get("ids") or {}).get("tmdb")
                        if tid is not None:
                            remote_rating_keys.add(("show", tid, None, int(it["rating"])))
                    for it in remote_ratings.get("seasons", []):
                        show_tid = ((it.get("show") or {}).get("ids") or {}).get("tmdb")
                        snum = (it.get("season") or {}).get("number")
                        if show_tid is not None and snum is not None:
                            remote_rating_keys.add(("season", show_tid, snum, int(it["rating"])))
                except Exception as exc:
                    # No dedup this run - still safe, just re-sends (batched).
                    logger.warning("Trakt push job %s: could not fetch remote ratings: %s", job_id, exc)

                movie_ratings: list[tuple[int, float]] = []
                show_ratings: list[tuple[int, float]] = []
                season_ratings: list[tuple[int, float]] = []
                for key, rating in ratings_map.items():
                    media_id, season_number = key
                    media = media_by_id.get(media_id)
                    if not media or not media.tmdb_id:
                        continue
                    from core.rating_projection import integer_provider_score
                    rounded = integer_provider_score(rating)
                    if season_number is not None:
                        season_tmdb_id = season_tmdb_ids.get(key)
                        if not season_tmdb_id:
                            continue
                        if ("season", media.tmdb_id, season_number, rounded) in remote_rating_keys:
                            ratings_already_present += 1
                            continue
                        season_ratings.append((season_tmdb_id, rating))
                    elif media.media_type == MediaType.movie:
                        if ("movie", media.tmdb_id, None, rounded) in remote_rating_keys:
                            ratings_already_present += 1
                            continue
                        movie_ratings.append((media.tmdb_id, rating))
                    elif media.media_type == MediaType.series:
                        if ("show", media.tmdb_id, None, rounded) in remote_rating_keys:
                            ratings_already_present += 1
                            continue
                        show_ratings.append((media.tmdb_id, rating))

                # One /sync/ratings POST per chunk (arrays of movies/shows/
                # seasons), not one per rating.
                pending_ratings: list[tuple[str, tuple]] = (
                    [("movie", p) for p in movie_ratings]
                    + [("show", p) for p in show_ratings]
                    + [("season", p) for p in season_ratings]
                )
                for index in range(0, len(pending_ratings), TRAKT_RATINGS_PUSH_BATCH_SIZE):
                    chunk = pending_ratings[index:index + TRAKT_RATINGS_PUSH_BATCH_SIZE]
                    push_tasks.append((
                        "ratings",
                        len(chunk),
                        trakt_client.set_ratings_batch(
                            settings.trakt_client_id,
                            settings.trakt_access_token,
                            [p for k, p in chunk if k == "movie"],
                            [p for k, p in chunk if k == "show"],
                            [p for k, p in chunk if k == "season"],
                        ),
                    ))

            if dropped_to_push:
                push_tasks.append((
                    "dropped",
                    len(dropped_to_push),
                    trakt_client.add_to_hidden_batch(
                        settings.trakt_client_id, settings.trakt_access_token, "dropped", dropped_to_push,
                    ),
                ))

            total = sum(item_count for _, item_count, _ in push_tasks)
            already_present = (
                watched_already_present + ratings_already_present + collection_already_present
            )
            await db.execute(
                update(SyncJob).where(SyncJob.id == job_id).values(total_items=total)
            )
            await db.commit()

            if not push_tasks:
                print(f"Trakt push job {job_id}: nothing to push - everything already on Trakt.")
                await db.execute(
                    update(SyncJob)
                    .where(SyncJob.id == job_id)
                    .values(
                        status=SyncStatus.completed,
                        stats={"succeeded": 0, "failed": 0, "skipped": already_present},
                        processed_items=0,
                    )
                )
                await db.commit()
                return

            queued_counts: dict[str, int] = {}
            for category, item_count, _ in push_tasks:
                queued_counts[category] = queued_counts.get(category, 0) + item_count
            print(
                f"Trakt push job {job_id}: queued "
                + ", ".join(f"{count} {category}" for category, count in queued_counts.items())
                + f" ({total} total, {already_present} already present)."
            )

            # One request at a time, spaced by TRAKT_PUSH_REQUEST_GAP - Trakt's
            # write limit is ~1/s and it's shared with real-time scrobbles (#327).
            succeeded = 0
            failed = 0
            succeeded_by_category: dict[str, int] = {}
            failed_by_category: dict[str, int] = {}
            for i, (category, item_count, task) in enumerate(push_tasks):
                try:
                    await task
                    succeeded += item_count
                    succeeded_by_category[category] = succeeded_by_category.get(category, 0) + item_count
                except Exception as exc:
                    logger.warning(
                        "Trakt push job %s: a %s batch (%s items) failed: %s",
                        job_id, category, item_count, exc,
                    )
                    failed += item_count
                    failed_by_category[category] = failed_by_category.get(category, 0) + item_count
                await db.execute(
                    update(SyncJob)
                    .where(SyncJob.id == job_id)
                    .values(processed_items=succeeded + failed)
                )
                await db.commit()
                await raise_if_cancelled(db, job_id)
                if i + 1 < len(push_tasks):
                    await asyncio.sleep(TRAKT_PUSH_REQUEST_GAP)
            breakdown = ", ".join(
                f"{category}: {succeeded_by_category.get(category, 0)} succeeded"
                + (
                    f", {failed_by_category[category]} failed"
                    if failed_by_category.get(category)
                    else ""
                )
                for category in queued_counts
            )
            print(
                f"Trakt push job {job_id} completed. {breakdown}. "
                f"Total: {succeeded}/{total} succeeded, {already_present} skipped."
            )

            await db.execute(
                update(SyncJob)
                .where(SyncJob.id == job_id)
                .values(
                    status=SyncStatus.completed,
                    stats={
                        "succeeded": succeeded,
                        "failed": failed,
                        "skipped": already_present,
                    },
                    processed_items=succeeded + failed,
                )
            )
            await db.commit()

        except SyncCancelled:
            print(f"Trakt push job {job_id} cancelled")
            await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(
                status=SyncStatus.cancelled,
                stats={"succeeded": succeeded, "failed": failed},
                processed_items=succeeded + failed,
            ))
            await db.commit()

        except Exception as exc:
            print(f"Trakt push job {job_id} failed: {exc}")
            await db.execute(
                update(SyncJob)
                .where(SyncJob.id == job_id)
                .values(status=SyncStatus.failed, error_message=short_error(exc))
            )
            await db.commit()
