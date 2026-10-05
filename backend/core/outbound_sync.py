"""Peer propagation of accepted watch, rating, and library changes.

Provider projection and transport stay in their provider modules. This service
owns destination selection, reconciliation gates, echo prevention, and batching.
"""
from core import bingebase, watch_echo
import asyncio
import logging
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import settings_store, trakt_auth, mdblist_payloads
from core import nuvio, nuvio_payloads, nuvio_projection, stremio_delivery, plex, jellyfin, emby, tmdb
from core import trakt as trakt_client
from core.connection_identity import refresh_stream_connection
from core.db_queries import latest_watched_at as _latest_watched_at, select_in_chunks as _select_in_chunks
from db import engine
from models.base import CollectionSource, MediaType
from models.collection import Collection, CollectionFile
from models.connections import MediaServerConnection
from models.events import WatchEvent
from models.media import Media
from models.show import Show
from models.users import UserSettings
from models.ratings import Rating, RatingChanges, RatingKey
from models.plex_pending_push import PlexPendingPush
from core.sync_delivery_targets import connection_clause, cloud_matches, dispatch_queues

logger = logging.getLogger(__name__)
TMDB_CONCURRENCY = 5
_MAX_IN_PARAMS = 30_000


async def resolve_tmdb_season_ids(
    media_by_id: dict[int, Media],
    rating_keys: set[RatingKey],
    api_key: str | None,
) -> dict[RatingKey, int]:
    """Resolve TMDB season resource IDs for season rating operations."""
    season_keys = {
        key
        for key in rating_keys
        if key[1] is not None
        and (media := media_by_id.get(key[0]))
        and media.media_type == MediaType.series
        and media.tmdb_id
    }
    if not season_keys:
        return {}

    resolved: dict[RatingKey, int] = {}
    semaphore = asyncio.Semaphore(TMDB_CONCURRENCY)

    async def resolve(key: RatingKey) -> None:
        media = media_by_id[key[0]]
        async with semaphore:
            try:
                season = await tmdb.get_season(
                    media.tmdb_id,
                    key[1],
                    api_key=api_key,
                )
            except Exception as exc:
                logger.warning(
                    "Could not resolve TMDB season ID for show=%s season=%s: %s",
                    media.tmdb_id,
                    key[1],
                    exc,
                )
                return
        season_tmdb_id = season.get("id")
        if season_tmdb_id:
            resolved[key] = int(season_tmdb_id)

    await asyncio.gather(*(resolve(key) for key in season_keys))
    return resolved



async def push_nuvio_library_delta(
    db: AsyncSession,
    conn: MediaServerConnection,
    current_items: list[dict],
    changed_content_ids: set[str],
) -> bool:
    items_by_id = {item["content_id"]: item for item in current_items}
    additions = [
        items_by_id[content_id]
        for content_id in changed_content_ids
        if content_id in items_by_id
    ]
    removals = changed_content_ids - items_by_id.keys()

    async def _persist_refresh(session: nuvio.NuvioSession) -> None:
        conn.token = session.refresh_token
        await db.commit()

    async with nuvio.connection_lock(conn.id):
        # See core/nuvio.py's connection_lock docstring - conn may have been
        # loaded before another request already rotated this single-use
        # refresh token while this one waited.
        await refresh_stream_connection(db, conn)
        await nuvio.merge_library(
            conn.url,
            conn.token,
            nuvio_payloads.profile_id(conn),
            additions=additions,
            removed_content_ids=set(removals),
            on_refresh=_persist_refresh,
        )
        # Commit the managed IDs before releasing the provider lock, so an
        # account switch cannot be followed by this old account's bookkeeping.
        conn.stremio_pushed_library_ids = sorted(items_by_id)
        await db.commit()
    return True



