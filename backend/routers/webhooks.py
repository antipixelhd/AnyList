from core import playback_sessions
from core import db_queries
from core import enrichment
from core import show_metadata
from core import scrobble_delivery, settings_store, outbound_sync, watch_echo, webhook_payloads
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError

from db import get_db
from dependencies import get_current_user_or_api_key
from models.media import Media
from models.show import Show
from models.collection import Collection, CollectionFile
from models.events import WatchEvent
from models.ratings import Rating
from models.users import User, UserSettings
from models.connections import MediaServerConnection
from models.scrobble_connection import ScrobbleConnection
from models.base import MediaType, CollectionSource
from models.playback_session import PlaybackSession
from models.playback_progress import PlaybackProgress
from models.library_selections import PlexLibrarySelection, JellyfinLibrarySelection, EmbyLibrarySelection
from core.enrichment import create_media_safely, enrich_media, enrich_media_safely
from core.episode_order import (
    ensure_episode_order_mapping_for_season,
    get_episode_order,
    get_mapping_by_tvdb_position,
    reconcile_divergent_episode_media,
)
from core.rewatch import record_rewatch_progress, get_active_rewatch
from core.watch_dedup import DEFAULT_DEDUP_WINDOW_MINUTES, dedup_window_from_settings, find_duplicate_watch_event
from models.rewatch import RewatchProgress
from core import tmdb

router = APIRouter()


async def _get_oldest_connection(db: AsyncSession, user_id: int, conn_type: str) -> MediaServerConnection | None:
    result = await db.execute(
        select(MediaServerConnection).where(
            MediaServerConnection.user_id == user_id,
            MediaServerConnection.type == conn_type,
        ).order_by(MediaServerConnection.id.asc()).limit(1)
    )
    return result.scalar_one_or_none()


