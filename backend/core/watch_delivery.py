"""Deliver explicit watch-state corrections after reconciliation approval.

Streaming providers use durable watch intents. Media servers and cloud history
providers receive direct watched/unwatched writes; source exclusions prevent echoes.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import mdblist_payloads, outbound_sync, trakt_auth, watch_echo
from core import plex as plex_client, jellyfin as jellyfin_client, emby as emby_client
from core import trakt as trakt_client
from core.db_queries import latest_watched_at
from core.enrichment import is_unmapped_tvdb_episode
from models.base import CollectionSource, MediaType
from models.collection import Collection, CollectionFile
from models.connections import MediaServerConnection
from models.media import Media
from models.show import Show
from models.users import UserSettings

logger = logging.getLogger(__name__)


async def push_watch_state(
    db: AsyncSession,
    user_id: int,
    media_ids: list[int],
    watched: bool,
    watched_at_by_media: dict[int, datetime | None] | None = None,
    exclude_connection_id: int | None = None,
    exclude_connection_ids: set[int] | None = None,
    skip_stream_watch_writes: bool = False,
    require_success: bool = False,
) -> None:
    """Push to approved destinations with watch delivery enabled.

    Exclude originating connections to prevent webhook feedback loops. Set
    skip_stream_watch_writes when durable intents were already dispatched.
    """
    if not media_ids:
        return

    from core.sync_delivery_targets import connection_clause, cloud_matches
    failed = False
    conns_result = await db.execute(
        select(MediaServerConnection).where(
            MediaServerConnection.user_id == user_id,
            MediaServerConnection.push_watched == True,
            connection_clause(MediaServerConnection.id),
        )
    )
    excluded = set(exclude_connection_ids or ())
    if exclude_connection_id is not None:
        excluded.add(exclude_connection_id)
    connections = [c for c in conns_result.scalars().all() if c.id not in excluded]
    if not skip_stream_watch_writes and any(c.type in ('nuvio', 'stremio') for c in connections):
        from core.watch_intents import queue_watch_intents, dispatch_watch_intents
        await queue_watch_intents(db, user_id, media_ids, exclude_connection_ids=excluded)
        # Persist retryable intent before making a provider write.
        await db.commit()
        await dispatch_watch_intents(db, user_id)
    from core.tracking_snapshot import require_stream_reconciliation
    approved_connections = []
    for connection in connections:
        try:
            await require_stream_reconciliation(db, connection)
        except HTTPException:
            continue
        approved_connections.append(connection)
    connections = approved_connections

    settings_result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    settings = settings_result.scalar_one_or_none()
    from core.cloud_reconciliation import cloud_push_is_approved
    push_trakt = bool(cloud_matches("trakt") and settings and settings.trakt_push_watched and settings.trakt_access_token)
    push_mdblist = bool(cloud_matches("mdblist") and settings and settings.mdblist_push_watched and settings.mdblist_api_key)
    push_simkl = bool(cloud_matches("simkl") and settings and settings.simkl_push_watched and settings.simkl_access_token)
    if push_trakt:
        push_trakt = await cloud_push_is_approved(db, user_id, 'trakt')
    if push_mdblist:
        push_mdblist = await cloud_push_is_approved(db, user_id, 'mdblist')
    if push_simkl:
        push_simkl = await cloud_push_is_approved(db, user_id, 'simkl')

    resolved_watched_at: dict[int, datetime | None] = {}
    if watched:
        if watched_at_by_media is not None:
            resolved_watched_at = watched_at_by_media
        else:
            resolved_watched_at = await latest_watched_at(db, user_id, media_ids)

    # Each entry is (label, coroutine) so a failure can be logged with which
    # provider/connection it came from — asyncio.gather(return_exceptions=True)
    # would otherwise swallow errors here silently.
    tasks: list[tuple[str, Any]] = []

    if connections:
        files_result = await db.execute(
            select(CollectionFile, Collection.media_id)
            .join(Collection, Collection.id == CollectionFile.collection_id)
            .where(
                Collection.user_id == user_id,
                Collection.media_id.in_(media_ids),
            )
        )
        coll_files = files_result.all()

        conn_by_type: dict[str, list[MediaServerConnection]] = {}
        for conn in connections:
            conn_by_type.setdefault(conn.type, []).append(conn)

        for coll_file, coll_media_id in coll_files:
            if not coll_file.source_id:
                continue
            source_type = coll_file.source.value if hasattr(coll_file.source, "value") else str(coll_file.source)
            for conn in conn_by_type.get(source_type, []):
                # Source IDs are meaningful only on the connection that supplied them.
                if getattr(coll_file, "connection_id", None) != conn.id:
                    continue
                if coll_file.source == CollectionSource.plex:
                    label = f"plex connection {conn.id}"
                    if watched:
                        tasks.append((label, outbound_sync.push_plex_watched_and_record(conn, coll_file.source_id, user_id, coll_media_id)))
                    else:
                        tasks.append((label, plex_client.mark_unwatched(conn.url, conn.token, coll_file.source_id)))
                elif coll_file.source == CollectionSource.jellyfin:
                    label = f"jellyfin connection {conn.id}"
                    if watched:
                        watch_echo.mark_pushed_watched(user_id, coll_media_id)
                        tasks.append((label, jellyfin_client.mark_watched(conn.url, conn.token, conn.server_user_id, coll_file.source_id, played_at=resolved_watched_at.get(coll_media_id))))
                    else:
                        tasks.append((label, jellyfin_client.mark_unwatched(conn.url, conn.token, conn.server_user_id, coll_file.source_id)))
                elif coll_file.source == CollectionSource.emby:
                    label = f"emby connection {conn.id}"
                    if watched:
                        watch_echo.mark_pushed_watched(user_id, coll_media_id)
                        tasks.append((label, emby_client.mark_watched(conn.url, conn.token, conn.server_user_id, coll_file.source_id, played_at=resolved_watched_at.get(coll_media_id))))
                    else:
                        tasks.append((label, emby_client.mark_unwatched(conn.url, conn.token, conn.server_user_id, coll_file.source_id)))

    if push_simkl and settings.simkl_client_id:
        from core import simkl as simkl_client
        simkl_media_res = await db.execute(select(Media).where(Media.id.in_(media_ids)))
        simkl_media_items = simkl_media_res.scalars().all()
        for media in simkl_media_items:
            if not media.tmdb_id or is_unmapped_tvdb_episode(media):
                continue
            if media.media_type == MediaType.movie:
                if watched:
                    watched_at = resolved_watched_at.get(media.id)
                    if watched_at is not None:
                        tasks.append((f"simkl add movie {media.tmdb_id}", simkl_client.add_movie_to_history(settings.simkl_client_id, settings.simkl_access_token, media.tmdb_id, watched_at)))
                else:
                    tasks.append((f"simkl remove movie {media.tmdb_id}", simkl_client.remove_movie_from_history(settings.simkl_client_id, settings.simkl_access_token, media.tmdb_id)))
            elif media.media_type == MediaType.episode and media.show_id and media.season_number is not None and media.episode_number is not None:
                show_res = await db.execute(select(Show).where(Show.id == media.show_id))
                show = show_res.scalar_one_or_none()
                if show and show.tmdb_id:
                    if watched:
                        watched_at = resolved_watched_at.get(media.id)
                        if watched_at is not None:
                            tasks.append((f"simkl add episode {show.tmdb_id} S{media.season_number}E{media.episode_number}", simkl_client.add_episode_to_history(settings.simkl_client_id, settings.simkl_access_token, show.tmdb_id, media.season_number, media.episode_number, watched_at)))
                    else:
                        tasks.append((f"simkl remove episode {show.tmdb_id} S{media.season_number}E{media.episode_number}", simkl_client.remove_episode_from_history(settings.simkl_client_id, settings.simkl_access_token, show.tmdb_id, media.season_number, media.episode_number)))

    trakt_token: str | None = None
    if push_trakt and settings.trakt_client_id:
        try:
            trakt_token = await trakt_auth.ensure_valid_trakt_token(db, settings)
        except trakt_auth.TraktTokenError as exc:
            failed = True
            logger.warning("Skipping Trakt history push for user %s: %s", user_id, exc)

    if trakt_token:
        media_res = await db.execute(
            select(Media).where(Media.id.in_(media_ids))
        )
        media_items = media_res.scalars().all()
        for media in media_items:
            if not media.tmdb_id or is_unmapped_tvdb_episode(media):
                continue
            if media.media_type == MediaType.movie:
                if watched:
                    tasks.append((f"trakt add movie {media.tmdb_id}", trakt_client.add_movie_to_history(settings.trakt_client_id, trakt_token, media.tmdb_id, resolved_watched_at.get(media.id))))
                else:
                    tasks.append((f"trakt remove movie {media.tmdb_id}", trakt_client.remove_movie_from_history(settings.trakt_client_id, trakt_token, media.tmdb_id)))
            elif media.media_type == MediaType.episode and media.show_id and media.season_number is not None and media.episode_number is not None:
                show_res = await db.execute(select(Show).where(Show.id == media.show_id))
                show = show_res.scalar_one_or_none()
                if show and show.tmdb_id:
                    if watched:
                        tasks.append((f"trakt add episode {show.tmdb_id} S{media.season_number}E{media.episode_number}", trakt_client.add_episode_to_history(settings.trakt_client_id, trakt_token, show.tmdb_id, media.season_number, media.episode_number, resolved_watched_at.get(media.id))))
                    else:
                        tasks.append((f"trakt remove episode {show.tmdb_id} S{media.season_number}E{media.episode_number}", trakt_client.remove_episode_from_history(settings.trakt_client_id, trakt_token, show.tmdb_id, media.season_number, media.episode_number)))

    if push_mdblist:
        from core import mdblist as mdblist_client

        mdblist_payload = mdblist_payloads.empty_payload()
        media_result = await db.execute(select(Media).where(Media.id.in_(media_ids)))
        media_list = media_result.scalars().all()
        mdblist_show_ids = {m.show_id for m in media_list if m.media_type == MediaType.episode and m.show_id}
        mdblist_shows_by_id: dict[int, Show] = {}
        if mdblist_show_ids:
            shows_result = await db.execute(select(Show).where(Show.id.in_(mdblist_show_ids)))
            mdblist_shows_by_id = {s.id: s for s in shows_result.scalars().all()}
        for media in media_list:
            if is_unmapped_tvdb_episode(media):
                continue
            show = mdblist_shows_by_id.get(media.show_id)
            item = (
                mdblist_payloads.payload_item(media, show=show, watched_at=resolved_watched_at.get(media.id, datetime.utcnow()))
                if watched
                else mdblist_payloads.payload_item(media, show=show)
            )
            if item:
                mdblist_payload[item[0]].append(item[1])
        mdblist_payload["shows"] = mdblist_payloads.merge_show_entries(mdblist_payload["shows"])
        # MDBList's /sync/watched/remove has no per-item removal feed — it bumps
        # a removal timestamp on /sync/last_activities and expects clients to
        # re-fetch the whole watched snapshot rather than confirming per item,
        # so removal on their end can lag visibly behind this call returning.
        operation = mdblist_client.push_watched if watched else mdblist_client.remove_watched
        tasks.append((f"mdblist {'push' if watched else 'remove'} watched", operation(settings.mdblist_api_key, mdblist_payload)))

    if tasks:
        results = await asyncio.gather(*(coro for _, coro in tasks), return_exceptions=True)
        for (label, _), result in zip(tasks, results):
            if isinstance(result, Exception) or result is False:
                failed = True
                # A warning with a plain reason, not a full traceback dump -
                # these are almost always an expired/revoked credential on
                # the remote service, not a bug here.
                logger.warning("Can't send history event to %s because %s", label, result)

    if require_success and failed:
        raise RuntimeError("Watch-state delivery is pending retry")