async def fan_out_streaming_library(
    db: AsyncSession,
    user_id: int,
    exclude_connection_id: int | None,
    *,
    new_collected_ids: set[int],
    removed_collected_ids: set[int],
    api_key: str | None,
    exclude_connection_ids: set[int] | None = None,
    source_observed_at_by_media: dict[int, datetime] | None = None,
    source_connection_ids_by_media: dict[int, set[int]] | None = None,
    desired_by_media: dict[int, bool] | None = None,
) -> None:
    """Persist and deliver an observed library delta to peer streaming accounts.

    Library membership is deliberately separate from tracked-list state. Only
    Stremio/Nuvio destinations with collection push enabled participate, and
    each destination must already have an approved first reconciliation before
    its durable delivery can be applied.
    """
    changed_media_ids = set(new_collected_ids) | set(removed_collected_ids)
    if not changed_media_ids:
        return

    excluded_ids = set(exclude_connection_ids or ())
    if exclude_connection_id is not None:
        excluded_ids.add(exclude_connection_id)
    source_observations = dict(source_observed_at_by_media or {})
    source_ids_by_media = {
        media_id: set(connection_ids)
        for media_id, connection_ids in (source_connection_ids_by_media or {}).items()
    }
    if exclude_connection_id is not None:
        for media_id in changed_media_ids:
            source_ids_by_media.setdefault(media_id, set()).add(exclude_connection_id)
    elif source_connection_ids_by_media is None:
        # Compatibility for callers that still supply only a global exclusion
        # set. Scheduled pull cycles pass precise per-title source identities.
        for media_id in changed_media_ids:
            source_ids_by_media.setdefault(media_id, set()).update(excluded_ids)

    if exclude_connection_id is not None:
        from core.tracking_snapshot import require_stream_reconciliation
        from models.tracking import StreamBaseline

        source_connection = await db.get(MediaServerConnection, exclude_connection_id)
        if source_connection is None:
            return
        try:
            await require_stream_reconciliation(db, source_connection)
        except HTTPException:
            logger.info(
                "Streaming library mirror held until source reconciliation: connection %s",
                exclude_connection_id,
            )
            return
        source_baseline = await db.get(StreamBaseline, exclude_connection_id)
        if source_baseline is None or not source_baseline.approved:
            return
        for media_id in changed_media_ids:
            source_observations.setdefault(media_id, source_baseline.observed_at)
        from core.pull_cycle import defer_library_fan_out
        if defer_library_fan_out(
            user_id,
            source_connection_id=exclude_connection_id,
            new_collected_ids=new_collected_ids,
            removed_collected_ids=removed_collected_ids,
            api_key=api_key,
            source_observed_at=source_baseline.observed_at,
        ):
            return
    if not source_observations:
        return

    from core.streaming_library import (
        dispatch_pending_library_deliveries,
        queue_provider_library_changes,
    )

    await queue_provider_library_changes(
        db,
        user_id,
        changed_media_ids,
        exclude_connection_ids=excluded_ids,
        source_observed_at_by_media=source_observations,
        source_connection_ids_by_media=source_ids_by_media,
        desired_by_media=desired_by_media,
    )
    # The queue is durable before any provider request starts. Failures remain
    # pending for the independent delivery retry worker.
    await db.commit()
    if dispatch_queues():
        await dispatch_pending_library_deliveries(db, user_id)