async def _get_connection_by_id(db: AsyncSession, user_id: int, connection_id: int) -> MediaServerConnection | None:
    result = await db.execute(
        select(MediaServerConnection).where(
            MediaServerConnection.id == connection_id,
            MediaServerConnection.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _get_scrobble_connection_by_id(db: AsyncSession, user_id: int, connection_id: int) -> ScrobbleConnection | None:
    result = await db.execute(
        select(ScrobbleConnection).where(
            ScrobbleConnection.id == connection_id,
            ScrobbleConnection.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _duplicated_by_full_connection(db: AsyncSession, source: str, user_id: int, raw_session_id: str) -> bool:
    """True when a full <source> media-server connection already has an
    active PlaybackSession for this exact server-assigned session id (#312).

    A scrobble-only connection has no server URL/token of its own, so it
    can't identify "the same physical server" the way exclude_connection_id
    does elsewhere (#190) - but `raw_session_id` comes straight from the
    server's own webhook payload (Jellyfin's session_id / Plex's
    session_key), not something Scrob generates, so an identical value
    arriving via both a full connection's webhook and a scrobble-only
    connection's webhook is the server itself reporting the same playback
    twice, e.g. a user with a full connection to one Jellyfin server and a
    scrobble-only connection accidentally also pointed at that same server.
    Two connections to two different servers never collide here, since each
    server mints its own session ids independently.

    Used to skip the *outbound* scrobble dispatch only - local session
    tracking and watch-event writes are left as they already were (the
    latter already has its own 5-minute completed-watch dedup guard, see
    _write_watch_event).
    """
    full_key = f"{source}:{user_id}:{raw_session_id}"
    result = await db.execute(select(PlaybackSession.id).where(PlaybackSession.session_key == full_key))
    return result.scalar_one_or_none() is not None


# ── Shared helpers ─────────────────────────────────────────────────────────────

# Best-effort guard against a webhook delivery being processed twice in quick
# succession — media servers (and any relay/proxy in front of them) retry
# deliveries that don't get a fast 2xx, and nothing upstream de-duplicates
# those retries for us. This only catches immediate repeats within the same
# process; it's not a substitute for a persistent idempotency key, but it's
# low-risk and stops a retried `media.stop`/`media.scrobble` from re-firing
# the outbound Trakt/Simkl/MDBList scrobble calls.
_recent_webhook_deliveries: dict[str, datetime] = {}
_WEBHOOK_DEDUP_WINDOW = timedelta(seconds=15)


def _is_duplicate_webhook_delivery(dedup_key: str) -> bool:
    now = datetime.utcnow()
    if len(_recent_webhook_deliveries) > 2000:
        cutoff = now - _WEBHOOK_DEDUP_WINDOW
        for key, seen_at in list(_recent_webhook_deliveries.items()):
            if seen_at < cutoff:
                del _recent_webhook_deliveries[key]
    last_seen = _recent_webhook_deliveries.get(dedup_key)
    _recent_webhook_deliveries[dedup_key] = now
    return last_seen is not None and (now - last_seen) < _WEBHOOK_DEDUP_WINDOW


async def _get_or_open_session(
    db: AsyncSession,
    session_key: str,
    source: str,
    user_id: int,
    media_id: int,
) -> PlaybackSession:
    """Tolerates a concurrent open of the same session_key racing with this
    one - two webhook events for a session that doesn't exist yet (Jellyfin/
    Emby's PlaybackStart and its first PlaybackProgress, sent back to back)
    can both SELECT nothing and both try to INSERT. Same recovery as
    create_media_safely: add()+flush() inside a savepoint, then on
    IntegrityError re-select and return the row the winner created, so the
    loser applies its own state/progress update on top instead of 500ing
    (#392)."""
    result = await db.execute(
        select(PlaybackSession).where(PlaybackSession.session_key == session_key)
    )
    session = result.scalar_one_or_none()
    if session:
        return session
    session = PlaybackSession(
        session_key=session_key,
        source=source,
        user_id=user_id,
        media_id=media_id,
        progress_percent=0.0,
        progress_seconds=0,
        started_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    try:
        # add() happens *inside* the savepoint, not before it - see
        # create_media_safely's comment on why that ordering matters.
        async with db.begin_nested():
            db.add(session)
            await db.flush()
    except IntegrityError:
        result = await db.execute(
            select(PlaybackSession).where(PlaybackSession.session_key == session_key)
        )
        existing = result.scalar_one_or_none()
        if not existing:
            raise
        return existing
    return session


async def _close_session(db: AsyncSession, session_key: str) -> Optional[PlaybackSession]:
    result = await db.execute(
        select(PlaybackSession).where(PlaybackSession.session_key == session_key)
    )
    session = result.scalar_one_or_none()
    if session:
        await db.delete(session)
    return session


def _episode_for_progress(
    media_list: list["Media"], progress_percent: float, progress_seconds: int
) -> tuple["Media", float, int]:
    """Picks which episode of a multi-episode file (see #138) to treat as
    'currently playing', dividing the combined file's runtime evenly across
    its N episodes - e.g. episode 1 for the first third, episode 2 for the
    next third, episode 3 for the rest. Returns that episode plus its
    progress re-normalized to that episode's own segment (0..1, seconds) so
    the now-playing bar shows a coherent 0->100% per episode instead of
    jumping straight to ~33%/66% then resetting when the episode changes."""
    n = len(media_list)
    pct = progress_percent or 0.0
    idx = min(n - 1, max(0, int(pct * n)))
    segment_pct = max(0.0, min(1.0, pct * n - idx))
    segment_seconds = int(segment_pct * (progress_seconds / pct) / n) if pct > 0 else 0
    return media_list[idx], segment_pct, segment_seconds


async def _update_playback_progress(
    db: AsyncSession,
    user_id: int,
    media_id: int,
    progress_percent: float,
    progress_seconds: int,
) -> None:
    """Updates persistent in-progress state (Continue Watching)."""
    # Don't track progress below 5% or above 90% (those are handled by scrobble)
    if progress_percent < 0.05 or progress_percent >= 0.90:
        # If we already have progress and it's now outside the range, delete it
        await db.execute(
            delete(PlaybackProgress).where(
                PlaybackProgress.user_id == user_id,
                PlaybackProgress.media_id == media_id
            )
        )
        return

    result = await db.execute(
        select(PlaybackProgress).where(
            PlaybackProgress.user_id == user_id,
            PlaybackProgress.media_id == media_id
        )
    )
    progress = result.scalar_one_or_none()
    if progress:
        progress.progress_percent = progress_percent
        progress.progress_seconds = progress_seconds
        progress.updated_at = datetime.utcnow()
    else:
        db.add(PlaybackProgress(
            user_id=user_id,
            media_id=media_id,
            progress_percent=progress_percent,
            progress_seconds=progress_seconds,
            updated_at=datetime.utcnow(),
        ))


async def _write_watch_event(
    db: AsyncSession,
    user_id: int,
    media_id: int,
    progress_percent: float,
    progress_seconds: int,
    completed: bool,
    window_minutes: int = DEFAULT_DEDUP_WINDOW_MINUTES,
) -> bool:
    """Returns False only when this call was consumed as a push-watched echo
    (see the _recently_pushed_watched comment above) - True in every other
    case, including the "already have a recent one" duplicate branch below
    and the plain progress-update branch, since both are real, non-echo
    events. A caller looping over multiple media in one webhook (a
    multi-episode file) uses this to also skip forwarding an echoed row on as
    a scrobble "stop" - an echo is not a play, nothing should leave the
    building for it (#369)."""
    if completed:
        # Echo of a mark-watched call Scrob itself just pushed to this server
        # (see the _recently_pushed_watched comment above) - not a real play.
        if watch_echo.consume_recently_pushed_watched(user_id, media_id):
            return False

        # A single completed viewing is often reported by more than one webhook
        # event for the same session (e.g. Plex sends both `media.scrobble` at
        # ~90% and `media.stop` when the session actually closes) — without this
        # guard each one adds its own WatchEvent row. Also catches the same play
        # arriving again from a different source within the user's configured
        # window (#390), since find_duplicate_watch_event doesn't care which
        # source either row came from.
        now = datetime.utcnow()
        if await find_duplicate_watch_event(db, user_id, media_id, now, window_minutes) is not None:
            return True
        event = WatchEvent(
            user_id=user_id,
            media_id=media_id,
            watched_at=now,
            progress_seconds=progress_seconds,
            progress_percent=1.0,
            completed=True,
            play_count=1,
            # watched_at is this server's receipt time, not the media server's own
            # record of the play — provisional until an authoritative sync (e.g.
            # Plex's history backfill) confirms/corrects it. See GitHub #135.
            provisional=True,
        )
        db.add(event)
        # Remove any in-progress marker since it's now done
        await db.execute(
            delete(PlaybackProgress).where(
                PlaybackProgress.user_id == user_id,
                PlaybackProgress.media_id == media_id
            )
        )
        await db.flush()
        await record_rewatch_progress(db, user_id, media_id, event.id)
        return True
    else:
        # Just update in-progress state, don't add to WatchEvent (History)
        await _update_playback_progress(db, user_id, media_id, progress_percent, progress_seconds)
        return True


async def _write_completed_events_and_filter_echoes(
    db: AsyncSession, user_id: int, media_list: list["Media"], progress_seconds: int,
    window_minutes: int = DEFAULT_DEDUP_WINDOW_MINUTES,
) -> list["Media"]:
    """Writes a completed WatchEvent for each item in media_list (a Jellyfin/
    Emby "mark played" webhook can carry more than one for a multi-episode
    file), returning only the ones that weren't push-watched echoes - see
    _write_watch_event's docstring. Every mark-played/TogglePlayed handler
    below scrobbles "stop" onward for whatever this returns, not for
    media_list itself, so an echoed row - not a real play - never reaches
    Trakt/MDBList/Simkl/Bingebase either (#369)."""
    return [
        m for m in media_list
        if await _write_watch_event(db, user_id, m.id, 1.0, progress_seconds, True, window_minutes)
    ]


async def _handle_unwatch_toggle(db: AsyncSession, user_id: int, media: Media) -> bool:
    """Server-reported "mark unwatched" for one item - Jellyfin's webhook
    plugin reports this via UserDataSaved/TogglePlayed, Emby's via its own
    MarkUnplayed event (see the handling for each below). While a rewatch is
    active for the media's show, this only undoes
    that episode's progress on the current cycle - real watch history is left
    untouched either way. Without an active rewatch, it removes all watch
    history for the item, matching this connection's normal bidirectional
    watched-status sync.

    Returns whether any row was actually deleted - callers use this to skip
    re-pushing the "unwatched" state out when this was a no-op (already
    unwatched). Without it, two two-way-sync connections can ping-pong: A's
    webhook pushes unwatched to B (excluding A), B's own webhook fires back
    reporting the same already-applied unwatch, and - since that inbound
    connection is B, not A - a naive re-push would go back out to A too,
    forever (see #190).
    """
    active_rewatch = None
    if media.media_type == MediaType.episode and media.show_id:
        active_rewatch = await get_active_rewatch(db, user_id, media.show_id)
    if active_rewatch:
        result = await db.execute(
            delete(RewatchProgress).where(
                RewatchProgress.rewatch_id == active_rewatch.id,
                RewatchProgress.media_id == media.id,
            )
        )
    else:
        result = await db.execute(
            delete(WatchEvent).where(
                WatchEvent.user_id == user_id,
                WatchEvent.media_id == media.id,
            )
        )
    return bool(result.rowcount)


# ── Jellyfin ───────────────────────────────────────────────────────────────────


async def _resolve_tvdb_episode_to_tmdb_position(
    db: AsyncSession, show: Show, season_number: int, episode_number: int,
    tmdb_api_key: str | None, tvdb_api_key: str | None,
) -> tuple[int, int] | None:
    """(tmdb_season_number, tmdb_episode_number) for a TVDB-native (season,
    episode) position reported by a Jellyfin/Emby webhook (#162).

    Jellyfin's own SeasonNumber/EpisodeNumber are whatever its metadata
    provider assigns (TheTVDB, for most anime libraries), not necessarily
    TMDB's numbering - for a show where the two disagree, matching/creating
    a Media row directly off these raw numbers produces a second, divergent
    row for an episode that already has a canonical TMDB-numbered one (from
    Trakt import, or any other TMDB-native tracking path).

    Checks the existing mapping first (the common case, a cheap indexed
    lookup); only falls back to computing it on demand (one extra TMDB+TVDB
    season fetch, cached in EpisodeOrderMapping from then on) the first time
    this show/season combination is seen. Returns None - the caller falls
    through to the existing raw-number behavior unchanged - if there's no
    TVDB id, no TVDB key configured, or the position genuinely doesn't exist
    on TMDB's side (real TVDB-only content, #101).

    Never raises - same contract as enrichment.resolve_tvdb_fallback: an enrichment/
    identity nicety failing must not fail the webhook.
    """
    if not (show.tvdb_id and tmdb_api_key and tvdb_api_key):
        return None
    try:
        mapping = await get_mapping_by_tvdb_position(db, show.tmdb_id, season_number, episode_number)
        if mapping:
            return mapping.tmdb_season_number, mapping.tmdb_episode_number

        new_mappings = await ensure_episode_order_mapping_for_season(
            db, show, season_number, tmdb_api_key, tvdb_api_key
        )
        if not new_mappings:
            return None

        # A show's next scrobble after this resolves the mapping is exactly
        # when a previously-mistracked episode (recorded under the old raw-
        # number behavior, before this show's mapping was ever computed) can
        # finally be detected and merged - not just future episodes going
        # forward from here.
        try:
            await reconcile_divergent_episode_media(db, show, season_number=season_number)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Reconciliation failed for show=%s season=%s", show.id, season_number
            )

        match = next(
            (m for m in new_mappings if m.tvdb_season_number == season_number and m.tvdb_episode_number == episode_number),
            None,
        )
        return (match.tmdb_season_number, match.tmdb_episode_number) if match else None
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "TVDB episode position resolution failed for show=%s season=%s episode=%s",
            show.id, season_number, episode_number,
        )
        return None


async def _translate_plex_tvdb_episode_position(
    data: dict, db: AsyncSession, series_tmdb_id: int | None,
    user_id: int | None, tmdb_api_key: str | None,
) -> None:
    """#335: when the user has explicitly put this show on TVDB (aired) episode
    order, Plex reports its TVDB-native (season, episode) numbers. Rewrite them
    in-place to the canonical TMDB position before find_or_create_media_plex's
    raw-number match/create, the same translation find_or_create_media_jellyfin
    does for #162.

    Unlike the Jellyfin path this is gated on the explicit per-show order
    preference: Plex defaults to TMDB numbering, so a show still on the default
    must never be touched. No-op (and never raises) if anything needed is
    missing or the position genuinely doesn't exist on TMDB's side.
    """
    if not (
        user_id
        and series_tmdb_id
        and data.get("media_type") == "episode"
        and not data.get("tmdb_id")
        and data.get("season_number") is not None
        and data.get("episode_number") is not None
    ):
        return
    try:
        order_pref = await get_episode_order(db, user_id, series_tmdb_id)
        if not order_pref or order_pref.episode_order != "tvdb":
            return
        show_row = (
            await db.execute(select(Show).where(Show.tmdb_id == series_tmdb_id))
        ).scalar_one_or_none()
        if not show_row or not show_row.tvdb_id:
            return
        _, tvdb_api_key, _ = await enrichment.resolve_tvdb_fallback(db, show_row, user_id)
        canonical = await _resolve_tvdb_episode_to_tmdb_position(
            db, show_row, data["season_number"], data["episode_number"],
            tmdb_api_key, tvdb_api_key,
        )
        if canonical:
            data["season_number"], data["episode_number"] = canonical
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Plex TVDB episode position translation failed (series_tmdb_id=%s s=%s e=%s)",
            series_tmdb_id, data.get("season_number"), data.get("episode_number"),
        )


def _parse_year(value) -> int | None:
    """A four-digit year from an int, a bare "2016", or a "2016-05-13" date."""
    if value is None:
        return None
    text = str(value)[:4]
    return int(text) if text.isdigit() and len(text) == 4 else None


def _pick_show_by_year(shows: list[Show], year: int | None) -> Show | None:
    """From same-title candidates, the one whose first_air_date year is within
    a year of `year`. Falls back to the first candidate when there's nothing
    to match on or nothing lines up - a guess is still better than None, and
    it's what the code did before the year check existed (#373)."""
    shows = list(shows)
    if not shows or len(shows) == 1 or year is None:
        return shows[0] if shows else None
    for show in shows:
        show_year = _parse_year(show.first_air_date)
        if show_year is not None and abs(show_year - year) <= 1:
            return show
    return shows[0]


async def _resolve_show_for_episode(
    data: dict, db: AsyncSession, api_key: str = None
) -> tuple[Show | None, int | None]:
    """(show, series_tmdb_id) for a parsed Jellyfin/Emby webhook payload.
    Falls back to a series_name lookup (local Show table, then TMDB search)
    when the payload carries no series_tmdb_id - the flat plugin format never
    has one, and Emby's nested webhooks omit it too (#192). When two shows
    share a title, the payload's series_year picks between them (#373)."""
    show = None
    series_tmdb_id = int(data["series_tmdb_id"]) if data.get("series_tmdb_id") else None

    if data["media_type"] == "episode" and not series_tmdb_id and data.get("series_name"):
        # Flat format: no series_tmdb_id — try local Show table first, then TMDB search
        series_year = _parse_year(data.get("series_year"))
        local_result = await db.execute(
            select(Show).where(Show.title.ilike(data["series_name"]))
        )
        local_show = _pick_show_by_year(local_result.scalars().all(), series_year)
        if local_show:
            series_tmdb_id = local_show.tmdb_id
        else:
            try:
                res = None
                if series_year:
                    res = await tmdb.search_shows(data["series_name"], year=series_year, api_key=api_key)
                if not res or not res.get("results"):
                    res = await tmdb.search_shows(data["series_name"], api_key=api_key)
                if res.get("results"):
                    series_tmdb_id = res["results"][0]["id"]
            except Exception:
                pass

    if data["media_type"] == "episode" and series_tmdb_id:
        try:
            show = await show_metadata.find_or_create_show(db, series_tmdb_id, api_key)
        except Exception:
            pass

    return show, series_tmdb_id


async def find_or_create_media_jellyfin(
    data: dict, db: AsyncSession, api_key: str = None, user_id: int | None = None
) -> Media | None:
    # 1. Match by source item ID via CollectionFile (fastest path post-sync).
    # This function is shared by both Jellyfin and Emby webhooks (they're the
    # same REST API) - matching only CollectionSource.jellyfin meant every
    # Emby-sourced item always missed this fast path and fell through to the
    # slower show/tmdb_id resolution below, even for episodes already synced
    # and correctly linked (contributing to #192).
    # A multi-episode file (see #138) has several CollectionFiles sharing this
    # source_id, one per episode - disambiguate by episode_number whenever the
    # caller knows which one it wants (find_or_create_media_jellyfin_multi sets
    # it per sub-call), so this doesn't just grab an arbitrary sibling episode.
    if data["jellyfin_id"]:
        query = (
            select(Media)
            .join(Collection, Collection.media_id == Media.id)
            .join(CollectionFile, CollectionFile.collection_id == Collection.id)
            .where(CollectionFile.source.in_((CollectionSource.jellyfin, CollectionSource.emby)))
            .where(CollectionFile.source_id == data["jellyfin_id"])
        )
        if data["media_type"] == "episode" and data.get("episode_number") is not None:
            query = query.where(Media.episode_number == data["episode_number"])
        result = await db.execute(query)
        media = result.scalars().first()
        if media:
            # This row may predate the series_name/CollectionSource.emby fixes
            # above (or the show lookup simply failed at creation time) and
            # still be missing show linkage - without this, Now Playing keeps
            # showing the bare episode title forever for that item even after
            # upgrading, since this fast path would otherwise return it as-is
            # on every future webhook too (#192 follow-up).
            if media.media_type == MediaType.episode and media.show_id is None:
                show, series_tmdb_id = await _resolve_show_for_episode(data, db, api_key)
                if show:
                    media.show_id = show.id
                    tvdb_id, tvdb_api_key, tvdb_lang = await enrichment.resolve_tvdb_fallback(db, show, user_id)
                    await enrich_media(
                        media, api_key=api_key, series_tmdb_id=series_tmdb_id,
                        tvdb_id=tvdb_id, tvdb_api_key=tvdb_api_key, tvdb_lang=tvdb_lang,
                    )
            return media

    # Resolve show for episode dedup and enrichment
    show, series_tmdb_id = await _resolve_show_for_episode(data, db, api_key)

    # 2. Match by TMDB ID (handles rapid webhook events before first sync, or items
    #    already added via another source / manually — prevents duplicate media rows)
    if data["tmdb_id"]:
        result = await db.execute(
            select(Media).where(
                Media.tmdb_id == int(data["tmdb_id"]),
                Media.media_type == MediaType(data["media_type"]),
            )
        )
        media = result.scalars().first()
        if media:
            if media.media_type == MediaType.episode and media.show_id is None and show:
                media.show_id = show.id
                tvdb_id, tvdb_api_key, tvdb_lang = await enrichment.resolve_tvdb_fallback(db, show, user_id)
                await enrich_media(
                    media, api_key=api_key, series_tmdb_id=series_tmdb_id,
                    tvdb_id=tvdb_id, tvdb_api_key=tvdb_api_key, tvdb_lang=tvdb_lang,
                )
            return media

    # 2b. Movie matching by title + year if TMDB ID is missing
    if data["media_type"] == "movie" and not data["tmdb_id"]:
        # Try local match first to avoid redundant TMDB search
        local_q = select(Media).where(
            Media.media_type == MediaType.movie,
            Media.title.ilike(data["title"]),
        )
        if data.get("year"):
            local_q = local_q.where(Media.release_date.like(f"{data['year']}%"))

        media = (await db.execute(local_q)).scalars().first()
        if media:
            return media

        # Try TMDB search to find the real ID
        try:
            search_res = await tmdb.search_movies(data["title"], year=data.get("year"), api_key=api_key)
            if search_res.get("results"):
                tmdb_movie = search_res["results"][0]
                data["tmdb_id"] = str(tmdb_movie["id"])
                # Check again with the new TMDB ID
                result = await db.execute(
                    select(Media).where(
                        Media.tmdb_id == tmdb_movie["id"],
                        Media.media_type == MediaType.movie,
                    )
                )
                media = result.scalars().first()
                if media:
                    return media
        except Exception:
            pass

    # 2c. Translate a TVDB-native (season, episode) position to the canonical
    #     TMDB one before the raw-number match below (#162) - Jellyfin's own
    #     SeasonNumber/EpisodeNumber follow whatever metadata provider it's
    #     using (TheTVDB, for most anime libraries), which can diverge
    #     entirely from TMDB's structure for the same show. Matching/creating
    #     directly off the raw numbers would produce a second Media row for
    #     an episode that already has a canonical TMDB-numbered one from
    #     Trakt import or any other TMDB-native tracking path.
    if show and data["media_type"] == "episode" and data["season_number"] is not None and data["episode_number"] is not None:
        _, tvdb_api_key, _ = await enrichment.resolve_tvdb_fallback(db, show, user_id)
        canonical_position = await _resolve_tvdb_episode_to_tmdb_position(
            db, show, data["season_number"], data["episode_number"], api_key, tvdb_api_key,
        )
        if canonical_position:
            data["season_number"], data["episode_number"] = canonical_position

    # 3. Match by (show_id, season_number, episode_number) — catches sync-created rows
    #    when the Jellyfin item's TMDB ID is missing or doesn't match
    if show and data["season_number"] is not None and data["episode_number"] is not None:
        result = await db.execute(
            select(Media).where(
                Media.media_type == MediaType.episode,
                Media.show_id == show.id,
                Media.season_number == data["season_number"],
                Media.episode_number == data["episode_number"],
            )
        )
        media = result.scalars().first()
        if media:
            return media

    # Don't create a row for an episode we can't identify at all — it can never
    # be enriched or matched back to a real episode, and would inflate collection counts.
    if data["media_type"] == "episode" and data["season_number"] is None and data["episode_number"] is None and not data["tmdb_id"]:
        print(f"  Skipping unidentifiable episode '{data['title']}' (no season/episode/tmdb_id)")
        return None

    media, _created = await create_media_safely(
        db,
        int(data["tmdb_id"]) if data["tmdb_id"] else None,
        MediaType(data["media_type"]),
        title=data["title"],
        season_number=data["season_number"],
        episode_number=data["episode_number"],
        show_id=show.id if show else None,
    )
    if show and series_tmdb_id:
        tvdb_id, tvdb_api_key, tvdb_lang = await enrichment.resolve_tvdb_fallback(db, show, user_id)
        media = await enrich_media_safely(
            db, media, api_key=api_key, series_tmdb_id=series_tmdb_id,
            tvdb_id=tvdb_id, tvdb_api_key=tvdb_api_key, tvdb_lang=tvdb_lang,
        )
    else:
        await enrich_media(media, api_key=api_key)
    return media


async def find_or_create_media_jellyfin_multi(
    data: dict, db: AsyncSession, api_key: str = None, user_id: int | None = None
) -> list[Media]:
    """Resolves every episode a Jellyfin/Emby webhook event covers - almost
    always just one, but a multi-episode file (IndexNumber..IndexNumberEnd,
    see #138) fires a single webhook event for the whole combined file.
    Expands into one find_or_create_media_jellyfin() call per episode number
    in the span so scrobbling/marking-watched applies to every episode, not
    just the first (the gap bittom reported in #138)."""
    start = data.get("episode_number")
    end = data.get("episode_number_end")
    if data["media_type"] != "episode" or start is None or end is None or end <= start:
        media = await find_or_create_media_jellyfin(data, db, api_key=api_key, user_id=user_id)
        return [media] if media else []

    results: list[Media] = []
    for ep in range(start, end + 1):
        media = await find_or_create_media_jellyfin(dict(data, episode_number=ep), db, api_key=api_key, user_id=user_id)
        if media:
            results.append(media)
    return results


async def _handle_jellyfin_webhook(request: Request, db: AsyncSession, api_key: str, connection_id: int | None = None):
    user_result = await db.execute(select(User).where(User.api_key == api_key))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    body = await request.body()
    if not body:
        return {"status": "ignored", "reason": "empty body"}

    try:
        payload = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid JSON"}

    data = webhook_payloads.parse_jellyfin_payload(payload)
    if not data:
        return {"status": "ignored"}

    notification_type = data["notification_type"]

    if connection_id is not None:
        conn = await _get_connection_by_id(db, user.id, connection_id)
    else:
        conn = await _get_oldest_connection(db, user.id, "jellyfin")

    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    settings = settings_result.scalar_one_or_none()
    window_minutes = dedup_window_from_settings(settings)
    tmdb_key = await settings_store.get_effective_tmdb_key(db, settings)

    # Almost always one episode; a combined multi-episode file (see #138)
    # resolves to several. "Now playing"/live progress scrobbles below stay
    # keyed on media (the first episode) — a single stream position doesn't
    # map onto per-sub-episode progress. Collection and completed-watch
    # events loop media_list so every episode in the file is covered.
    media_list = await find_or_create_media_jellyfin_multi(data, db, api_key=tmdb_key, user_id=user.id)
    session_key = f"jellyfin:{user.id}:{data['session_id']}"

    if not media_list:
        return {"status": "ignored", "reason": "episode could not be identified (no season/episode/tmdb_id)"}
    media = media_list[0]

    if notification_type in _RUNTIME_BACKFILL_EVENTS:
        await _backfill_jellyfin_runtimes(db, media_list, data, tmdb_key)

    # ItemAdded fires once, right when a title lands in the library — often
    # long before anyone plays it, so "add to collection" can't wait on a
    # playback event to piggy-back on (see #129: an added-but-unwatched item
    # never showed up in Scrob until it was played or a full sync ran).
    if notification_type in ("PlaybackStart", "PlaybackProgress", "PlaybackStop", "MarkPlayed", "ItemAdded", "playback.start", "playback.progress", "playback.stop", "item.markplayed"):
        if not conn or conn.sync_collection:
            allow_collection = True
            jellyfin_id = data.get("jellyfin_id")
            if jellyfin_id and conn:
                sel_result = await db.execute(
                    select(JellyfinLibrarySelection).where(JellyfinLibrarySelection.connection_id == conn.id)
                )
                selected_ids = {row.library_id for row in sel_result.scalars().all()}
                if selected_ids:
                    import core.jellyfin as jellyfin_client
                    # user_id is required here - Jellyfin's admin-only Items/{id}
                    # endpoint (no Users/ prefix) throws server-side for a
                    # non-admin token (see #179).
                    item_data = await jellyfin_client.get_item(conn.url, conn.token, jellyfin_id, user_id=conn.server_user_id)
                    library_id: str | None = None
                    if item_data:
                        if item_data.get("Type") == "Episode":
                            series_id = item_data.get("SeriesId")
                            if series_id:
                                series_data = await jellyfin_client.get_item(conn.url, conn.token, series_id, user_id=conn.server_user_id)
                                library_id = (series_data or {}).get("ParentId")
                        else:
                            library_id = item_data.get("ParentId")
                    allow_collection = library_id in selected_ids if library_id else True

            if allow_collection:
                created_file = False
                for m in media_list:
                    created_file |= await _ensure_collection_entry(
                        db, user.id, m.id, CollectionSource.jellyfin, data["jellyfin_id"], data.get("quality"),
                        connection_id=conn.id if conn else None,
                    )
                # Needed here specifically for ItemAdded: unlike the playback
                # notification types, nothing later in this request commits —
                # without this, the insert is silently rolled back when the
                # request's session closes (see #129 followup).
                await db.commit()
                if created_file and notification_type == "ItemAdded":
                    await _push_watched_for_new_item(db, user.id, conn, data["jellyfin_id"], media_list)

    # See #129 — the counterpart to ItemAdded above: a title removed from the
    # Jellyfin library should leave the user's collection too, rather than
    # only being cleaned up on the next full sync.
    elif notification_type == "ItemDeleted":
        if (not conn or conn.sync_collection) and data.get("jellyfin_id"):
            for m in media_list:
                await _remove_collection_entry(
                    db, user.id, m.id, CollectionSource.jellyfin, data["jellyfin_id"],
                )
            await db.commit()

    if notification_type in ("PlaybackStart", "playback.start"):
        if not conn or conn.sync_playback:
            # Multi-episode file: show whichever episode the file-wide progress
            # currently falls into (see #138 follow-up), not always the first.
            current_episode, _, _ = _episode_for_progress(media_list, data["progress_percent"], data["progress_seconds"])
            session = await _get_or_open_session(db, session_key, "jellyfin", user.id, current_episode.id)
            session.media_id = current_episode.id
            session.state = "playing"
            await playback_sessions._commit_playback_session_update(db, settings, *media_list)
        await scrobble_delivery.forward(settings, media, "start", data["progress_percent"], db=db)

    elif notification_type in ("PlaybackProgress", "playback.progress"):
        if not conn or conn.sync_playback:
            current_episode, segment_pct, segment_seconds = _episode_for_progress(
                media_list, data["progress_percent"], data["progress_seconds"]
            )
            session = await _get_or_open_session(db, session_key, "jellyfin", user.id, current_episode.id)
            session.media_id = current_episode.id
            session.state = "paused" if data["is_paused"] else "playing"
            session.progress_percent = segment_pct
            session.progress_seconds = segment_seconds
            session.updated_at = datetime.utcnow()
            await playback_sessions._commit_playback_session_update(db, settings, *media_list)
        if data["is_paused"]:
            await scrobble_delivery.forward(settings, media, "pause", data["progress_percent"], db=db)

    elif notification_type in ("PlaybackStop", "playback.stop"):
        # sync_watched and sync_playback are independent toggles - watched status
        # must sync even when continue-watching tracking is off, and _close_session's
        # pending delete needs committing either way (was only ever reached when
        # sync_playback was on, leaving the closed session uncommitted otherwise).
        session = await _close_session(db, session_key)
        progress_percent = data["progress_percent"] or (session.progress_percent if session else 0.0)
        progress_seconds = data["progress_seconds"] or (session.progress_seconds if session else 0)
        if data.get("played_to_completion"):
            # Trust this over the computed ratio above - an Emby auto-play
            # transition can zero out position/runtime for the item that just
            # finished before this stop event is built, silently dropping a
            # genuine completion under the 5% floor below otherwise (#206).
            progress_percent = 1.0
        if (not conn or conn.sync_watched) and progress_percent > 0.05:
            for m in media_list:
                await _write_watch_event(db, user.id, m.id, progress_percent, progress_seconds, progress_percent >= 0.90, window_minutes)
        await db.commit()
        for m in media_list:
            await scrobble_delivery.forward(settings, m, "stop", progress_percent, db=db)

    elif notification_type in ("MarkPlayed", "item.markplayed"):
        # Same reasoning as PlaybackStop above: _close_session's pending delete
        # needs committing regardless of sync_watched, not only when it fires.
        await _close_session(db, session_key)
        # A row _write_watch_event reports back as an echo of Scrob's own
        # mark-watched push is not a real play - forwarding it as a scrobble
        # "stop" anyway used to write a spurious now-dated play to every
        # connected Trakt/MDBList/Simkl/Bingebase (#369).
        non_echo_media = media_list
        if not conn or conn.sync_watched:
            non_echo_media = await _write_completed_events_and_filter_echoes(
                db, user.id, media_list, data["progress_seconds"], window_minutes
            )
        await db.commit()
        for m in non_echo_media:
            await scrobble_delivery.forward(settings, m, "stop", 1.0, db=db)

    elif notification_type == "UserDataSaved":
        # Jellyfin's official Webhook plugin has no dedicated "mark played"
        # event — manually toggling watched/unwatched (and rating changes,
        # favorites, imports, and every playback tick) all raise this same
        # UserDataSaved notification. SaveReason is the only way to tell a
        # manual watched-state toggle apart from the rest.
        if data.get("save_reason") == "TogglePlayed" and (not conn or conn.sync_watched):
            played = data.get("played")
            if played:
                await _close_session(db, session_key)
                # See the matching comment in the MarkPlayed branch above (#369).
                non_echo_media = await _write_completed_events_and_filter_echoes(
                    db, user.id, media_list, data["progress_seconds"], window_minutes
                )
                await db.commit()
                for m in non_echo_media:
                    await scrobble_delivery.forward(settings, m, "stop", 1.0, db=db)
            elif played is False:
                changed_ids = [
                    m.id for m in media_list
                    if await _handle_unwatch_toggle(db, user.id, m)
                ]
                await db.commit()
                if changed_ids:
                    from core import watch_delivery
                    # exclude_connection_id: this unwatch was itself reported BY this
                    # connection - pushing it right back to the same server is what
                    # causes the infinite webhook loop in #190. Still propagates to
                    # any OTHER connection with push_watched enabled. changed_ids
                    # (rather than every m in media_list) additionally skips the
                    # push entirely when nothing was actually deleted - closing the
                    # multi-connection ping-pong case exclude_connection_id alone
                    # doesn't cover (see _handle_unwatch_toggle's docstring).
                    await watch_delivery.push_watch_state(
                        db, user.id, changed_ids, watched=False,
                        exclude_connection_id=conn.id if conn else None,
                    )

    return {"status": "ok", "event": notification_type, "title": data["title"]}


@router.post("/jellyfin")
async def jellyfin_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: str = Query(..., description="AnyList user API key"),
):
    return await _handle_jellyfin_webhook(request, db, api_key)


@router.post("/jellyfin/scrobble/{connection_id}")
async def jellyfin_scrobble_webhook(
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: str = Query(..., description="AnyList user API key"),
):
    return await _handle_jellyfin_scrobble_webhook(request, db, api_key, connection_id, source="jellyfin")


@router.post("/jellyfin/{connection_id}")
async def jellyfin_webhook_connection(
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: str = Query(..., description="AnyList user API key"),
):
    return await _handle_jellyfin_webhook(request, db, api_key, connection_id)


# ── Emby ───────────────────────────────────────────────────────────────────────

async def _handle_emby_webhook(request: Request, db: AsyncSession, api_key: str, connection_id: int | None = None):
    user_result = await db.execute(select(User).where(User.api_key == api_key))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    body = await request.body()
    if not body:
        return {"status": "ignored", "reason": "empty body"}

    # The Emby Webhooks plugin's default "Request content type" is
    # multipart/form-data with the JSON payload in a form field named
    # "data", not a raw JSON body (#295) - request.json() throws on that
    # (the body starts with a MIME boundary, not "{"), so every Emby
    # webhook event was silently swallowed by the except below and
    # answered 200 OK having done nothing, not just mark-unwatched.
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        try:
            form = await request.form()
            raw = form.get("data")
            payload = json.loads(raw) if raw else None
        except Exception:
            payload = None
        if payload is None:
            return {"status": "ignored", "reason": "invalid multipart payload"}
    else:
        try:
            payload = await request.json()
        except Exception:
            return {"status": "ignored", "reason": "invalid JSON"}

    data = webhook_payloads.parse_jellyfin_payload(payload)
    if not data:
        return {"status": "ignored"}

    notification_type = data["notification_type"]

    if connection_id is not None:
        conn = await _get_connection_by_id(db, user.id, connection_id)
    else:
        conn = await _get_oldest_connection(db, user.id, "emby")

    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    settings = settings_result.scalar_one_or_none()
    window_minutes = dedup_window_from_settings(settings)
    tmdb_key = await settings_store.get_effective_tmdb_key(db, settings)

    # See the matching comment in _handle_jellyfin_webhook (#138 follow-up).
    media_list = await find_or_create_media_jellyfin_multi(data, db, api_key=tmdb_key, user_id=user.id)
    session_key = f"emby:{user.id}:{data['session_id']}"

    if not media_list:
        return {"status": "ignored", "reason": "episode could not be identified (no season/episode/tmdb_id)"}
    media = media_list[0]

    if notification_type in _RUNTIME_BACKFILL_EVENTS:
        await _backfill_jellyfin_runtimes(db, media_list, data, tmdb_key)

    # See the matching comment in _handle_jellyfin_webhook (#129). Emby's own
    # plugin reports these as dotted-lowercase names (confirmed live, #295) -
    # "library.new" here is its equivalent of Jellyfin's "ItemAdded", kept
    # alongside the Jellyfin-style names since Emby's Webhooks plugin has used
    # both conventions across versions.
    if notification_type in ("PlaybackStart", "PlaybackProgress", "PlaybackStop", "MarkPlayed", "ItemAdded", "playback.start", "playback.progress", "playback.stop", "item.markplayed", "library.new"):
        if not conn or conn.sync_collection:
            allow_collection = True
            emby_item_id = data.get("jellyfin_id")
            if emby_item_id and conn:
                sel_result = await db.execute(
                    select(EmbyLibrarySelection).where(EmbyLibrarySelection.connection_id == conn.id)
                )
                selected_ids = {row.library_id for row in sel_result.scalars().all()}
                if selected_ids:
                    import core.emby as emby_client
                    # user_id is required here - same reasoning as the Jellyfin
                    # branch above (see #179).
                    item_data = await emby_client.get_item(conn.url, conn.token, emby_item_id, user_id=conn.server_user_id)
                    library_id: str | None = None
                    if item_data:
                        if item_data.get("Type") == "Episode":
                            series_id = item_data.get("SeriesId")
                            if series_id:
                                series_data = await emby_client.get_item(conn.url, conn.token, series_id, user_id=conn.server_user_id)
                                library_id = (series_data or {}).get("ParentId")
                        else:
                            library_id = item_data.get("ParentId")
                    allow_collection = library_id in selected_ids if library_id else True

            if allow_collection:
                created_file = False
                for m in media_list:
                    created_file |= await _ensure_collection_entry(
                        db, user.id, m.id, CollectionSource.emby, data["jellyfin_id"], data.get("quality"),
                        connection_id=conn.id if conn else None,
                    )
                # See the matching comment in _handle_jellyfin_webhook (#129).
                await db.commit()
                if created_file and notification_type in ("ItemAdded", "library.new"):
                    await _push_watched_for_new_item(db, user.id, conn, data["jellyfin_id"], media_list)

    # See the matching comment in _handle_jellyfin_webhook (#129). "library.deleted"
    # is Emby's dotted-lowercase equivalent of "ItemDeleted" (confirmed live, #295).
    elif notification_type in ("ItemDeleted", "library.deleted"):
        if (not conn or conn.sync_collection) and data.get("jellyfin_id"):
            for m in media_list:
                await _remove_collection_entry(
                    db, user.id, m.id, CollectionSource.emby, data["jellyfin_id"],
                )
            await db.commit()

    if notification_type in ("PlaybackStart", "playback.start"):
        if not conn or conn.sync_playback:
            session = await _get_or_open_session(db, session_key, "emby", user.id, media.id)
            session.state = "playing"
            await playback_sessions._commit_playback_session_update(db, settings, media)
        await scrobble_delivery.forward(settings, media, "start", data["progress_percent"], db=db)

    elif notification_type in ("PlaybackProgress", "playback.progress"):
        if not conn or conn.sync_playback:
            session = await _get_or_open_session(db, session_key, "emby", user.id, media.id)
            session.state = "paused" if data["is_paused"] else "playing"
            session.progress_percent = data["progress_percent"]
            session.progress_seconds = data["progress_seconds"]
            session.updated_at = datetime.utcnow()
            await playback_sessions._commit_playback_session_update(db, settings, media)
        if data["is_paused"]:
            await scrobble_delivery.forward(settings, media, "pause", data["progress_percent"], db=db)

    elif notification_type in ("PlaybackStop", "playback.stop"):
        # sync_watched and sync_playback are independent toggles - watched status
        # must sync even when continue-watching tracking is off, and _close_session's
        # pending delete needs committing either way (was only ever reached when
        # sync_playback was on, leaving the closed session uncommitted otherwise).
        session = await _close_session(db, session_key)
        progress_percent = data["progress_percent"] or (session.progress_percent if session else 0.0)
        progress_seconds = data["progress_seconds"] or (session.progress_seconds if session else 0)
        if data.get("played_to_completion"):
            # See the matching comment in _handle_jellyfin_webhook (#206).
            progress_percent = 1.0
        if (not conn or conn.sync_watched) and progress_percent > 0.05:
            for m in media_list:
                await _write_watch_event(db, user.id, m.id, progress_percent, progress_seconds, progress_percent >= 0.90, window_minutes)
        await db.commit()
        for m in media_list:
            await scrobble_delivery.forward(settings, m, "stop", progress_percent, db=db)

    elif notification_type in ("MarkPlayed", "item.markplayed"):
        # Same reasoning as PlaybackStop above: _close_session's pending delete
        # needs committing regardless of sync_watched, not only when it fires.
        await _close_session(db, session_key)
        # See the matching comment in _handle_jellyfin_webhook's MarkPlayed
        # branch - an echoed row must not scrobble onward either (#369).
        non_echo_media = media_list
        if not conn or conn.sync_watched:
            non_echo_media = await _write_completed_events_and_filter_echoes(
                db, user.id, media_list, data["progress_seconds"], window_minutes
            )
        await db.commit()
        for m in non_echo_media:
            await scrobble_delivery.forward(settings, m, "stop", 1.0, db=db)

    elif notification_type in ("MarkUnplayed", "item.markunplayed"):
        # Emby's webhook plugin reports mark-unwatched as its own distinct
        # event (unlike Jellyfin, which raises a generic UserDataSaved for
        # every watched-state/rating/favorite toggle) - see the matching
        # played-is-False branch in _handle_jellyfin_webhook for the same
        # unwatch-toggle + cross-connection push reasoning (#295).
        if not conn or conn.sync_watched:
            changed_ids = [
                m.id for m in media_list
                if await _handle_unwatch_toggle(db, user.id, m)
            ]
            await db.commit()
            if changed_ids:
                from core import watch_delivery
                await watch_delivery.push_watch_state(
                    db, user.id, changed_ids, watched=False,
                    exclude_connection_id=conn.id if conn else None,
                )

    return {"status": "ok", "event": notification_type, "title": data["title"]}


async def _handle_jellyfin_scrobble_webhook(
    request: Request, db: AsyncSession, api_key: str, connection_id: int, source: str
):
    user_result = await db.execute(select(User).where(User.api_key == api_key))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    body = await request.body()
    if not body:
        return {"status": "ignored", "reason": "empty body"}

    # Same multipart/form-data quirk as _handle_emby_webhook (#295) - Emby's
    # webhook plugin can send this shared scrobble endpoint the same way.
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        try:
            form = await request.form()
            raw = form.get("data")
            payload = json.loads(raw) if raw else None
        except Exception:
            payload = None
        if payload is None:
            return {"status": "ignored", "reason": "invalid multipart payload"}
    else:
        try:
            payload = await request.json()
        except Exception:
            return {"status": "ignored", "reason": "invalid JSON"}

    data = webhook_payloads.parse_jellyfin_payload(payload)
    if not data:
        return {"status": "ignored"}

    conn = await _get_scrobble_connection_by_id(db, user.id, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Scrobble connection not found")

    notification_type = data["notification_type"]

    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    settings = settings_result.scalar_one_or_none()
    window_minutes = dedup_window_from_settings(settings)
    tmdb_key = await settings_store.get_effective_tmdb_key(db, settings)

    # See the matching comment in _handle_jellyfin_webhook (#138 follow-up).
    media_list = await find_or_create_media_jellyfin_multi(data, db, api_key=tmdb_key, user_id=user.id)
    session_key = f"{source}:scrobble:{user.id}:{data['session_id']}"
    # See _duplicated_by_full_connection's docstring (#312) - guards only the
    # outbound scrobble dispatch below, not local session/watch tracking.
    is_duplicate = await _duplicated_by_full_connection(db, source, user.id, data["session_id"])

    if not media_list:
        return {"status": "ignored", "reason": "episode could not be identified (no season/episode/tmdb_id)"}
    media = media_list[0]

    if notification_type in _RUNTIME_BACKFILL_EVENTS:
        await _backfill_jellyfin_runtimes(db, media_list, data, tmdb_key)

    coll_source = CollectionSource.jellyfin if source == "jellyfin" else CollectionSource.emby

    # See the matching comment in _handle_jellyfin_webhook (#129). "library.new"/
    # "library.deleted" are Emby's dotted-lowercase equivalents of Jellyfin's
    # "ItemAdded"/"ItemDeleted" (confirmed live, #295) - this handler is shared
    # by both providers, so both naming conventions need to be matched here.
    if notification_type in ("PlaybackStart", "PlaybackProgress", "PlaybackStop", "MarkPlayed", "ItemAdded", "playback.start", "playback.progress", "playback.stop", "item.markplayed", "library.new"):
        if conn.sync_collection:
            for m in media_list:
                await _ensure_collection_entry(
                    db, user.id, m.id, coll_source, data["jellyfin_id"], data.get("quality"),
                    # `conn` here is a ScrobbleConnection - a different table
                    # and id sequence from media_server_connections, which is
                    # what collection_files.connection_id is FK'd to. A
                    # scrobble-only connection has no media-server row to link,
                    # so leave it NULL (#339).
                    connection_id=None,
                )
            # See the matching comment in _handle_jellyfin_webhook (#129).
            await db.commit()

    elif notification_type in ("ItemDeleted", "library.deleted"):
        if conn.sync_collection and data.get("jellyfin_id"):
            for m in media_list:
                await _remove_collection_entry(
                    db, user.id, m.id, coll_source, data["jellyfin_id"],
                )
            await db.commit()

    if notification_type in ("PlaybackStart", "playback.start"):
        if conn.sync_playback:
            session = await _get_or_open_session(db, session_key, source, user.id, media.id)
            session.state = "playing"
            await playback_sessions._commit_playback_session_update(db, settings, media)
        if not is_duplicate:
            await scrobble_delivery.forward(settings, media, "start", data["progress_percent"], db=db)

    elif notification_type in ("PlaybackProgress", "playback.progress"):
        if conn.sync_playback:
            session = await _get_or_open_session(db, session_key, source, user.id, media.id)
            session.state = "paused" if data["is_paused"] else "playing"
            session.progress_percent = data["progress_percent"]
            session.progress_seconds = data["progress_seconds"]
            session.updated_at = datetime.utcnow()
            await playback_sessions._commit_playback_session_update(db, settings, media)
        if data["is_paused"] and not is_duplicate:
            await scrobble_delivery.forward(settings, media, "pause", data["progress_percent"], db=db)

    elif notification_type in ("PlaybackStop", "playback.stop"):
        # sync_watched and sync_playback are independent toggles - watched status
        # must sync even when continue-watching tracking is off, and _close_session's
        # pending delete needs committing either way (was only ever reached when
        # sync_playback was on, leaving the closed session uncommitted otherwise).
        session = await _close_session(db, session_key)
        progress_percent = data["progress_percent"] or (session.progress_percent if session else 0.0)
        progress_seconds = data["progress_seconds"] or (session.progress_seconds if session else 0)
        if data.get("played_to_completion"):
            # See the matching comment in _handle_jellyfin_webhook (#206).
            progress_percent = 1.0
        if conn.sync_watched and progress_percent > 0.05:
            for m in media_list:
                await _write_watch_event(db, user.id, m.id, progress_percent, progress_seconds, progress_percent >= 0.90, window_minutes)
        await db.commit()
        if not is_duplicate:
            for m in media_list:
                await scrobble_delivery.forward(settings, m, "stop", progress_percent, db=db)

    elif notification_type in ("MarkPlayed", "item.markplayed"):
        # Same reasoning as PlaybackStop above: _close_session's pending delete
        # needs committing regardless of sync_watched, not only when it fires.
        await _close_session(db, session_key)
        # See the matching comment in _handle_jellyfin_webhook's MarkPlayed
        # branch - an echoed row must not scrobble onward either (#369).
        non_echo_media = media_list
        if conn.sync_watched:
            non_echo_media = await _write_completed_events_and_filter_echoes(
                db, user.id, media_list, data["progress_seconds"], window_minutes
            )
        await db.commit()
        if not is_duplicate:
            for m in non_echo_media:
                await scrobble_delivery.forward(settings, m, "stop", 1.0, db=db)

    elif notification_type == "UserDataSaved":
        # Jellyfin's official Webhook plugin has no dedicated "mark played"
        # event - manually toggling watched/unwatched raises this same
        # UserDataSaved notification (see the matching comment in
        # _handle_jellyfin_webhook, #129). Scrobble-only connections never
        # handled this at all, so a manual toggle in Jellyfin/Emby's own UI
        # never propagated for them.
        if data.get("save_reason") == "TogglePlayed" and conn.sync_watched:
            played = data.get("played")
            if played:
                await _close_session(db, session_key)
                # See the matching comment in the MarkPlayed branch above (#369).
                non_echo_media = await _write_completed_events_and_filter_echoes(
                    db, user.id, media_list, data["progress_seconds"], window_minutes
                )
                await db.commit()
                if not is_duplicate:
                    for m in non_echo_media:
                        await scrobble_delivery.forward(settings, m, "stop", 1.0, db=db)
            elif played is False:
                changed_ids = [
                    m.id for m in media_list
                    if await _handle_unwatch_toggle(db, user.id, m)
                ]
                await db.commit()
                if changed_ids:
                    from core import watch_delivery
                    # Unlike _handle_jellyfin_webhook, there's no exclude_connection_id
                    # here - a ScrobbleConnection has no url of its own, so it can't be
                    # matched against push-enabled MediaServerConnections to identify
                    # "the server this came from" (see #190). This only becomes a loop
                    # if the user also has a separate full MediaServerConnection with
                    # push_watched enabled pointing at that same physical server.
                    # changed_ids still helps here too: skips the push when this
                    # delivery was a no-op (already unwatched), same reasoning as
                    # _handle_jellyfin_webhook.
                    await watch_delivery.push_watch_state(db, user.id, changed_ids, watched=False)

    return {"status": "ok", "event": notification_type, "title": data["title"]}


@router.post("/emby")
async def emby_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: str = Query(..., description="AnyList user API key"),
):
    return await _handle_emby_webhook(request, db, api_key)


@router.post("/emby/scrobble/{connection_id}")
async def emby_scrobble_webhook(
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: str = Query(..., description="AnyList user API key"),
):
    return await _handle_jellyfin_scrobble_webhook(request, db, api_key, connection_id, source="emby")


@router.post("/emby/{connection_id}")
async def emby_webhook_connection(
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: str = Query(..., description="AnyList user API key"),
):
    return await _handle_emby_webhook(request, db, api_key, connection_id)


# ── Plex ───────────────────────────────────────────────────────────────────────


async def _ensure_collection_entry(
    db: AsyncSession,
    user_id: int,
    media_id: int,
    source: CollectionSource,
    source_id: str,
    quality: dict = None,
    connection_id: int | None = None,
) -> bool:
    """Ensures a Collection + CollectionFile entry exists for the user, creating or updating as needed.

    Returns True when this call created the CollectionFile (the item is new to
    that source), False when it already existed."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not quality:
        quality = {}

    # collection_files.connection_id is FK'd to media_server_connections. A
    # caller can hand us an id that isn't one (a ScrobbleConnection id, or a
    # since-deleted connection a webhook is still pointed at) - link to NULL
    # rather than letting the INSERT FK-crash and roll back the whole webhook,
    # taking the watch event and session-close with it (#339).
    if connection_id is not None:
        exists = await db.execute(
            select(MediaServerConnection.id).where(
                MediaServerConnection.id == connection_id,
                MediaServerConnection.user_id == user_id,
            )
        )
        if exists.scalar_one_or_none() is None:
            connection_id = None

    # 1. Upsert the Collection row (one per user+media)
    coll_stmt = pg_insert(Collection).values(user_id=user_id, media_id=media_id)
    coll_stmt = coll_stmt.on_conflict_do_nothing(constraint="uq_collection_user_media")
    await db.execute(coll_stmt)
    await db.flush()

    # Fetch the canonical collection id
    coll_result = await db.execute(
        select(Collection.id).where(Collection.user_id == user_id, Collection.media_id == media_id)
    )
    collection_id = coll_result.scalar_one()

    # 2. Upsert the CollectionFile row (one per collection+source+source_id)
    file_existed = (await db.execute(
        select(CollectionFile.id).where(
            CollectionFile.collection_id == collection_id,
            CollectionFile.source == source,
            CollectionFile.source_id == source_id,
        )
    )).scalar_one_or_none() is not None
    update_dict: dict = {}
    if connection_id is not None:          update_dict["connection_id"]       = connection_id
    if quality.get("resolution"):         update_dict["resolution"]         = quality["resolution"]
    if quality.get("video_codec"):        update_dict["video_codec"]        = quality["video_codec"]
    if quality.get("audio_codec"):        update_dict["audio_codec"]        = quality["audio_codec"]
    if quality.get("audio_channels"):     update_dict["audio_channels"]     = quality["audio_channels"]
    if quality.get("audio_languages"):    update_dict["audio_languages"]    = quality["audio_languages"]
    if quality.get("subtitle_languages"): update_dict["subtitle_languages"] = quality["subtitle_languages"]
    if quality.get("file_path"):          update_dict["file_path"]          = quality["file_path"]

    file_stmt = pg_insert(CollectionFile).values(
        collection_id=collection_id,
        source=source,
        source_id=source_id,
        connection_id=connection_id,
        resolution=quality.get("resolution"),
        video_codec=quality.get("video_codec"),
        audio_codec=quality.get("audio_codec"),
        audio_channels=quality.get("audio_channels"),
        audio_languages=quality.get("audio_languages", []),
        subtitle_languages=quality.get("subtitle_languages", []),
        file_path=quality.get("file_path"),
    )
    if update_dict:
        file_stmt = file_stmt.on_conflict_do_update(
            constraint="uq_collection_file_source",
            set_=update_dict,
        )
    else:
        file_stmt = file_stmt.on_conflict_do_nothing(constraint="uq_collection_file_source")

    await db.execute(file_stmt)
    await db.flush()
    return not file_existed


async def _push_watched_for_new_item(
    db: AsyncSession,
    user_id: int,
    conn: MediaServerConnection | None,
    source_id: str,
    media_list: list[Media],
) -> bool:
    """A title just landed in ``conn``'s library (library.new / ItemAdded) that
    Scrob already has finished watches of - marked watched before it was
    collected (#420) - so mark it watched on that server too, instead of
    leaving it unwatched until a full push. The server-side counterpart of the
    same fix in the pull sync (see core.server_sync._push_watched_back_to_source).

    Gated on the connection's own push_watched flag. A multi-episode file
    only qualifies when every episode in it is watched, since the server call
    marks the whole file. Checks the server's own state first - marking an
    already-watched item again mints a fresh dated play (#302). Jellyfin/Emby
    get the original watch date; Plex can't be backdated.
    """
    if not conn or not conn.push_watched or conn.type not in ("plex", "jellyfin", "emby") or not media_list:
        return False

    from models.tracking import StreamBaseline
    baseline = await db.get(StreamBaseline, conn.id)
    if baseline is None or not baseline.approved:
        return False


    media_ids = [m.id for m in media_list]
    watched_at_by_media = await db_queries.latest_watched_at(db, user_id, media_ids)
    if len(watched_at_by_media) != len(set(media_ids)):
        return False

    if conn.type == "plex":
        import core.plex as plex_client
        item = await plex_client.get_item(conn.url, conn.token, source_id)
        if item is None or int(item.get("viewCount") or 0) > 0:
            return False
        return await outbound_sync.push_plex_watched_and_record(conn, source_id, user_id, media_ids[0])

    from core import emby as emby_client
    from core import jellyfin as jellyfin_client
    client = emby_client if conn.type == "emby" else jellyfin_client
    item = await client.get_item(conn.url, conn.token, source_id, user_id=conn.server_user_id)
    if item is None or (item.get("UserData") or {}).get("Played"):
        return False
    dates = [d for d in watched_at_by_media.values() if d is not None]
    # Registered before the call so the server's echo can't beat it (#247/#251).
    for media_id in media_ids:
        watch_echo.mark_pushed_watched(user_id, media_id)
    return await client.mark_watched(
        conn.url, conn.token, conn.server_user_id, source_id,
        played_at=max(dates) if dates else None,
    )


async def _remove_collection_entry(
    db: AsyncSession,
    user_id: int,
    media_id: int,
    source: CollectionSource,
    source_id: str,
) -> None:
    """Removes the CollectionFile for this (source, source_id), and the parent
    Collection too if that was the last file backing it (an item can be
    collected from more than one source, e.g. both Plex and Jellyfin — only
    delisting it everywhere should remove it from the user's collection)."""
    from sqlalchemy import delete as sa_delete

    coll_result = await db.execute(
        select(Collection.id).where(Collection.user_id == user_id, Collection.media_id == media_id)
    )
    collection_id = coll_result.scalar_one_or_none()
    if collection_id is None:
        return

    await db.execute(
        sa_delete(CollectionFile).where(
            CollectionFile.collection_id == collection_id,
            CollectionFile.source == source,
            CollectionFile.source_id == source_id,
        )
    )
    await db.flush()

    remaining = await db.execute(
        select(CollectionFile.id).where(CollectionFile.collection_id == collection_id)
    )
    if remaining.first() is None:
        await db.execute(sa_delete(Collection).where(Collection.id == collection_id))
        await db.flush()


async def _backfill_plex_runtime(
    db: AsyncSession, media: Media, data: dict, conn: MediaServerConnection | None, tmdb_key: str | None,
) -> None:
    """Actively fills in Media.runtime when a Plex webhook event finds it
    still missing - without it, the Now Playing bar's live progress
    interpolation can never engage and stays frozen at a flat 0%/whatever
    percent the last event reported (#169). Some Plex clients (e.g. TV apps)
    under-report duration on their first play/resume event, so the current
    event's own duration_ms isn't always enough; asks Plex directly for the
    item next, then TMDB as a last resort. All three sources are best-effort -
    leaves media.runtime untouched (still None) if none of them pan out, to
    be retried on the next event for this item.
    """
    if media.runtime:
        return

    # Each source below is independently wrapped - a webhook-only setup may
    # have no Plex *connection* at all (conn is None, or one exists but its
    # url/token weren't filled in), and a multi-server user's webhook can
    # arrive from a Plex server other than the one configured here, so a
    # "wrong server" lookup failure is a normal, expected outcome, not a bug.
    # One source failing must still let the next be tried, and none of them
    # may ever take the webhook down with it.
    duration_ms = data.get("duration_ms")

    if not duration_ms and conn and getattr(conn, "url", None) and getattr(conn, "token", None) and data.get("plex_rating_key"):
        try:
            import core.plex as plex_client
            item = await plex_client.get_item(conn.url, conn.token, str(data["plex_rating_key"]))
            if item:
                duration_ms = item.get("duration")
        except Exception as e:
            print(f"  Could not fetch Plex item to backfill runtime: {e}")

    if duration_ms:
        try:
            media.runtime = max(1, round(duration_ms / 60000))
            return
        except (TypeError, ValueError) as e:
            print(f"  Could not compute runtime from duration_ms={duration_ms!r}: {e}")

    from_tmdb = await _runtime_from_tmdb(db, media, tmdb_key)
    if from_tmdb:
        media.runtime = from_tmdb


async def _runtime_from_tmdb(db: AsyncSession, media: Media, tmdb_key: str | None) -> int | None:
    """Runtime in minutes from TMDB for a movie or episode, or None. Shared
    last-resort source for the Plex and Jellyfin/Emby runtime backfills -
    best-effort, never raises."""
    if not tmdb_key:
        return None
    try:
        if media.media_type == MediaType.movie and media.tmdb_id:
            data = await tmdb.get_movie(media.tmdb_id, api_key=tmdb_key)
            return data.get("runtime") or None
        if (
            media.media_type == MediaType.episode
            and media.show_id
            and media.season_number is not None
            and media.episode_number is not None
        ):
            show = (await db.execute(select(Show).where(Show.id == media.show_id))).scalar_one_or_none()
            if show and show.tmdb_id:
                data = await tmdb.get_episode(
                    show.tmdb_id, media.season_number, media.episode_number, api_key=tmdb_key,
                )
                return data.get("runtime") or None
    except Exception as e:
        print(f"  Could not backfill runtime from TMDB for media_id={getattr(media, 'id', None)}: {e}")
    return None


_RUNTIME_BACKFILL_EVENTS = (
    "PlaybackStart", "PlaybackProgress", "PlaybackStop",
    "playback.start", "playback.progress", "playback.stop",
)


async def _backfill_jellyfin_runtimes(
    db: AsyncSession, media_list: list["Media"], data: dict, tmdb_key: str | None,
) -> None:
    """Fill Media.runtime for any row in media_list still missing it, from the
    payload's RunTimeTicks (exact for the file) or, failing that, TMDB - then
    commit. Without it the Now Playing bar's live progress interpolation never
    engages for that item (#383). The Plex path already does this via
    _backfill_plex_runtime; Jellyfin/Emby dropped RunTimeTicks after using it
    for the progress ratio.
    """
    changed = False
    for media in media_list:
        if media.runtime:
            continue
        minutes: int | None = None
        ticks = data.get("runtime_ticks")
        if ticks:
            try:
                # RunTimeTicks is 100-nanosecond units: 6e8 ticks per minute.
                minutes = round(int(ticks) / 600_000_000) or None
            except (TypeError, ValueError):
                minutes = None
        if not minutes:
            minutes = await _runtime_from_tmdb(db, media, tmdb_key)
        if minutes and minutes > 0:
            media.runtime = minutes
            changed = True
    if changed:
        await db.commit()


async def _backfill_kodi_runtime(
    db: AsyncSession, media: Media, data: dict, tmdb_key: str | None,
) -> None:
    """Fill Media.runtime when a Kodi-style webhook event finds it missing,
    from the event's own total length (exact for the file) or, failing that,
    TMDB - then commit. The Plex and Jellyfin/Emby paths already do this
    (_backfill_plex_runtime, _backfill_jellyfin_runtimes); this webhook used
    the length only for the progress ratio and dropped it, so an episode TMDB
    has no runtime for yet (a freshly aired one, typically) left the Now
    Playing bar's live progress frozen at the last reported percentage (#383).
    """
    if media.runtime:
        return
    minutes: int | None = None
    total_seconds = data.get("total_seconds")
    if total_seconds:
        try:
            minutes = round(int(total_seconds) / 60) or None
        except (TypeError, ValueError):
            minutes = None
    if not minutes:
        minutes = await _runtime_from_tmdb(db, media, tmdb_key)
    if minutes and minutes > 0:
        media.runtime = minutes
        await db.commit()


async def _backfill_credits_stingers(db: AsyncSession, media: Media, tmdb_key: str | None) -> None:
    """Actively fills in a movie's mid/post-credits-scene flags (#319) when a
    webhook event finds them missing from tmdb_data - a movie enriched before
    this feature shipped has no has_mid_credits_scene/has_post_credits_scene
    keys yet, so the Now Playing bar's badge would otherwise never show for
    it until a manual "Refresh Metadata". Self-heals once per movie: the
    keys are always written together, so their presence (even both False)
    means this has already run.
    """
    if media.media_type != MediaType.movie or not media.tmdb_id or not tmdb_key:
        return
    tmdb_data = media.tmdb_data or {}
    if "has_mid_credits_scene" in tmdb_data:
        return
    try:
        data = await tmdb.get_movie(media.tmdb_id, api_key=tmdb_key)
        has_mid, has_post = tmdb.extract_credits_stingers(data)
        media.tmdb_data = {**tmdb_data, "has_mid_credits_scene": has_mid, "has_post_credits_scene": has_post}
    except Exception as e:
        print(f"  Could not backfill credits-stinger flags for media_id={getattr(media, 'id', None)}: {e}")


async def _resolve_plex_progress(
    data: dict, conn: MediaServerConnection | None,
) -> tuple[float, int]:
    """Plex's play/resume/stop webhook can fire with viewOffset still at 0 -
    the client hasn't reported its real seek position back to the server yet
    (most visible on resume: the Now Playing bar would start over at 0%
    instead of the position playback actually resumed from). Asking Plex
    directly for the item's own last known viewOffset is authoritative -
    it's the same value that powers Plex's own Continue Watching - so it's a
    reliable fallback when the webhook's momentary value is suspiciously 0.
    """
    if data["progress_percent"] > 0:
        return data["progress_percent"], data["progress_seconds"]
    if conn and getattr(conn, "url", None) and getattr(conn, "token", None) and data.get("plex_rating_key"):
        try:
            import core.plex as plex_client
            item = await plex_client.get_item(conn.url, conn.token, str(data["plex_rating_key"]))
            if item:
                view_offset_ms = item.get("viewOffset", 0) or 0
                duration_ms = item.get("duration", 0) or 0
                if duration_ms and view_offset_ms:
                    return round(view_offset_ms / duration_ms, 4), int(view_offset_ms / 1000)
        except Exception as e:
            print(f"  Could not fetch Plex item to resolve authoritative progress: {e}")
    return data["progress_percent"], data["progress_seconds"]


async def find_or_create_media_plex(
    data: dict, db: AsyncSession, api_key: str = None, conn: MediaServerConnection | None = None,
    user_id: int | None = None,
) -> Media | None:
    # Fastest path: match via CollectionFile source_id (plex ratingKey).
    # This works even after season remaps where show_id/season_number no longer
    # match what Plex reports in the webhook payload.
    if data.get("plex_rating_key"):
        cf_result = await db.execute(
            select(Media)
            .join(Collection, Collection.media_id == Media.id)
            .join(CollectionFile, CollectionFile.collection_id == Collection.id)
            .where(
                CollectionFile.source == CollectionSource.plex,
                CollectionFile.source_id == data["plex_rating_key"],
            )
        )
        media = cf_result.scalars().first()
        if media:
            return media

    series_tmdb_id: Optional[int] = int(data["grandparent_tmdb_id"]) if data.get("grandparent_tmdb_id") else None

    # If missing series_tmdb_id, try to resolve it via other identifiers
    if data["media_type"] == "episode" and not series_tmdb_id:
        # 1. Try grandparent TVDB/IMDb
        if data.get("grandparent_tvdb_id"):
            try:
                res = await tmdb.find_by_external_id(data["grandparent_tvdb_id"], "tvdb_id", api_key=api_key)
                if res.get("tv_results"):
                    series_tmdb_id = res["tv_results"][0]["id"]
            except Exception: pass

        if not series_tmdb_id and data.get("grandparent_imdb_id"):
            try:
                res = await tmdb.find_by_external_id(data["grandparent_imdb_id"], "imdb_id", api_key=api_key)
                if res.get("tv_results"):
                    series_tmdb_id = res["tv_results"][0]["id"]
            except Exception: pass

        # 2. Try episode identifiers (TMDB Find returns show context)
        if not series_tmdb_id and data.get("tvdb_id"):
            try:
                res = await tmdb.find_by_external_id(data["tvdb_id"], "tvdb_id", api_key=api_key)
                if res.get("tv_episode_results"):
                    series_tmdb_id = res["tv_episode_results"][0].get("show_id")
            except Exception: pass

        if not series_tmdb_id and data.get("imdb_id"):
            try:
                res = await tmdb.find_by_external_id(data["imdb_id"], "imdb_id", api_key=api_key)
                if res.get("tv_episode_results"):
                    series_tmdb_id = res["tv_episode_results"][0].get("show_id")
            except Exception: pass

        # 3. Fetch grandparent show from Plex to extract its TMDB GUID
        #    (needed when grandparentGuid is a plex://show/xxx internal ID)
        if not series_tmdb_id and data.get("grandparent_rating_key") and conn:
            try:
                import core.plex as plex_client
                show_item = await plex_client.get_item(conn.url, conn.token, data["grandparent_rating_key"])
                if show_item:
                    # get_guids() falls back to the lowercase 'guid' string a
                    # legacy-agent show has instead of a Guid array, and
                    # extract_tmdb_id/extract_tvdb_id understand the HAMA
                    # agent's packed scheme too, so an older/manually-matched
                    # or HAMA-scanned show can still resolve here.
                    show_guids = plex_client.get_guids(show_item)
                    series_tmdb_id = plex_client.extract_tmdb_id(show_guids)
                    # Also try TVDB on the show if TMDB still not found
                    if not series_tmdb_id:
                        show_tvdb_id = plex_client.extract_tvdb_id(show_guids)
                        if show_tvdb_id:
                            try:
                                res = await tmdb.find_by_external_id(show_tvdb_id, "tvdb_id", api_key=api_key)
                                if res.get("tv_results"):
                                    series_tmdb_id = res["tv_results"][0]["id"]
                            except Exception:
                                pass
            except Exception:
                pass

        # 4. Last resort: search by show title — exact name match only to avoid false positives
        #    (a fuzzy first-result on a show that doesn't exist on TMDB at all causes wrong linkage).
        if not series_tmdb_id and data.get("grandparent_title"):
            try:
                res = await tmdb.search_shows(data["grandparent_title"], api_key=api_key)
                gt_lower = data["grandparent_title"].lower()
                for r in (res.get("results") or [])[:3]:
                    if r.get("name", "").lower() == gt_lower or r.get("original_name", "").lower() == gt_lower:
                        series_tmdb_id = r["id"]
                        break
            except Exception: pass

    # When we couldn't verify the parent show on TMDB via any identifier, discard any
    # episode-level TMDB ID that Plex provided. Plex sometimes assigns a movie's TMDB ID
    # to episodes it can't match (the show exists only on TVDB/IMDB, not TMDB).
    if data["media_type"] == "episode" and not series_tmdb_id:
        data["tmdb_id"] = None

    if data["tmdb_id"]:
        tmdb_id_int = int(data["tmdb_id"])
        media_type = MediaType(data["media_type"])
        result = await db.execute(
            select(Media).where(
                Media.tmdb_id == tmdb_id_int,
                Media.media_type == media_type,
            )
        )
        media = result.scalars().first()
        if media:
            show: Show | None = None
            if media.media_type == MediaType.episode and media.show_id is None and series_tmdb_id:
                # Backfill show context if this episode record was created without it
                try:
                    show = await show_metadata.find_or_create_show(db, series_tmdb_id, api_key)
                    media.show_id = show.id
                except Exception as e:
                    print(f"  Could not backfill show context for episode: {e}")
            elif media.media_type == MediaType.episode and media.show_id is not None:
                show_result = await db.execute(select(Show).where(Show.id == media.show_id))
                show = show_result.scalar_one_or_none()

            if media.media_type == MediaType.episode and show:
                # Refresh title/metadata from TMDB on every match, not just
                # once at creation - Plex's own title for a brand-new episode
                # can still be a pre-air working title (#394: "TTT Anniversary"
                # vs. the aired "125 Years Young"), and unlike the episode/
                # season pages (which always fetch TMDB live), this cached row
                # was otherwise never revisited again after creation.
                # enrich_media already tolerates a TMDB failure internally,
                # leaving the existing row exactly as it was rather than
                # raising or blanking anything out.
                try:
                    tvdb_id, tvdb_api_key, tvdb_lang = await enrichment.resolve_tvdb_fallback(db, show, user_id)
                    media = await enrich_media_safely(
                        db, media, api_key=api_key, series_tmdb_id=series_tmdb_id or show.tmdb_id,
                        tvdb_id=tvdb_id, tvdb_api_key=tvdb_api_key, tvdb_lang=tvdb_lang,
                    )
                except Exception as e:
                    print(f"  Could not refresh episode metadata: {e}")
            return media

    # 2b. Movie matching by title + year if TMDB ID is missing
    if data["media_type"] == "movie" and not data["tmdb_id"]:
        # Try local match first to avoid redundant TMDB search
        local_q = select(Media).where(
            Media.media_type == MediaType.movie,
            Media.title.ilike(data["title"]),
        )
        if data.get("year"):
            local_q = local_q.where(Media.release_date.like(f"{data['year']}%"))

        media = (await db.execute(local_q)).scalars().first()
        if media:
            return media

        # Try TMDB search to find the real ID
        try:
            search_res = await tmdb.search_movies(data["title"], year=data.get("year"), api_key=api_key)
            if search_res.get("results"):
                tmdb_movie = search_res["results"][0]
                data["tmdb_id"] = str(tmdb_movie["id"])
                # Check again with the new TMDB ID
                result = await db.execute(
                    select(Media).where(
                        Media.tmdb_id == tmdb_movie["id"],
                        Media.media_type == MediaType.movie,
                    )
                )
                media = result.scalars().first()
                if media:
                    return media
        except Exception:
            pass

    # Don't create a row for an episode we can't identify at all — it can never
    # be enriched or matched back to a real episode, and would inflate collection counts.
    if data["media_type"] == "episode" and data["season_number"] is None and data["episode_number"] is None and not data["tmdb_id"]:
        print(f"  Skipping unidentifiable episode '{data['title']}' (no season/episode/tmdb_id)")
        return None

    # #335: if the user put this show on TVDB (aired) order, rewrite the
    # TVDB-native (season, episode) Plex reported to the canonical TMDB position
    # before the raw-number match/create below.
    await _translate_plex_tvdb_episode_position(data, db, series_tmdb_id, user_id, api_key)

    # For episodes without a TMDB ID, look up by show+season+episode before creating
    # to avoid duplicate Media rows on repeated webhook events (e.g. episodes not yet
    # on TMDB that Plex tracks only by season/episode number).
    if data["media_type"] == "episode" and not data["tmdb_id"] and series_tmdb_id and data["season_number"] is not None and data["episode_number"] is not None:
        show_result = await db.execute(select(Show).where(Show.tmdb_id == series_tmdb_id))
        existing_show = show_result.scalar_one_or_none()
        if existing_show:
            ep_result = await db.execute(
                select(Media).where(
                    Media.show_id == existing_show.id,
                    Media.season_number == data["season_number"],
                    Media.episode_number == data["episode_number"],
                    Media.media_type == MediaType.episode,
                )
            )
            existing_ep = ep_result.scalars().first()
            if existing_ep:
                return existing_ep

    media, created = await create_media_safely(
        db,
        int(data["tmdb_id"]) if data["tmdb_id"] else None,
        MediaType(data["media_type"]),
        title=data["title"],
        season_number=data["season_number"],
        episode_number=data["episode_number"],
    )
    if created and media.media_type == MediaType.episode and not series_tmdb_id and data.get("grandparent_title"):
        media.tmdb_data = {"show_title": data["grandparent_title"]}

    if media.media_type == MediaType.episode and series_tmdb_id:
        try:
            show = await show_metadata.find_or_create_show(db, series_tmdb_id, api_key)
            media.show_id = show.id
            tvdb_id, tvdb_api_key, tvdb_lang = await enrichment.resolve_tvdb_fallback(db, show, user_id)
            media = await enrich_media_safely(
                db, media, api_key=api_key, series_tmdb_id=series_tmdb_id,
                tvdb_id=tvdb_id, tvdb_api_key=tvdb_api_key, tvdb_lang=tvdb_lang,
            )
        except Exception as e:
            print(f"  Could not enrich episode with show context: {e}")
    else:
        await enrich_media(media, api_key=api_key)
    return media


async def _handle_plex_webhook(request: Request, db: AsyncSession, api_key: str, connection_id: int | None = None):
    user_result = await db.execute(select(User).where(User.api_key == api_key))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        form = await request.form()
    except Exception as e:
        return {"status": "error", "reason": f"form parse failed: {e}"}

    raw_payload = form.get("payload")
    if not raw_payload:
        return {"status": "ignored", "reason": "no payload field"}

    try:
        payload = json.loads(str(raw_payload))
    except (json.JSONDecodeError, TypeError):
        return {"status": "ignored", "reason": "invalid JSON"}

    event = payload.get("event", "unknown")

    data = webhook_payloads.parse_plex_payload(payload)
    if not data:
        return {"status": "ignored"}

    if connection_id is not None:
        conn = await _get_connection_by_id(db, user.id, connection_id)
    else:
        conn = await _get_oldest_connection(db, user.id, "plex")

    # If a plex server_username is configured on the connection, enforce it.
    account_title = data.get("account_title", "")
    if account_title and conn and conn.server_username:
        if account_title.lower() != conn.server_username.strip().lower():
            return {"status": "ignored", "reason": f"event for plex user '{account_title}' does not match connection '{conn.server_username}'"}

    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    settings = settings_result.scalar_one_or_none()
    window_minutes = dedup_window_from_settings(settings)
    tmdb_key = await settings_store.get_effective_tmdb_key(db, settings)

    session_key = f"plex:{user.id}:{data['session_key']}"

    if event in ("media.play", "media.resume", "media.pause", "media.stop", "media.scrobble", "media.rate"):
        if _is_duplicate_webhook_delivery(f"{session_key}:{event}"):
            return {"status": "ignored", "reason": "duplicate webhook delivery"}

    if event in ("media.play", "media.resume", "media.pause", "media.stop", "media.scrobble"):
        media = await find_or_create_media_plex(data, db, api_key=tmdb_key, conn=conn, user_id=user.id)
        if media is None:
            return {"status": "ignored", "reason": "episode could not be identified (no season/episode/tmdb_id)"}

    if event == "media.play":
        if not conn or conn.sync_playback:
            session = await _get_or_open_session(db, session_key, "plex", user.id, media.id)
            session.state = "playing"
            session.updated_at = datetime.utcnow()
            resolved_percent, resolved_seconds = await _resolve_plex_progress(data, conn)
            if resolved_percent > 0:
                session.progress_percent = resolved_percent
                session.progress_seconds = resolved_seconds
            await _backfill_plex_runtime(db, media, data, conn, tmdb_key)
            await _backfill_credits_stingers(db, media, tmdb_key)
            await db.commit()
        await scrobble_delivery.forward(settings, media, "start", data["progress_percent"], db=db)

    elif event == "media.resume":
        media = await find_or_create_media_plex(data, db, api_key=tmdb_key, conn=conn, user_id=user.id)
        if media is None:
            return {"status": "ignored", "reason": "episode could not be identified (no season/episode/tmdb_id)"}
        if not conn or conn.sync_playback:
            session = await _get_or_open_session(db, session_key, "plex", user.id, media.id)
            session.state = "playing"
            # Same resolution as media.play above - Plex can fire this before
            # the player has reported its actual seek position, so a 0 here
            # is often stale rather than a real "back to the start" resume.
            resolved_percent, resolved_seconds = await _resolve_plex_progress(data, conn)
            if resolved_percent > 0:
                session.progress_percent = resolved_percent
                session.progress_seconds = resolved_seconds
            session.updated_at = datetime.utcnow()
            await _backfill_plex_runtime(db, media, data, conn, tmdb_key)
            await _backfill_credits_stingers(db, media, tmdb_key)
            await db.commit()
        await scrobble_delivery.forward(settings, media, "start", data["progress_percent"], db=db)

    elif event == "media.pause":
        if not conn or conn.sync_playback:
            result = await db.execute(
                select(PlaybackSession).where(PlaybackSession.session_key == session_key)
            )
            session = result.scalar_one_or_none()
            if session:
                session.state = "paused"
                session.progress_percent = data["progress_percent"]
                session.progress_seconds = data["progress_seconds"]
                session.updated_at = datetime.utcnow()
                await _backfill_plex_runtime(db, media, data, conn, tmdb_key)
                await _backfill_credits_stingers(db, media, tmdb_key)
                await db.commit()
        await scrobble_delivery.forward(settings, media, "pause", data["progress_percent"], db=db)

    elif event == "media.stop":
        session = await _close_session(db, session_key)
        progress_percent, progress_seconds = await _resolve_plex_progress(data, conn)
        if progress_percent <= 0:
            progress_percent = session.progress_percent if session else 0.0
            progress_seconds = session.progress_seconds if session else 0
        if not conn or conn.sync_playback:
            media_id = session.media_id if session else None
            if media_id is None:
                fallback = await find_or_create_media_plex(data, db, api_key=tmdb_key, conn=conn, user_id=user.id)
                media_id = fallback.id if fallback else None
            if media_id and (not conn or conn.sync_watched) and progress_percent > 0.05:
                await _write_watch_event(
                    db, user.id, media_id,
                    progress_percent, progress_seconds,
                    progress_percent >= 0.90,
                    window_minutes,
                )
            await _backfill_plex_runtime(db, media, data, conn, tmdb_key)
            await _backfill_credits_stingers(db, media, tmdb_key)
            await db.commit()
        await scrobble_delivery.forward(settings, media, "stop", progress_percent, db=db)

    elif event == "media.scrobble":
        await _close_session(db, session_key)
        if not conn or conn.sync_watched:
            media = await find_or_create_media_plex(data, db, api_key=tmdb_key, conn=conn, user_id=user.id)
            if media:
                await _write_watch_event(db, user.id, media.id, 1.0, data["progress_seconds"], True, window_minutes)
            await db.commit()

    elif event == "media.rate":
        if not conn or conn.sync_ratings:
            media = await find_or_create_media_plex(data, db, api_key=tmdb_key, conn=conn, user_id=user.id)
            rating_value = data.get("rating")

            existing = await db.execute(
                select(Rating).where(Rating.media_id == media.id, Rating.user_id == user.id)
            )
            existing_rating = existing.scalar_one_or_none()

            if rating_value is None or float(rating_value) == 0:
                if existing_rating:
                    await db.delete(existing_rating)
                    await db.commit()
            else:
                if existing_rating:
                    existing_rating.rating = float(rating_value)
                    existing_rating.rated_at = datetime.utcnow()
                else:
                    db.add(Rating(
                        media_id=media.id,
                        user_id=user.id,
                        rating=float(rating_value),
                    ))
                await db.commit()

    elif event == "library.new":
        if not conn or conn.sync_collection:
            section_id = data.get("library_section_id")
            if section_id and conn:
                sel_result = await db.execute(
                    select(PlexLibrarySelection).where(PlexLibrarySelection.connection_id == conn.id)
                )
                selected_keys = {row.library_key for row in sel_result.scalars().all()}
                if selected_keys and section_id not in selected_keys:
                    return {"status": "ignored", "reason": f"library section {section_id} not in sync selection"}

            import core.plex as plex_client

            plex_media_type = 1 if data["media_type"] == "movie" else 4
            recent_items: list = []
            if section_id and conn:
                recent_items = await plex_client.get_recently_added(
                    conn.url, conn.token, section_id, plex_media_type
                )

            payload_key = data.get("plex_rating_key")
            recent_keys = {str(it.get("ratingKey")) for it in recent_items}
            if payload_key:
                payload_item = await plex_client.get_item(conn.url, conn.token, payload_key) if conn else None
                if payload_item:
                    # Always prefer the individually-fetched item — bulk recentlyAdded
                    # omits Part.Stream data so audio/subtitle languages would be empty.
                    recent_items = [it for it in recent_items if str(it.get("ratingKey")) != str(payload_key)]
                    recent_items.insert(0, payload_item)
                elif str(payload_key) not in recent_keys:
                    recent_items = []

            new_plex_items: list[tuple[str, Media]] = []
            if recent_items:
                for plex_item in recent_items:
                    item_guids = plex_client.get_guids(plex_item)
                    item_tmdb_id = plex_client.extract_tmdb_id(item_guids)
                    item_rating_key = str(plex_item.get("ratingKey", ""))
                    item_quality = plex_client.extract_quality(plex_item.get("Media", []))

                    item_data = {
                        "media_type": "movie" if plex_item.get("type") == "movie" else "episode",
                        "tmdb_id": str(item_tmdb_id) if item_tmdb_id else None,
                        "tvdb_id": plex_client.extract_tvdb_id(item_guids),
                        "imdb_id": plex_client.extract_imdb_id(item_guids),
                        "title": plex_item.get("title") or plex_item.get("grandparentTitle", ""),
                        "season_number": plex_item.get("parentIndex"),
                        "episode_number": plex_item.get("index"),
                        "plex_rating_key": item_rating_key,
                        "grandparent_rating_key": str(plex_item["grandparentRatingKey"]) if plex_item.get("grandparentRatingKey") else None,
                        "grandparent_title": plex_item.get("grandparentTitle"),
                        "grandparent_tmdb_id": None,
                        "grandparent_tvdb_id": None,
                        "grandparent_imdb_id": None,
                        "quality": item_quality,
                    }
                    # Same extract_* helpers as item_guids above (not raw
                    # regex) so a HAMA-agent show's grandparentGuid resolves
                    # here too - see the matching comment in parse_plex_payload.
                    grandparent_guids = [{"id": plex_item.get("grandparentGuid", "")}]
                    _gp_tmdb_id = plex_client.extract_tmdb_id(grandparent_guids)
                    item_data["grandparent_tmdb_id"] = str(_gp_tmdb_id) if _gp_tmdb_id else None
                    item_data["grandparent_tvdb_id"] = plex_client.extract_tvdb_id(grandparent_guids)
                    item_data["grandparent_imdb_id"] = plex_client.extract_imdb_id(grandparent_guids)

                    try:
                        item_media = await find_or_create_media_plex(
                            item_data, db, api_key=tmdb_key, conn=conn, user_id=user.id
                        )
                        if item_media:
                            created_file = await _ensure_collection_entry(
                                db, user.id, item_media.id, CollectionSource.plex,
                                item_rating_key, item_quality,
                                connection_id=conn.id if conn else None,
                            )
                            if created_file:
                                new_plex_items.append((item_rating_key, item_media))
                    except Exception as e:
                        print(f"  library.new batch: failed to process item {item_rating_key}: {e}")
            else:
                media = await find_or_create_media_plex(data, db, api_key=tmdb_key, conn=conn, user_id=user.id)
                quality = data.get("quality") or {}
                if (not quality.get("resolution") or not quality.get("audio_languages")) and conn:
                    item = await plex_client.get_item(conn.url, conn.token, data["plex_rating_key"])
                    if item:
                        quality = plex_client.extract_quality(item.get("Media", []))
                if media:
                    created_file = await _ensure_collection_entry(
                        db, user.id, media.id, CollectionSource.plex, data["plex_rating_key"], quality,
                        connection_id=conn.id if conn else None,
                    )
                    if created_file:
                        new_plex_items.append((data["plex_rating_key"], media))
            await db.commit()
            for rating_key, new_media in new_plex_items:
                try:
                    await _push_watched_for_new_item(db, user.id, conn, rating_key, [new_media])
                except Exception as e:
                    print(f"  library.new: watched push-back failed for {rating_key}: {e}")

    elif event == "library.update":
        if not conn or conn.sync_collection:
            section_id = data.get("library_section_id")
            if section_id and conn:
                sel_result = await db.execute(
                    select(PlexLibrarySelection).where(PlexLibrarySelection.connection_id == conn.id)
                )
                selected_keys = {row.library_key for row in sel_result.scalars().all()}
                if selected_keys and section_id not in selected_keys:
                    return {"status": "ignored", "reason": f"library section {section_id} not in sync selection"}

            media = await find_or_create_media_plex(data, db, api_key=tmdb_key, conn=conn, user_id=user.id)
            if media is None:
                return {"status": "ignored", "reason": "could not identify media"}

            quality = data.get("quality") or {}
            if (not quality.get("resolution") or not quality.get("audio_languages")) and conn:
                import core.plex as plex_client
                item = await plex_client.get_item(conn.url, conn.token, data["plex_rating_key"])
                if item:
                    quality = plex_client.extract_quality(item.get("Media", []))

            old_files_result = await db.execute(
                select(CollectionFile)
                .join(Collection)
                .where(
                    CollectionFile.source == CollectionSource.plex,
                    CollectionFile.source_id == data["plex_rating_key"],
                    Collection.user_id == user.id,
                    Collection.media_id != media.id,
                )
            )
            for old_file in old_files_result.scalars().all():
                old_collection_id = old_file.collection_id
                await db.delete(old_file)
                await db.flush()
                remaining = await db.execute(
                    select(func.count(CollectionFile.id)).where(
                        CollectionFile.collection_id == old_collection_id
                    )
                )
                if remaining.scalar() == 0:
                    old_coll = await db.get(Collection, old_collection_id)
                    if old_coll:
                        await db.delete(old_coll)

            await _ensure_collection_entry(
                db, user.id, media.id, CollectionSource.plex, data["plex_rating_key"], quality,
                connection_id=conn.id if conn else None,
            )
            await db.commit()

    return {"status": "ok", "event": event, "title": data["title"]}


async def _handle_plex_scrobble_webhook(request: Request, db: AsyncSession, api_key: str, connection_id: int):
    user_result = await db.execute(select(User).where(User.api_key == api_key))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        form = await request.form()
    except Exception as e:
        return {"status": "error", "reason": f"form parse failed: {e}"}

    raw_payload = form.get("payload")
    if not raw_payload:
        return {"status": "ignored", "reason": "no payload field"}

    try:
        payload = json.loads(str(raw_payload))
    except (json.JSONDecodeError, TypeError):
        return {"status": "ignored", "reason": "invalid JSON"}

    event = payload.get("event", "unknown")

    data = webhook_payloads.parse_plex_payload(payload)
    if not data:
        return {"status": "ignored"}

    conn = await _get_scrobble_connection_by_id(db, user.id, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Scrobble connection not found")

    account_title = data.get("account_title", "")
    if account_title and conn.server_username:
        if account_title.lower() != conn.server_username.strip().lower():
            return {"status": "ignored", "reason": f"event for plex user '{account_title}' does not match connection '{conn.server_username}'"}

    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    settings = settings_result.scalar_one_or_none()
    window_minutes = dedup_window_from_settings(settings)
    tmdb_key = await settings_store.get_effective_tmdb_key(db, settings)

    session_key = f"plex:scrobble:{user.id}:{data['session_key']}"
    # See _duplicated_by_full_connection's docstring (#312) - guards only the
    # outbound scrobble dispatch below, not local session/watch tracking.
    is_duplicate = await _duplicated_by_full_connection(db, "plex", user.id, data["session_key"])

    if event in ("media.play", "media.resume", "media.pause", "media.stop", "media.scrobble"):
        if _is_duplicate_webhook_delivery(f"{session_key}:{event}"):
            return {"status": "ignored", "reason": "duplicate webhook delivery"}
        media = await find_or_create_media_plex(data, db, api_key=tmdb_key, conn=None, user_id=user.id)
        if media is None:
            return {"status": "ignored", "reason": "episode could not be identified (no season/episode/tmdb_id)"}

    if event == "media.play":
        if conn.sync_playback:
            session = await _get_or_open_session(db, session_key, "plex", user.id, media.id)
            session.state = "playing"
            session.updated_at = datetime.utcnow()
            if data["progress_percent"] > 0:
                session.progress_percent = data["progress_percent"]
                session.progress_seconds = data["progress_seconds"]
            await _backfill_plex_runtime(db, media, data, None, tmdb_key)
            await _backfill_credits_stingers(db, media, tmdb_key)
            await db.commit()
        if not is_duplicate:
            await scrobble_delivery.forward(settings, media, "start", data["progress_percent"], db=db)

    elif event == "media.resume":
        media = await find_or_create_media_plex(data, db, api_key=tmdb_key, conn=None, user_id=user.id)
        if media is None:
            return {"status": "ignored", "reason": "episode could not be identified (no season/episode/tmdb_id)"}
        if conn.sync_playback:
            session = await _get_or_open_session(db, session_key, "plex", user.id, media.id)
            session.state = "playing"
            session.progress_percent = data["progress_percent"]
            session.progress_seconds = data["progress_seconds"]
            session.updated_at = datetime.utcnow()
            await _backfill_plex_runtime(db, media, data, None, tmdb_key)
            await _backfill_credits_stingers(db, media, tmdb_key)
            await db.commit()
        if not is_duplicate:
            await scrobble_delivery.forward(settings, media, "start", data["progress_percent"], db=db)

    elif event == "media.pause":
        if conn.sync_playback:
            result = await db.execute(
                select(PlaybackSession).where(PlaybackSession.session_key == session_key)
            )
            session = result.scalar_one_or_none()
            if session:
                session.state = "paused"
                session.progress_percent = data["progress_percent"]
                session.progress_seconds = data["progress_seconds"]
                session.updated_at = datetime.utcnow()
                await _backfill_plex_runtime(db, media, data, None, tmdb_key)
                await _backfill_credits_stingers(db, media, tmdb_key)
                await db.commit()
        if not is_duplicate:
            await scrobble_delivery.forward(settings, media, "pause", data["progress_percent"], db=db)

    elif event == "media.stop":
        session = await _close_session(db, session_key)
        progress_percent = data["progress_percent"] or (session.progress_percent if session else 0.0)
        if conn.sync_playback:
            progress_seconds = data["progress_seconds"] or (session.progress_seconds if session else 0)
            if conn.sync_watched and progress_percent > 0.05:
                await _write_watch_event(db, user.id, media.id, progress_percent, progress_seconds, progress_percent >= 0.90, window_minutes)
        await _backfill_plex_runtime(db, media, data, None, tmdb_key)
        await _backfill_credits_stingers(db, media, tmdb_key)
        if conn.sync_collection:
            quality = data.get("quality")
            await _ensure_collection_entry(
                db, user.id, media.id, CollectionSource.plex, data["plex_rating_key"], quality,
                # `conn` is a ScrobbleConnection, not a media_server_connections
                # row - collection_files.connection_id is FK'd to the latter and
                # a scrobble-only connection has no row there, so leave it NULL
                # (passing conn.id here FK-crashed the whole webhook - #339).
                connection_id=None,
            )
        await db.commit()
        if not is_duplicate:
            await scrobble_delivery.forward(settings, media, "stop", progress_percent, db=db)

    elif event == "media.scrobble":
        await _close_session(db, session_key)
        if conn.sync_watched:
            await _write_watch_event(db, user.id, media.id, 1.0, data["progress_seconds"], True, window_minutes)
        if conn.sync_collection:
            quality = data.get("quality")
            await _ensure_collection_entry(
                db, user.id, media.id, CollectionSource.plex, data["plex_rating_key"], quality,
                connection_id=None,  # scrobble connection, not a media-server row (#339)
            )
        await db.commit()

    # library.new fires once, right when a title is added — often long before
    # anyone plays it, so "add to collection" can't wait on a playback event
    # (see #129). Unlike the full Plex connection's library.new handler, there's
    # no server URL/token here to re-fetch or check library selection against,
    # but parse_plex_payload already extracts everything needed (Guid array,
    # quality) straight from the webhook body itself.
    elif event == "library.new":
        if conn.sync_collection:
            media = await find_or_create_media_plex(data, db, api_key=tmdb_key, conn=None, user_id=user.id)
            if media:
                quality = data.get("quality")
                await _ensure_collection_entry(
                    db, user.id, media.id, CollectionSource.plex, data["plex_rating_key"], quality,
                    connection_id=None,  # scrobble connection, not a media-server row (#339)
                )
                await db.commit()

    return {"status": "ok", "event": event, "title": data["title"]}


@router.post("/plex")
async def plex_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: str = Query(..., description="AnyList user API key"),
):
    return await _handle_plex_webhook(request, db, api_key)


@router.post("/plex/scrobble/{connection_id}")
async def plex_scrobble_webhook(
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: str = Query(..., description="AnyList user API key"),
):
    return await _handle_plex_scrobble_webhook(request, db, api_key, connection_id)


@router.post("/plex/{connection_id}")
async def plex_webhook_connection(
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: str = Query(..., description="AnyList user API key"),
):
    return await _handle_plex_webhook(request, db, api_key, connection_id)


# ── Kodi ───────────────────────────────────────────────────────────────────────


def _kodi_episode_matches(media: Media, data: dict, show: Show | None) -> bool:
    """Is a tmdb_id hit really the episode Kodi is playing?"""
    if show is not None and media.show_id is not None and media.show_id != show.id:
        return False
    for field, key in (("season_number", "season_number"), ("episode_number", "episode_number")):
        want = data.get(key)
        if want is not None and getattr(media, field) is not None and getattr(media, field) != want:
            return False
    return True


async def find_or_create_media_kodi(
    data: dict, db: AsyncSession, api_key: str = None, user_id: int | None = None
) -> Media | None:
    series_tmdb_id: Optional[int] = None

    if data["media_type"] == "episode":
        if data.get("tvdb_id"):
            try:
                res = await tmdb.find_by_external_id(data["tvdb_id"], "tvdb_id", api_key=api_key)
                if res.get("tv_results"):
                    series_tmdb_id = res["tv_results"][0]["id"]
            except Exception:
                pass

        if not series_tmdb_id and data.get("imdb_id"):
            try:
                res = await tmdb.find_by_external_id(data["imdb_id"], "imdb_id", api_key=api_key)
                if res.get("tv_results"):
                    series_tmdb_id = res["tv_results"][0]["id"]
            except Exception:
                pass

        if not series_tmdb_id and data.get("series_name"):
            local = await db.execute(select(Show).where(Show.title.ilike(data["series_name"])))
            local_show = local.scalars().first()
            if local_show:
                series_tmdb_id = local_show.tmdb_id
            else:
                try:
                    res = await tmdb.search_shows(data["series_name"], api_key=api_key)
                    if res.get("results"):
                        series_tmdb_id = res["results"][0]["id"]
                except Exception:
                    pass

    # Kodi stores the *show* TMDB id in an episode's uniqueid when its scraper
    # has no episode-level id, so an episode payload's tmdb_id is only a hint.
    episode_tmdb_id_unverified = data["media_type"] == "episode" and bool(data.get("tmdb_id"))
    show = None
    if episode_tmdb_id_unverified and not series_tmdb_id:
        try:
            candidate = int(data["tmdb_id"])
        except (TypeError, ValueError):
            candidate = None
        if candidate:
            local = await db.execute(select(Show).where(Show.tmdb_id == candidate))
            candidate_show = local.scalars().first()
            if candidate_show is not None:
                series_tmdb_id = candidate
                # Already fetched the row above - show_metadata.find_or_create_show below
                # would only repeat this exact query and hit its found-branch
                # again, never its create-from-TMDB one, since a match is
                # what was just confirmed.
                show = candidate_show

    if series_tmdb_id and show is None:
        try:
            show = await show_metadata.find_or_create_show(db, series_tmdb_id, api_key)
        except Exception:
            pass

    if data.get("tmdb_id"):
        result = await db.execute(
            select(Media).where(
                Media.tmdb_id == int(data["tmdb_id"]),
                Media.media_type == MediaType(data["media_type"]),
            )
        )
        media = result.scalars().first()
        if media and episode_tmdb_id_unverified and not _kodi_episode_matches(media, data, show):
            # Show and episode ids share one number space on TMDB, so an
            # unverified id can land on an unrelated episode. Fall through to
            # the show + season/episode lookup instead.
            media = None
        if media:
            if media.media_type == MediaType.episode and media.show_id is None and show:
                media.show_id = show.id
                tvdb_id, tvdb_api_key, tvdb_lang = await enrichment.resolve_tvdb_fallback(db, show, user_id)
                await enrich_media(
                    media, api_key=api_key, series_tmdb_id=series_tmdb_id,
                    tvdb_id=tvdb_id, tvdb_api_key=tvdb_api_key, tvdb_lang=tvdb_lang,
                )
            return media

    if data["media_type"] == "movie":
        local_q = select(Media).where(Media.media_type == MediaType.movie, Media.title.ilike(data["title"]))
        if data.get("year"):
            local_q = local_q.where(Media.release_date.like(f"{data['year']}%"))
        media = (await db.execute(local_q)).scalars().first()
        if media:
            return media
        try:
            search_res = await tmdb.search_movies(data["title"], year=data.get("year"), api_key=api_key)
            if search_res.get("results"):
                tmdb_movie = search_res["results"][0]
                data["tmdb_id"] = str(tmdb_movie["id"])
                result = await db.execute(
                    select(Media).where(Media.tmdb_id == tmdb_movie["id"], Media.media_type == MediaType.movie)
                )
                media = result.scalars().first()
                if media:
                    return media
        except Exception:
            pass

    if (
        data["media_type"] == "episode"
        and show
        and data.get("season_number") is not None
        and data.get("episode_number") is not None
    ):
        result = await db.execute(
            select(Media).where(
                Media.media_type == MediaType.episode,
                Media.show_id == show.id,
                Media.season_number == data["season_number"],
                Media.episode_number == data["episode_number"],
            )
        )
        media = result.scalars().first()
        if media:
            return media

    if data["media_type"] == "episode" and data.get("season_number") is None:
        if not data.get("tmdb_id") or episode_tmdb_id_unverified:
            return None

    new_tmdb_id = None if episode_tmdb_id_unverified else data.get("tmdb_id")
    if (
        data["media_type"] == "episode"
        and show is None
        and new_tmdb_id is None
        and data.get("season_number") is not None
        and data.get("episode_number") is not None
    ):
        # create_media_safely's unique index skips a null tmdb_id entirely, so
        # without this the next webhook for this same episode (pause/resume/
        # stop, a repeat play) would fail the tmdb_id lookup above and mint a
        # fresh duplicate row every time, forever.
        result = await db.execute(
            select(Media).where(
                Media.media_type == MediaType.episode,
                Media.show_id.is_(None),
                Media.season_number == data["season_number"],
                Media.episode_number == data["episode_number"],
                Media.title.ilike(data["title"]),
            )
        )
        media = result.scalars().first()
        if media:
            return media

    media, _created = await create_media_safely(
        db,
        int(new_tmdb_id) if new_tmdb_id else None,
        MediaType(data["media_type"]),
        title=data["title"],
        season_number=data.get("season_number"),
        episode_number=data.get("episode_number"),
        show_id=show.id if show else None,
    )
    if show and series_tmdb_id:
        tvdb_id, tvdb_api_key, tvdb_lang = await enrichment.resolve_tvdb_fallback(db, show, user_id)
        media = await enrich_media_safely(
            db, media, api_key=api_key, series_tmdb_id=series_tmdb_id,
            tvdb_id=tvdb_id, tvdb_api_key=tvdb_api_key, tvdb_lang=tvdb_lang,
        )
    else:
        await enrich_media(media, api_key=api_key)
    return media


async def _handle_kodi_webhook(request: Request, db: AsyncSession, user: User):
    body = await request.body()
    if not body:
        return {"status": "ignored", "reason": "empty body"}

    try:
        payload = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid JSON"}

    data = webhook_payloads.parse_kodi_payload(payload)
    if not data:
        return {"status": "ignored"}

    notification_type = data["notification_type"]

    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    settings = settings_result.scalar_one_or_none()
    window_minutes = dedup_window_from_settings(settings)
    tmdb_key = await settings_store.get_effective_tmdb_key(db, settings)

    media = await find_or_create_media_kodi(data, db, api_key=tmdb_key, user_id=user.id)
    if media is None:
        return {"status": "ignored", "reason": "could not identify media"}
    await _backfill_kodi_runtime(db, media, data, tmdb_key)

    session_key = f"kodi:{user.id}:{data['session_id']}"

    if notification_type == "play":
        session = await _get_or_open_session(db, session_key, "kodi", user.id, media.id)
        session.state = "playing"
        session.updated_at = datetime.utcnow()
        await db.commit()
        await scrobble_delivery.forward(settings, media, "start", data["progress_percent"], db=db)

    elif notification_type == "resume":
        session = await _get_or_open_session(db, session_key, "kodi", user.id, media.id)
        session.state = "playing"
        session.progress_percent = data["progress_percent"]
        session.progress_seconds = data["progress_seconds"]
        session.updated_at = datetime.utcnow()
        await db.commit()
        await scrobble_delivery.forward(settings, media, "start", data["progress_percent"], db=db)

    elif notification_type == "pause":
        result = await db.execute(select(PlaybackSession).where(PlaybackSession.session_key == session_key))
        session = result.scalar_one_or_none()
        if session:
            session.state = "paused"
            session.progress_percent = data["progress_percent"]
            session.progress_seconds = data["progress_seconds"]
            session.updated_at = datetime.utcnow()
            await db.commit()
        await scrobble_delivery.forward(settings, media, "pause", data["progress_percent"], db=db)

    elif notification_type == "progress":
        session = await _get_or_open_session(db, session_key, "kodi", user.id, media.id)
        session.state = "paused" if data["is_paused"] else "playing"
        session.progress_percent = data["progress_percent"]
        session.progress_seconds = data["progress_seconds"]
        session.updated_at = datetime.utcnow()
        await db.commit()

    elif notification_type == "stop":
        # A library mark-as-watched of something Scrob already has a play for
        # adds nothing. Without this, Kodi echoing back a playcount Scrob just
        # synced to it was recorded as a brand new play each time, and the
        # growing count made the next sync write it to Kodi again, forever.
        if data.get("library_update"):
            already = await db.execute(
                select(func.count()).select_from(WatchEvent).where(
                    WatchEvent.user_id == user.id,
                    WatchEvent.media_id == media.id,
                )
            )
            if already.scalar_one() > 0:
                return {"status": "ignored", "reason": "already watched", "title": data["title"]}

        session = await _close_session(db, session_key)
        progress_percent = data["progress_percent"] or (session.progress_percent if session else 0.0)
        progress_seconds = data["progress_seconds"] or (session.progress_seconds if session else 0)
        completed = data.get("ended") or progress_percent >= 0.90
        if completed or progress_percent > 0.05:
            await _write_watch_event(db, user.id, media.id, progress_percent, progress_seconds, completed, window_minutes)
        await db.commit()
        await scrobble_delivery.forward(settings, media, "stop", progress_percent, db=db)

    return {"status": "ok", "event": notification_type, "title": data["title"]}


@router.post("/kodi")
async def kodi_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user_or_api_key),
):
    return await _handle_kodi_webhook(request, db, user)


@router.get("/kodi/history")
async def kodi_library_history(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user_or_api_key),
):
    movie_rows = (await db.execute(
        select(Media.tmdb_id, func.sum(WatchEvent.play_count).label("play_count"))
        .join(WatchEvent, WatchEvent.media_id == Media.id)
        .where(
            WatchEvent.user_id == user.id,
            Media.media_type == MediaType.movie,
            Media.tmdb_id.isnot(None),
        )
        .group_by(Media.tmdb_id)
    )).all()

    episode_rows = (await db.execute(
        select(Show.tmdb_id, Media.season_number, Media.episode_number, func.sum(WatchEvent.play_count).label("play_count"))
        .join(WatchEvent, WatchEvent.media_id == Media.id)
        .join(Show, Show.id == Media.show_id)
        .where(
            WatchEvent.user_id == user.id,
            Media.media_type == MediaType.episode,
            Media.season_number.isnot(None),
            Media.episode_number.isnot(None),
        )
        .group_by(Show.tmdb_id, Media.season_number, Media.episode_number)
    )).all()

    return {
        "movies": [{"tmdb_id": r.tmdb_id, "play_count": r.play_count} for r in movie_rows],
        "episodes": [{"show_tmdb_id": r.tmdb_id, "season_number": r.season_number, "episode_number": r.episode_number, "play_count": r.play_count} for r in episode_rows],
    }


@router.get("/kodi/ratings")
async def kodi_library_ratings(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user_or_api_key),
):
    """Item-level ratings for the signed-in user, shaped for the Kodi add-on to
    mirror back into its local library as ``userrating``. Movie and episode
    ratings only - season/show-level rows (``Rating.season_number`` or
    ``episode_order`` set) are left out."""
    movie_rows = (await db.execute(
        select(Media.tmdb_id, Rating.rating)
        .join(Rating, Rating.media_id == Media.id)
        .where(
            Rating.user_id == user.id,
            Rating.rating.isnot(None),
            Rating.season_number.is_(None),
            Rating.episode_order.is_(None),
            Media.media_type == MediaType.movie,
            Media.tmdb_id.isnot(None),
        )
    )).all()

    episode_rows = (await db.execute(
        select(Show.tmdb_id, Media.season_number, Media.episode_number, Rating.rating)
        .join(Rating, Rating.media_id == Media.id)
        .join(Show, Show.id == Media.show_id)
        .where(
            Rating.user_id == user.id,
            Rating.rating.isnot(None),
            Rating.season_number.is_(None),
            Rating.episode_order.is_(None),
            Media.media_type == MediaType.episode,
            Media.season_number.isnot(None),
            Media.episode_number.isnot(None),
            Show.tmdb_id.isnot(None),
        )
    )).all()

    return {
        "movies": [{"tmdb_id": r.tmdb_id, "rating": r.rating} for r in movie_rows],
        "episodes": [
            {"show_tmdb_id": r.tmdb_id, "season_number": r.season_number,
             "episode_number": r.episode_number, "rating": r.rating}
            for r in episode_rows
        ],
    }