async def fan_out_changes(
    db: AsyncSession,
    user_id: int,
    exclude_connection_id: int | None,
    new_watched_ids: set[int],
    new_ratings: RatingChanges,
    settings: "UserSettings | None" = None,
    exclude_cloud_source: CollectionSource | None = None,
    removed_ratings: set[RatingKey] | None = None,
    new_collected_ids: set[int] | None = None,
    removed_collected_ids: set[int] | None = None,
    exclude_connection_ids: set[int] | None = None,
    exclude_cloud_sources: set[CollectionSource] | None = None,
    durable_watch_media_ids: set[int] | None = None,
    require_success: bool = False,
) -> None:
    """Push an inbound sync delta to every enabled media server and cloud target.

    ``exclude_connection_id`` prevents media-server echo. ``exclude_cloud_source``
    prevents a cloud pull from writing the same delta back to its source.
    """
    removed_ratings = removed_ratings or set()
    new_collected_ids = new_collected_ids or set()
    removed_collected_ids = removed_collected_ids or set()
    durable_watch_media_ids = set(durable_watch_media_ids or ())
    directly_pushed_watch_ids = set(new_watched_ids) - durable_watch_media_ids
    delivery_failed = False
    if not new_watched_ids and not new_ratings and not removed_ratings and not new_collected_ids and not removed_collected_ids:
        return

    from core.pull_cycle import defer_fan_out
    if defer_fan_out(
        user_id,
        exclude_connection_id=exclude_connection_id,
        exclude_cloud_source=exclude_cloud_source,
        new_watched_ids=new_watched_ids,
        new_ratings=new_ratings,
        removed_ratings=removed_ratings,
        new_collected_ids=new_collected_ids,
        removed_collected_ids=removed_collected_ids,
    ):
        return

    excluded_connection_ids = set(exclude_connection_ids or ())
    if exclude_connection_id is not None:
        excluded_connection_ids.add(exclude_connection_id)
    excluded_cloud_sources = set(exclude_cloud_sources or ())
    excluded_cloud_sources.update(source for source in CollectionSource if not cloud_matches(source))
    if exclude_cloud_source is not None:
        excluded_cloud_sources.add(exclude_cloud_source)


    all_changed_ids = (
        set(new_watched_ids)
        | {media_id for media_id, _ in new_ratings}
        | {media_id for media_id, _ in removed_ratings}
        | new_collected_ids
        | removed_collected_ids
    )
    media_items = await _select_in_chunks(
        db,
        lambda chunk: select(Media).where(Media.id.in_(chunk)),
        list(all_changed_ids),
    )
    media_by_id: dict[int, Media] = {media.id: media for media in media_items}

    # Load parent shows for episode media — needed by both Trakt and MDBList fan-out
    # to identify episodes (which have no meaningful standalone tmdb id on either API).
    show_ids = {m.show_id for m in media_items if m.show_id}
    shows_by_id: dict[int, "Show"] = {}
    if show_ids:
        shows_list = await _select_in_chunks(
            db,
            lambda chunk: select(Show).where(Show.id.in_(chunk)),
            list(show_ids),
        )
        shows_by_id = {s.id: s for s in shows_list}

    # ── Media server fan-out ─────────────────────────────────────────────────
    conns_filter = [MediaServerConnection.user_id == user_id, connection_clause(MediaServerConnection.id)]
    if excluded_connection_ids:
        conns_filter.append(MediaServerConnection.id.not_in(excluded_connection_ids))
    other_conns_result = await db.execute(
        select(MediaServerConnection).where(*conns_filter)
    )
    other_conns = other_conns_result.scalars().all()
    push_candidates = [
        conn
        for conn in other_conns
        if getattr(conn, "push_collection", False) or conn.push_watched or conn.push_ratings or conn.push_playback
    ]

    push_tasks = []
    server_rating_changes = {key: 0.0 for key in removed_ratings}
    server_rating_changes.update(new_ratings)

    if push_candidates:
        # Chunk the IN clause to stay under asyncpg's 32767-parameter limit.
        # A large first-time sync can produce tens of thousands of changed IDs.
        # Keyed by connection_id, not source type - a ratingKey/item ID is only
        # valid on the specific server it was read from, and a user can have
        # several connections of the same type (e.g. two Plex servers). Rows
        # with no connection_id (pre-migration data for an ambiguous multi-
        # connection user) are skipped rather than risk pushing to the wrong
        # server; they'll get one after their next sync.
        source_ids_map: dict[tuple[int, int], list[str]] = {}
        all_changed_list = list(all_changed_ids)
        for i in range(0, len(all_changed_list), _MAX_IN_PARAMS):
            chunk = all_changed_list[i : i + _MAX_IN_PARAMS]
            files_result = await db.execute(
                select(CollectionFile.source_id, CollectionFile.connection_id, Collection.media_id)
                .join(Collection, Collection.id == CollectionFile.collection_id)
                .where(
                    Collection.user_id == user_id,
                    Collection.media_id.in_(chunk),
                    CollectionFile.source_id.isnot(None),
                    CollectionFile.connection_id.isnot(None),
                )
            )
            for source_id, connection_id, media_id in files_result.all():
                source_ids_map.setdefault((connection_id, media_id), []).append(source_id)

        sem = asyncio.Semaphore(20)

        async def _guarded(coro):
            async with sem:
                return await coro

        nuvio_watched_items: list[dict] | None = None
        has_nuvio_collection_target = any(
            conn.type == "nuvio" and conn.push_collection
            for conn in push_candidates
        )
        nuvio_api_key = (
            await settings_store.get_effective_tmdb_key(db, settings)
            if any(
                conn.type == "nuvio" and (conn.push_watched or conn.push_collection)
                for conn in push_candidates
            )
            else None
        )
        stremio_api_key = (
            await settings_store.get_effective_tmdb_key(db, settings)
            if any(conn.type == "stremio" for conn in push_candidates)
            else None
        )
        collection_changed_ids = new_collected_ids | removed_collected_ids
        collection_shows_by_tmdb: dict[int, Show] = {}
        if has_nuvio_collection_target:
            collection_series_tmdb_ids = {
                media.tmdb_id
                for media_id in collection_changed_ids
                if (media := media_by_id.get(media_id))
                if media.media_type == MediaType.series and media.tmdb_id is not None
            }
            if collection_series_tmdb_ids:
                collection_shows = await _select_in_chunks(
                    db,
                    lambda chunk: select(Show).where(Show.tmdb_id.in_(chunk)),
                    list(collection_series_tmdb_ids),
                )
                collection_shows_by_tmdb = {
                    show.tmdb_id: show
                    for show in collection_shows
                    if show.tmdb_id is not None
                }
            await nuvio_projection.ensure_imdb_ids(
                [
                    media
                    for media_id in collection_changed_ids
                    if (media := media_by_id.get(media_id))
                ],
                shows_by_id,
                nuvio_api_key,
                collection_shows_by_tmdb,
            )
        nuvio_changed_content_ids = {
            content_id
            for media_id in collection_changed_ids
            if (media := media_by_id.get(media_id))
            if (
                content_id := nuvio_payloads.library_content_id(
                    media,
                    (
                        shows_by_id.get(media.show_id)
                        if media.media_type == MediaType.episode
                        else collection_shows_by_tmdb.get(media.tmdb_id)
                    ),
                )
            )
        }
        nuvio_library_items = (
            await nuvio_projection.build_library_items(db, user_id, api_key=nuvio_api_key)
            if nuvio_changed_content_ids
            and has_nuvio_collection_target
            else []
        )

        async def _push_to_nuvio(conn: MediaServerConnection, items: list[dict]) -> bool:
            async def _persist_refresh(session: nuvio.NuvioSession) -> None:
                conn.token = session.refresh_token
                await db.commit()

            async with nuvio.connection_lock(conn.id):
                # See core/nuvio.py's connection_lock docstring - conn may
                # have been loaded before another request already rotated
                # this single-use refresh token while this one waited.
                await refresh_stream_connection(db, conn)
                await nuvio.push_watched_items(
                    conn.url,
                    conn.token,
                    nuvio_payloads.profile_id(conn),
                    items,
                    on_refresh=_persist_refresh,
                )
            return True

        watched_at_by_media = (
            await _latest_watched_at(db, user_id, list(new_watched_ids))
            if new_watched_ids and any(conn.type in ("jellyfin", "emby") and conn.push_watched for conn in push_candidates)
            else {}
        )

        for conn in push_candidates:
            if conn.type in ('stremio', 'nuvio', 'jellyfin', 'emby', 'plex'):
                from core.tracking_snapshot import require_stream_reconciliation
                try:
                    await require_stream_reconciliation(db, conn)
                except HTTPException:
                    logger.info('Streaming push held for reconciliation: connection %s', conn.id)
                    continue
            if conn.type == "stremio":
                try:
                    await stremio_delivery.push_connection(
                        db,
                        conn,
                        user_id,
                        api_key=stremio_api_key,
                        changed_media_ids=all_changed_ids,
                        skip_watch_media_ids=durable_watch_media_ids,
                    )
                except Exception:
                    delivery_failed = True
                    logger.exception(
                        "Stremio fan-out failed for connection %s",
                        conn.id,
                    )
                continue
            if conn.type == "nuvio":
                if conn.push_watched:
                    if nuvio_watched_items is None:
                        nuvio_watched_items = await nuvio_projection.build_watched_items(
                            db,
                            user_id,
                            directly_pushed_watch_ids,
                            api_key=nuvio_api_key,
                        )
                    if nuvio_watched_items:
                        push_tasks.append(_guarded(_push_to_nuvio(conn, nuvio_watched_items)))
                if conn.push_collection and nuvio_changed_content_ids:
                    push_tasks.append(
                        _guarded(
                            push_nuvio_library_delta(
                                db,
                                conn,
                                nuvio_library_items,
                                nuvio_changed_content_ids,
                            )
                        )
                    )
                continue
            if conn.push_watched:
                for mid in new_watched_ids:
                    for sid in source_ids_map.get((conn.id, mid), []):
                        if conn.type == "plex":
                            push_tasks.append(_guarded(push_plex_watched_and_record(conn, sid, user_id, mid)))
                        elif conn.type == "jellyfin":
                            # Registered before the call, not inside it - Jellyfin/Emby's
                            # UserDataSaved webhook can echo this back fast enough that a
                            # post-await registration would already be too late (#247/#251).
                            watch_echo.mark_pushed_watched(user_id, mid)
                            push_tasks.append(_guarded(jellyfin.mark_watched(conn.url, conn.token, conn.server_user_id, sid, played_at=watched_at_by_media.get(mid))))
                        elif conn.type == "emby":
                            watch_echo.mark_pushed_watched(user_id, mid)
                            push_tasks.append(_guarded(emby.mark_watched(conn.url, conn.token, conn.server_user_id, sid, played_at=watched_at_by_media.get(mid))))
            if conn.push_ratings:
                for (mid, season_number), rating in server_rating_changes.items():
                    media = media_by_id.get(mid)
                    if season_number is not None:
                        if conn.type == "plex" and media and media.tmdb_id:
                            async def _set_plex_season_rating(
                                target_conn: MediaServerConnection = conn,
                                target_media: Media = media,
                                target_season: int = season_number,
                                target_rating: float = rating,
                            ) -> bool:
                                rating_key = await plex.resolve_season_rating_key(
                                    target_conn.url,
                                    target_conn.token,
                                    target_media.tmdb_id,
                                    target_season,
                                )
                                if not rating_key:
                                    return False
                                return await plex.set_rating(
                                    target_conn.url,
                                    target_conn.token,
                                    rating_key,
                                    target_rating,
                                )

                            push_tasks.append(_guarded(_set_plex_season_rating()))
                        continue
                    for sid in source_ids_map.get((conn.id, mid), []):
                        if conn.type == "plex":
                            push_tasks.append(_guarded(plex.set_rating(conn.url, conn.token, sid, rating)))
                        elif conn.type == "jellyfin":
                            push_tasks.append(_guarded(jellyfin.set_rating(conn.url, conn.token, conn.server_user_id, sid, rating)))
                        elif conn.type == "emby":
                            push_tasks.append(_guarded(emby.set_rating(conn.url, conn.token, conn.server_user_id, sid, rating)))

    season_tmdb_ids: dict[RatingKey, int] = {}
    # ── Trakt fan-out ────────────────────────────────────────────────────────
    from core.cloud_reconciliation import cloud_push_is_approved
    trakt_approved = bool(settings) and await cloud_push_is_approved(db, user_id, "trakt")
    mdblist_approved = bool(settings) and await cloud_push_is_approved(db, user_id, "mdblist")
    simkl_approved = bool(settings) and await cloud_push_is_approved(db, user_id, "simkl")
    push_trakt_watched = settings and trakt_approved and CollectionSource.trakt not in excluded_cloud_sources and settings.trakt_push_watched and settings.trakt_access_token and settings.trakt_client_id
    push_trakt_ratings = settings and trakt_approved and CollectionSource.trakt not in excluded_cloud_sources and settings.trakt_push_ratings and settings.trakt_access_token and settings.trakt_client_id
    push_trakt_collection = settings and trakt_approved and CollectionSource.trakt not in excluded_cloud_sources and settings.trakt_push_collection and settings.trakt_access_token and settings.trakt_client_id

    if (push_trakt_watched or push_trakt_ratings or push_trakt_collection) and all_changed_ids:
        # Validate / refresh the token before the fan-out (own session - this
        # runs amid concurrently-gathered push tasks). Skipping this let the
        # token expire unnoticed and stall Trakt pushes for days (#326). On
        # failure, disable every Trakt sub-push below.
        try:
            trakt_access_token = await trakt_auth.ensure_valid_trakt_token_for_user(user_id)
        except Exception as exc:  # best-effort fan-out - don't fail the whole sync
            delivery_failed = True
            logger.warning("Skipping Trakt fan-out for user %s: %s", user_id, exc)
            trakt_access_token = None
            push_trakt_watched = push_trakt_ratings = push_trakt_collection = False

        trakt_history_movies: list[tuple[int, datetime | None]] = []
        trakt_history_episodes: list[tuple[int, int, int, datetime | None]] = []
        if push_trakt_watched:
            trakt_watched_at_by_media = await _latest_watched_at(db, user_id, list(new_watched_ids))
            for mid in new_watched_ids:
                media = media_by_id.get(mid)
                if not media or not media.tmdb_id:
                    continue
                watched_at = trakt_watched_at_by_media.get(mid)
                if media.media_type == MediaType.movie:
                    trakt_history_movies.append((media.tmdb_id, watched_at))
                elif media.media_type == MediaType.episode and media.show_id and media.season_number is not None and media.episode_number is not None:
                    show = shows_by_id.get(media.show_id)
                    if show and show.tmdb_id:
                        trakt_history_episodes.append((show.tmdb_id, media.season_number, media.episode_number, watched_at))

        if trakt_history_movies or trakt_history_episodes:
            push_tasks.append(trakt_client.add_to_history_batch(
                settings.trakt_client_id, trakt_access_token,
                trakt_history_movies, trakt_history_episodes,
            ))

        if push_trakt_collection:
            trakt_collection_add_movies: list[int] = []
            trakt_collection_add_episodes: list[tuple[int, int, int]] = []
            for mid in new_collected_ids:
                media = media_by_id.get(mid)
                if not media or not media.tmdb_id:
                    continue
                if media.media_type == MediaType.movie:
                    trakt_collection_add_movies.append(media.tmdb_id)
                elif media.media_type == MediaType.episode and media.show_id and media.season_number is not None and media.episode_number is not None:
                    show = shows_by_id.get(media.show_id)
                    if show and show.tmdb_id:
                        trakt_collection_add_episodes.append((show.tmdb_id, media.season_number, media.episode_number))

            if trakt_collection_add_movies or trakt_collection_add_episodes:
                push_tasks.append(trakt_client.add_to_collection_batch(
                    settings.trakt_client_id, trakt_access_token,
                    trakt_collection_add_movies, trakt_collection_add_episodes,
                ))

            trakt_collection_remove_movies: list[int] = []
            trakt_collection_remove_episodes: list[tuple[int, int, int]] = []
            for mid in removed_collected_ids:
                media = media_by_id.get(mid)
                if not media or not media.tmdb_id:
                    continue
                if media.media_type == MediaType.movie:
                    trakt_collection_remove_movies.append(media.tmdb_id)
                elif media.media_type == MediaType.episode and media.show_id and media.season_number is not None and media.episode_number is not None:
                    show = shows_by_id.get(media.show_id)
                    if show and show.tmdb_id:
                        trakt_collection_remove_episodes.append((show.tmdb_id, media.season_number, media.episode_number))

            if trakt_collection_remove_movies or trakt_collection_remove_episodes:
                push_tasks.append(trakt_client.remove_from_collection_batch(
                    settings.trakt_client_id, trakt_access_token,
                    trakt_collection_remove_movies, trakt_collection_remove_episodes,
                ))

        trakt_movie_ratings: list[tuple[int, float]] = []
        trakt_show_ratings: list[tuple[int, float]] = []
        trakt_season_ratings: list[tuple[int, float]] = []
        if push_trakt_ratings:
            all_rating_keys = set(new_ratings) | removed_ratings
            season_tmdb_ids = await resolve_tmdb_season_ids(
                media_by_id,
                all_rating_keys,
                await settings_store.get_effective_tmdb_key(db, settings),
            )
            for key, rating in new_ratings.items():
                mid, season_number = key
                media = media_by_id.get(mid)
                if not media or not media.tmdb_id:
                    continue
                if season_number is not None:
                    if season_tmdb_id := season_tmdb_ids.get(key):
                        trakt_season_ratings.append((season_tmdb_id, rating))
                elif media.media_type == MediaType.movie:
                    trakt_movie_ratings.append((media.tmdb_id, rating))
                elif media.media_type == MediaType.series:
                    trakt_show_ratings.append((media.tmdb_id, rating))

        if trakt_movie_ratings or trakt_show_ratings or trakt_season_ratings:
            push_tasks.append(
                trakt_client.set_ratings_batch(
                    settings.trakt_client_id,
                    trakt_access_token,
                    trakt_movie_ratings,
                    trakt_show_ratings,
                    trakt_season_ratings,
                )
            )

        if push_trakt_ratings:
            removed_trakt_movies: list[int] = []
            removed_trakt_shows: list[int] = []
            removed_trakt_seasons: list[int] = []
            for key in removed_ratings:
                media_id, season_number = key
                media = media_by_id.get(media_id)
                if not media or not media.tmdb_id:
                    continue
                if season_number is not None:
                    if season_tmdb_id := season_tmdb_ids.get(key):
                        removed_trakt_seasons.append(season_tmdb_id)
                elif media.media_type == MediaType.movie:
                    removed_trakt_movies.append(media.tmdb_id)
                elif media.media_type == MediaType.series:
                    removed_trakt_shows.append(media.tmdb_id)
            if removed_trakt_movies or removed_trakt_shows or removed_trakt_seasons:
                push_tasks.append(
                    trakt_client.remove_ratings_batch(
                        settings.trakt_client_id,
                        trakt_access_token,
                        removed_trakt_movies,
                        removed_trakt_shows,
                        removed_trakt_seasons,
                    )
                )

    # ── MDBList fan-out ──────────────────────────────────────────────────────
    push_mdblist_watched = settings and mdblist_approved and CollectionSource.mdblist not in excluded_cloud_sources and settings.mdblist_push_watched and settings.mdblist_api_key
    push_mdblist_ratings = settings and mdblist_approved and CollectionSource.mdblist not in excluded_cloud_sources and settings.mdblist_push_ratings and settings.mdblist_api_key
    push_mdblist_collection = settings and mdblist_approved and CollectionSource.mdblist not in excluded_cloud_sources and settings.mdblist_push_collection and settings.mdblist_api_key

    if (push_mdblist_watched or push_mdblist_ratings or push_mdblist_collection) and all_changed_ids:
        from core import mdblist as mdblist_client

        mdblist_media_by_id = media_by_id

        if push_mdblist_watched:
            watched_at_by_media = await _latest_watched_at(db, user_id, list(new_watched_ids))

            watched_payload = mdblist_payloads.empty_payload()
            for media_id in new_watched_ids:
                media = mdblist_media_by_id.get(media_id)
                item = (
                    mdblist_payloads.payload_item(
                        media,
                        show=shows_by_id.get(media.show_id),
                        watched_at=watched_at_by_media.get(media_id, datetime.utcnow()),
                    )
                    if media
                    else None
                )
                if item:
                    watched_payload[item[0]].append(item[1])
            watched_payload["shows"] = mdblist_payloads.merge_show_entries(watched_payload["shows"])
            push_tasks.append(mdblist_client.push_watched(settings.mdblist_api_key, watched_payload))

        if push_mdblist_collection and new_collected_ids:
            collected_at_result = await db.execute(
                select(Collection.media_id, Collection.added_at).where(
                    Collection.user_id == user_id,
                    Collection.media_id.in_(list(new_collected_ids)),
                )
            )
            collected_at_by_media = {media_id: added_at for media_id, added_at in collected_at_result.all()}

            collection_add_payload = mdblist_payloads.empty_payload()
            for media_id in new_collected_ids:
                media = mdblist_media_by_id.get(media_id)
                item = (
                    mdblist_payloads.payload_item(
                        media,
                        show=shows_by_id.get(media.show_id),
                        collected_at=collected_at_by_media.get(media_id, datetime.utcnow()),
                    )
                    if media
                    else None
                )
                if item:
                    collection_add_payload[item[0]].append(item[1])
            collection_add_payload["shows"] = mdblist_payloads.merge_show_entries(collection_add_payload["shows"])
            push_tasks.append(mdblist_client.push_collection(settings.mdblist_api_key, collection_add_payload))

        if push_mdblist_collection and removed_collected_ids:
            collection_remove_payload = mdblist_payloads.empty_payload()
            for media_id in removed_collected_ids:
                media = mdblist_media_by_id.get(media_id)
                item = mdblist_payloads.payload_item(media, show=shows_by_id.get(media.show_id)) if media else None
                if item:
                    collection_remove_payload[item[0]].append(item[1])
            collection_remove_payload["shows"] = mdblist_payloads.merge_show_entries(collection_remove_payload["shows"])
            push_tasks.append(mdblist_client.remove_collection(settings.mdblist_api_key, collection_remove_payload))

        if push_mdblist_ratings and new_ratings:
            rated_media_ids = list({media_id for media_id, _ in new_ratings})
            rated_at_by_key: dict[RatingKey, datetime] = {}
            for i in range(0, len(rated_media_ids), _MAX_IN_PARAMS):
                chunk = rated_media_ids[i : i + _MAX_IN_PARAMS]
                rated_at_result = await db.execute(
                    select(Rating.media_id, Rating.season_number, Rating.rated_at).where(
                        Rating.user_id == user_id,
                        Rating.media_id.in_(chunk),
                        Rating.episode_order.is_(None),
                    )
                )
                rated_at_by_key.update(
                    {
                        (media_id, season_number): rated_at
                        for media_id, season_number, rated_at in rated_at_result.all()
                    }
                )
            ratings_payload = mdblist_payloads.empty_payload()
            for key, rating in new_ratings.items():
                media_id, season_number = key
                media = mdblist_media_by_id.get(media_id)
                item = (
                    mdblist_payloads.payload_item(
                        media,
                        show=shows_by_id.get(media.show_id),
                        rating=rating,
                        rated_at=rated_at_by_key.get(key),
                        season_number=season_number,
                    )
                    if media
                    else None
                )
                if item:
                    ratings_payload[item[0]].append(item[1])
            ratings_payload["shows"] = mdblist_payloads.merge_show_entries(ratings_payload["shows"])
            push_tasks.append(mdblist_client.push_ratings(settings.mdblist_api_key, ratings_payload))

        if push_mdblist_ratings and removed_ratings:
            removed_payload = mdblist_payloads.empty_payload()
            for media_id, season_number in removed_ratings:
                media = mdblist_media_by_id.get(media_id)
                item = (
                    mdblist_payloads.rating_removal_item(media, season_number, show=shows_by_id.get(media.show_id))
                    if media
                    else None
                )
                if item:
                    removed_payload[item[0]].append(item[1])
            removed_payload["shows"] = mdblist_payloads.merge_show_entries(removed_payload["shows"])
            push_tasks.append(
                mdblist_client.remove_ratings(
                    settings.mdblist_api_key,
                    removed_payload,
                )
            )

    # ── Simkl fan-out ────────────────────────────────────────────────────────
    push_simkl_watched = (
        settings
        and simkl_approved
        and CollectionSource.simkl not in excluded_cloud_sources
        and settings.simkl_push_watched
        and settings.simkl_access_token
        and settings.simkl_client_id
    )
    push_simkl_ratings = (
        settings
        and simkl_approved
        and CollectionSource.simkl not in excluded_cloud_sources
        and settings.simkl_push_ratings
        and settings.simkl_access_token
        and settings.simkl_client_id
    )
    if push_simkl_watched or push_simkl_ratings:
        from core import simkl as simkl_client

        if push_simkl_watched and new_watched_ids:
            simkl_watched_at_by_media = await _latest_watched_at(db, user_id, list(new_watched_ids))
            for mid in new_watched_ids:
                media = media_by_id.get(mid)
                if not media or not media.tmdb_id:
                    continue
                # Simkl has no unknown-date representation, and watched_at=None means
                # "stamp as now" on its side — skip rather than fabricate a date.
                watched_at = simkl_watched_at_by_media.get(mid)
                if watched_at is None:
                    continue
                if media.media_type == MediaType.movie:
                    push_tasks.append(
                        simkl_client.add_movie_to_history(
                            settings.simkl_client_id, settings.simkl_access_token, media.tmdb_id, watched_at,
                        )
                    )
                elif media.media_type == MediaType.episode and media.show_id and media.season_number is not None and media.episode_number is not None:
                    show = shows_by_id.get(media.show_id)
                    if show and show.tmdb_id:
                        push_tasks.append(
                            simkl_client.add_episode_to_history(
                                settings.simkl_client_id, settings.simkl_access_token,
                                show.tmdb_id, media.season_number, media.episode_number, watched_at,
                            )
                        )

        # Simkl has no "rated but not watched" state: rating an item that isn't
        # already in one of its lists auto-files it as watched (today's date), and
        # removing that watched status removes the rating right along with it — so
        # there's no way to represent "rated, never watched" on Simkl. Only push
        # ratings for items scrob also considers watched (independent of whether
        # settings.simkl_push_watched is on, since local watch history can predate
        # or be unrelated to this run's watched-push setting).
        media_ids_with_watch_event: set[int] = set()
        shows_with_watched_episode: set[int] = set()
        if push_simkl_ratings and new_ratings:
            rated_movie_ids = {
                media_id
                for (media_id, season_number) in new_ratings
                if season_number is None and (m := media_by_id.get(media_id)) and m.media_type == MediaType.movie
            }
            if rated_movie_ids:
                watch_check_result = await db.execute(
                    select(WatchEvent.media_id).where(
                        WatchEvent.user_id == user_id,
                        WatchEvent.media_id.in_(list(rated_movie_ids)),
                    ).distinct()
                )
                media_ids_with_watch_event = {row[0] for row in watch_check_result.all()}

            rated_show_tmdb_ids = {
                m.tmdb_id
                for (media_id, season_number) in new_ratings
                if season_number is None and (m := media_by_id.get(media_id)) and m.media_type == MediaType.series and m.tmdb_id
            }
            if rated_show_tmdb_ids:
                watched_show_result = await db.execute(
                    select(Show.tmdb_id)
                    .join(Media, Media.show_id == Show.id)
                    .join(WatchEvent, WatchEvent.media_id == Media.id)
                    .where(WatchEvent.user_id == user_id, Show.tmdb_id.in_(rated_show_tmdb_ids))
                    .distinct()
                )
                shows_with_watched_episode = {row[0] for row in watched_show_result.all()}

        for key, rating in new_ratings.items():
            media_id, season_number = key
            if season_number is not None:
                continue
            media = media_by_id.get(media_id)
            if not media or not media.tmdb_id:
                continue
            if media.media_type == MediaType.movie:
                if media_id not in media_ids_with_watch_event:
                    continue
                push_tasks.append(
                    simkl_client.set_movie_rating(
                        settings.simkl_client_id,
                        settings.simkl_access_token,
                        media.tmdb_id,
                        rating,
                    )
                )
            elif media.media_type == MediaType.series:
                if media.tmdb_id not in shows_with_watched_episode:
                    continue
                push_tasks.append(
                    simkl_client.set_show_rating(
                        settings.simkl_client_id,
                        settings.simkl_access_token,
                        media.tmdb_id,
                        rating,
                    )
                )
        for media_id, season_number in removed_ratings:
            if season_number is not None:
                continue
            media = media_by_id.get(media_id)
            if not media or not media.tmdb_id:
                continue
            if media.media_type == MediaType.movie:
                push_tasks.append(
                    simkl_client.remove_movie_rating(
                        settings.simkl_client_id,
                        settings.simkl_access_token,
                        media.tmdb_id,
                    )
                )
            elif media.media_type == MediaType.series:
                push_tasks.append(
                    simkl_client.remove_show_rating(
                        settings.simkl_client_id,
                        settings.simkl_access_token,
                        media.tmdb_id,
                    )
                )

    # ── Bingebase fan-out ───────────────────────────────────────────────────
    push_bingebase_watched = (
        settings
        and CollectionSource.bingebase not in excluded_cloud_sources
        and getattr(settings, "bingebase_push_watched", False)
        and getattr(settings, "bingebase_webhook_url", None)
    )
    if push_bingebase_watched and new_watched_ids:
        for media_id in new_watched_ids:
            media = media_by_id.get(media_id)
            if media:
                push_tasks.append(bingebase.scrobble(settings, media, "stop", 1.0, db=db))

    if push_tasks:
        target_count = len(push_candidates)
        target_count += 1 if (push_trakt_watched or push_trakt_ratings) else 0
        target_count += 1 if (push_mdblist_watched or push_mdblist_ratings) else 0
        target_count += 1 if (push_simkl_watched or push_simkl_ratings) else 0
        target_count += 1 if push_bingebase_watched else 0
        print(f"  Fanning out {len(push_tasks)} changes to {target_count} other connection(s)...")
        # Chunked rather than one giant gather() — a large one-time import can
        # produce thousands of individual per-item media-server push tasks (Plex/
        # Jellyfin/Emby have no bulk "mark watched" endpoint), and creating that
        # many pending asyncio tasks at once degrades responsiveness for the whole
        # process, not just this request. The per-item concurrency is still capped
        # by each task's own semaphore (see _guarded above); this only bounds how
        # many tasks are queued into the event loop at once.
        FAN_OUT_CHUNK_SIZE = 200
        failed = 0
        for i in range(0, len(push_tasks), FAN_OUT_CHUNK_SIZE):
            chunk = push_tasks[i:i + FAN_OUT_CHUNK_SIZE]
            results = await asyncio.gather(*chunk, return_exceptions=True)
            failed += sum(1 for r in results if isinstance(r, Exception) or (require_success and r is False))
        if failed:
            delivery_failed = True
            print(f"  {failed}/{len(push_tasks)} fan-out push tasks failed (non-fatal)")
    if any(conn.type in ("nuvio", "stremio") for conn in push_candidates):
        await db.commit()
    if require_success and delivery_failed:
        raise RuntimeError("Accepted sync delivery is pending retry")



async def record_plex_pending_push(user_id: int, media_id: int) -> None:
    """Record that we just pushed a "watched" mark to Plex for (user_id,
    media_id), so a later history pull can recognize Plex's echo of it even
    when the original watch happened long before the push (see GitHub #320
    and PlexPendingPush's docstring). Upserts - one row per (user, media).

    Uses its own short-lived session rather than a caller-supplied one: every
    call site fires this from inside a concurrently-gathered push task, and
    AsyncSession isn't safe for concurrent use from multiple coroutines on
    the same session instance.
    """
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        stmt = insert(PlexPendingPush).values(
            user_id=user_id,
            media_id=media_id,
            pushed_at=datetime.utcnow(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "media_id"],
            set_={"pushed_at": stmt.excluded.pushed_at},
        )
        await db.execute(stmt)
        await db.commit()



async def push_plex_watched_and_record(conn: MediaServerConnection, sid: str, user_id: int, media_id: int) -> bool:
    """plex.mark_watched, then record a PlexPendingPush on success - see
    record_plex_pending_push and GitHub #320."""
    ok = await plex.mark_watched(conn.url, conn.token, sid)
    if ok:
        await record_plex_pending_push(user_id, media_id)
    return ok