class KodiRatingPayload(BaseModel):
    tmdb_id: Optional[str] = None
    tvdb_id: Optional[str] = None
    imdb_id: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    media_type: str
    rating: float
    series_name: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None


@router.post("/kodi/rating")
async def kodi_rating(
    payload: KodiRatingPayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user_or_api_key),
):
    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    settings = settings_result.scalar_one_or_none()
    tmdb_key = await settings_store.get_effective_tmdb_key(db, settings)

    data = {
        "media_type": payload.media_type,
        "title": payload.title or "",
        "year": payload.year,
        "tmdb_id": payload.tmdb_id,
        "imdb_id": payload.imdb_id,
        "tvdb_id": payload.tvdb_id,
        "series_name": payload.series_name,
        "season_number": payload.season_number,
        "episode_number": payload.episode_number,
    }
    media = await find_or_create_media_kodi(data, db, api_key=tmdb_key, user_id=user.id)
    if media is None:
        raise HTTPException(status_code=422, detail="Could not identify media")

    existing_result = await db.execute(
        select(Rating).where(Rating.media_id == media.id, Rating.user_id == user.id)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        existing.rating = payload.rating
        existing.rated_at = datetime.utcnow()
    else:
        db.add(Rating(media_id=media.id, user_id=user.id, rating=payload.rating))
    await db.commit()
    return {"status": "ok"}

