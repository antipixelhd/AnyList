"""Application periodic jobs and their lifespan."""

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import (
    arr_settings,
    manual_sessions,
    mdblist_sync,
    netflix_sessions,
    outbound_sync,
    playback_sessions,
    server_sync,
    settings_store,
    show_metadata,
    simkl_sync,
    trakt_sync,
)
from db import engine
from models.base import CollectionSource
from models.events import WatchEvent
from models.playback_session import PlaybackSession
from models.sync import SyncJob, SyncStatus


async def _flush_pull_cycle(state) -> None:
    """Deliver one coalesced outbound delta after overlapping pulls settle."""
    from core.cloud_actions import dispatch_cloud_actions
    from core.stream_actions import dispatch_stream_actions
    from db import async_sessionmaker
    from models import Collection, UserSettings
    from core.sync_delivery_targets import connection_matches, dispatch_queues, queue_handoff

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db:
        if queue_handoff():
            from core.watch_intents import dispatch_watch_intents
            from core.streaming_library import dispatch_pending_library_deliveries
            await dispatch_watch_intents(db, state.user_id)
            await dispatch_pending_library_deliveries(db, state.user_id)
            await dispatch_stream_actions(db, state.user_id)
            await dispatch_cloud_actions(db, state.user_id)
            return
        errors = []
        async def deliver(*args, **kwargs):
            try:
                await outbound_sync.fan_out_changes(*args, **kwargs, require_success=True)
            except Exception as error:
                errors.append(error)
        # A retry must never replay a rating over a newer canonical edit.
        from models.ratings import Rating
        keys = set(state.new_ratings) | state.removed_ratings
        current_ratings = {}
        if keys:
            current_ratings = {(row.media_id, row.season_number): row.rating for row in
                (await db.execute(select(Rating).where(Rating.user_id == state.user_id,
                    Rating.media_id.in_({key[0] for key in keys}), Rating.episode_order.is_(None)))).scalars()}
        state.new_ratings = {key: score for key, score in state.new_ratings.items()
                             if current_ratings.get(key) == score}
        state.removed_ratings -= current_ratings.keys()
        changed_collection_ids = state.new_collected_ids | state.removed_collected_ids
        if changed_collection_ids:
            present = set((await db.execute(select(Collection.media_id).where(
                Collection.user_id == state.user_id,
                Collection.media_id.in_(changed_collection_ids),
            ))).scalars())
            state.new_collected_ids = changed_collection_ids & present
            state.removed_collected_ids = changed_collection_ids - present

        settings = (await db.execute(select(UserSettings).where(
            UserSettings.user_id == state.user_id
        ))).scalar_one_or_none()
        if state.library_new_ids or state.library_removed_ids:
            state.library_api_key = await settings_store.get_effective_tmdb_key(db, settings)
        changed_watch_ids = state.new_watched_ids
        final_watched_ids = set()
        if changed_watch_ids:
            final_watched_ids = set((await db.execute(select(WatchEvent.media_id).where(
                WatchEvent.user_id == state.user_id,
                WatchEvent.media_id.in_(changed_watch_ids),
                WatchEvent.completed.is_(True),
            ))).scalars())
        watched_groups: dict[tuple[frozenset[int], frozenset[CollectionSource]], set[int]] = {}
        for media_id in final_watched_ids:
            excluded_connections, excluded_cloud = state.watched_exclusions.get(media_id, (set(), set()))
            watched_groups.setdefault((frozenset(excluded_connections), frozenset(excluded_cloud)), set()).add(media_id)
        if watched_groups:
            from core.watch_intents import dispatch_watch_intents, queue_watch_intents
            for exclusions, media_ids in watched_groups.items():
                await queue_watch_intents(
                    db,
                    state.user_id,
                    media_ids,
                    exclude_connection_ids=set(exclusions[0]),
                )
            await db.commit()
            if dispatch_queues():
                await dispatch_watch_intents(db, state.user_id)
        for exclusions, media_ids in watched_groups.items():
            await deliver(
                db, state.user_id, None, media_ids, {}, settings,
                exclude_connection_ids=set(exclusions[0]),
                exclude_cloud_sources=set(exclusions[1]),
                durable_watch_media_ids=media_ids,
            )
        removed_watch_ids = set(state.removed_watch_exclusions)
        if removed_watch_ids:
            still_watched = set((await db.execute(select(WatchEvent.media_id).where(
                WatchEvent.user_id == state.user_id,
                WatchEvent.media_id.in_(removed_watch_ids),
                WatchEvent.completed.is_(True),
            ))).scalars())
            removal_groups: dict[frozenset[int], set[int]] = {}
            for media_id in removed_watch_ids - still_watched:
                excluded = frozenset(state.removed_watch_exclusions[media_id])
                removal_groups.setdefault(excluded, set()).add(media_id)
            if removal_groups:
                from core import watch_delivery
                from core.watch_intents import (
                    dispatch_watch_intents,
                    queue_watch_intents,
                )
                for excluded, media_ids in removal_groups.items():
                    await queue_watch_intents(db, state.user_id, media_ids,
                                              exclude_connection_ids=set(excluded))
                await db.commit()
                if dispatch_queues():
                    await dispatch_watch_intents(db, state.user_id)
                for excluded, media_ids in removal_groups.items():
                    await watch_delivery.push_watch_state(
                        db, state.user_id, sorted(media_ids), watched=False,
                        exclude_connection_ids=set(excluded),
                        skip_stream_watch_writes=True, require_success=True,
                    )
        if state.library_new_ids or state.library_removed_ids:
            await outbound_sync.fan_out_streaming_library(
                db,
                state.user_id,
                None,
                new_collected_ids=state.library_new_ids,
                removed_collected_ids=state.library_removed_ids,
                api_key=state.library_api_key,
                source_observed_at_by_media=state.library_observed_at_by_media,
                source_connection_ids_by_media=state.library_source_ids_by_media,
                desired_by_media=state.library_desired,
            )
        # Rating sources are scoped to a title/season, not the whole cycle.
        groups = {}
        for key, score in state.new_ratings.items():
            sources = state.rating_sources.get(key)
            connections = {int(value.split(":")[1]) for value in sources or () if value.startswith("connection:")}
            clouds = {CollectionSource(value) for value in sources or () if not value.startswith("connection:")}
            if sources is None:
                connections, clouds = state.excluded_connection_ids, state.excluded_cloud_sources
            groups.setdefault((frozenset(connections), frozenset(clouds)), {})[key] = score
        for (connections, clouds), ratings in groups.items():
            await deliver(db, state.user_id, None, set(), ratings, settings,
                exclude_connection_ids=set(connections), exclude_cloud_sources=set(clouds))
        await deliver(
            db, state.user_id, None, set(), {}, settings,
            removed_ratings=state.removed_ratings,
            new_collected_ids=state.new_collected_ids,
            removed_collected_ids=state.removed_collected_ids,
            exclude_connection_ids=state.excluded_connection_ids,
            exclude_cloud_sources=state.excluded_cloud_sources,
        )
        if dispatch_queues():
            await dispatch_stream_actions(db, state.user_id)
            await dispatch_cloud_actions(db, state.user_id)
        from models import MediaServerConnection
        from core.server_sync import _push_watched_back_to_source
        for connection_id, items in state.push_back.items():
            conn = await db.get(MediaServerConnection, connection_id)
            if not connection_matches(connection_id) or not conn or conn.user_id != state.user_id or conn.identity_version != state.connection_versions.get(connection_id):
                continue
            try:
                await _push_watched_back_to_source(db, state.user_id, conn, items, require_success=True)
            except Exception as error:
                errors.append(error)
        if errors:
            raise RuntimeError("Accepted sync delivery is pending retry") from errors[0]


async def _run_scheduled_pull_cycle(user_id: int, pulls: list[tuple[str, object]]) -> None:
    """Run all pulls selected for one scheduler tick behind one delivery barrier."""
    from core.pull_cycle import allow_cycle_delivery, coordinated_pull_cycle

    async with coordinated_pull_cycle(user_id) as state:
        results = await asyncio.gather(
            *(runner() for _, runner in pulls),
            return_exceptions=True,
        )
        for (label, _), result in zip(pulls, results):
            if isinstance(result, BaseException):
                print(f"Scheduled pull failed for user {user_id}, {label}: {type(result).__name__}")
        try:
            with allow_cycle_delivery(state):
                await _flush_pull_cycle(state)
        except Exception as error:
            # Local pull commits are durable. A later retry tick can deliver any
            # durable actions if this best-effort coalesced fan-out fails.
            print(f"Scheduled pull fan-out failed for user {user_id}: {type(error).__name__}")


async def _queue_scheduled_push(db, *, user_id, source, interval, runner, connection_id=None):
    """Keep full-push scheduling independent from the account pull cadence."""
    from datetime import datetime, timedelta

    active = (await db.execute(select(SyncJob.id).where(
        SyncJob.user_id == user_id, SyncJob.source == source,
        SyncJob.connection_id == connection_id,
        SyncJob.status.in_((SyncStatus.pending, SyncStatus.running)),
    ).limit(1))).scalar_one_or_none()
    if active is not None:
        return
    last = (await db.execute(select(SyncJob.updated_at).where(
        SyncJob.user_id == user_id, SyncJob.source == source,
        SyncJob.connection_id == connection_id, SyncJob.job_type == "push",
        SyncJob.status.in_((SyncStatus.completed, SyncStatus.failed)),
    ).order_by(SyncJob.updated_at.desc()).limit(1))).scalar_one_or_none()
    if last is not None and last + timedelta(hours=interval) > datetime.utcnow():
        return
    job = SyncJob(user_id=user_id, source=source, connection_id=connection_id,
                  job_type="push", status=SyncStatus.pending)
    db.add(job)
    await db.flush()
    await db.commit()
    if connection_id is None:
        asyncio.create_task(runner(user_id, job.id))
    else:
        asyncio.create_task(runner(user_id, connection_id, job.id))


async def _schedule_sync_tick(db):
    from fastapi import HTTPException
    from core.account_sync import queue_account_pull, run_account_pull
    from core.cloud_reconciliation import cloud_push_is_approved
    from core.tracking_snapshot import require_stream_reconciliation
    from models.connections import MediaServerConnection
    from models.users import UserSettings

    connections = list((await db.execute(select(MediaServerConnection).where(
        MediaServerConnection.auto_push_interval.isnot(None),
    ))).scalars())
    for conn in connections:
        interval = conn.scheduled_auto_push_interval
        if interval is None or not conn.push_enabled or conn.type not in {"jellyfin", "emby", "plex"}:
            continue
        try:
            await require_stream_reconciliation(db, conn)
        except HTTPException:
            continue
        await _queue_scheduled_push(db, user_id=conn.user_id, source=CollectionSource(conn.type),
                                    interval=interval, runner=server_sync._run_full_push, connection_id=conn.id)

    cloud_pushes = (
        ("trakt", "access_token", trakt_sync._run_trakt_push,
         ("push_watched", "push_ratings", "push_collection", "push_dropped")),
        ("simkl", "access_token", simkl_sync._run_simkl_push, ("push_watched", "push_ratings")),
        ("mdblist", "api_key", mdblist_sync.run_mdblist_push,
         ("push_watched", "push_ratings", "push_watchlist", "push_collection", "push_dropped")),
    )
    settings_rows = list((await db.execute(select(UserSettings))).scalars())
    # Capture IDs before rollback/commit expires any ORM objects.
    account_ids = [row.user_id for row in settings_rows if row.pull_sync_interval is not None]
    for row in settings_rows:
        for provider, credential, runner, flags in cloud_pushes:
            interval = getattr(row, f"{provider}_auto_push_interval")
            if interval is None or not getattr(row, f"{provider}_{credential}"):
                continue
            if not any(getattr(row, f"{provider}_{flag}") for flag in flags):
                continue
            if not await cloud_push_is_approved(db, row.user_id, provider):
                continue
            await _queue_scheduled_push(db, user_id=row.user_id, source=CollectionSource(provider),
                                        interval=interval, runner=runner)
    for user_id in account_ids:
        try:
            cycle = await queue_account_pull(db, user_id, scheduled=True)
            await db.commit()  # Release admission lock when not due.
            if cycle:
                asyncio.create_task(run_account_pull(user_id, cycle.id))
        except HTTPException:
            await db.rollback()


async def _auto_sync_scheduler():
    from db import async_sessionmaker

    while True:
        await asyncio.sleep(300)  # Preserve the existing five-minute tick.
        try:
            await netflix_sessions.cleanup_expired_netflix_imports()
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with factory() as db:
                await _schedule_sync_tick(db)
        except Exception as error:
            print(f"Auto-sync scheduler error: {type(error).__name__}")


async def _manual_session_completer():
    from db import async_sessionmaker


    while True:
        await asyncio.sleep(60)
        try:
            async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with async_session() as db:
                await manual_sessions.auto_complete_manual_sessions(db)
        except Exception as e:
            print(f"Manual session completer error: {e}")


async def _dispatch_pending_stream_actions_once(session_factory=None):
    """Retry durable streaming writes independently of provider pull schedules."""
    from core.cloud_actions import dispatch_cloud_actions
    from core.stream_actions import dispatch_stream_actions
    from core.streaming_library import dispatch_pending_library_deliveries
    from core.watch_intents import dispatch_watch_intents
    from db import async_sessionmaker
    from models.streaming_library import (
        StreamingLibraryDelivery,
        StreamingLibraryIntent,
    )
    from models.tracking import CloudAction, StreamAction
    from models.watch_intent import WatchIntent

    factory = session_factory or async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    from core.sync_delivery import retry_cycle_deliveries
    await retry_cycle_deliveries(factory)
    async with factory() as db:
        pending_users = select(StreamAction.user_id).where(StreamAction.state == "pending").union(
            select(CloudAction.user_id).where(CloudAction.state == "pending"),
            select(WatchIntent.user_id).where(WatchIntent.state == "pending"),
            select(StreamingLibraryIntent.user_id)
                .join(StreamingLibraryDelivery, StreamingLibraryDelivery.intent_id == StreamingLibraryIntent.id)
                .where(StreamingLibraryDelivery.state == "pending"),
        ).subquery()
        result = await db.execute(select(pending_users.c.user_id).order_by(pending_users.c.user_id))
        user_ids = list(result.scalars().all())

    # Use one transaction per user. dispatch_stream_actions locks the user and
    # each pending row, so multiple app processes remain idempotent.
    for user_id in user_ids:
        try:
            async with factory() as db:
                stream_ok = True
                try:
                    await dispatch_stream_actions(db, user_id)
                except Exception as error:
                    stream_ok = False
                    print(f"Stream action retry failed for user {user_id}: {type(error).__name__}")
                await dispatch_watch_intents(db, user_id)
                try:
                    await dispatch_pending_library_deliveries(db, user_id)
                except Exception as error:
                    # A collection destination failure must not block unrelated
                    # watched-state or cloud deletion retries for this user.
                    print(f"Library delivery retry failed for user {user_id}: {type(error).__name__}")
                if stream_ok:
                    await dispatch_cloud_actions(db, user_id)
        except Exception as error:
            # One unavailable connection must not prevent another user's writes
            # from being retried on this tick. Remote error bodies are not logged.
            print(
                f"Stream action retry failed for user {user_id}: "
                f"{type(error).__name__}"
            )


async def _stream_action_retry_scheduler():
    while True:
        try:
            await _dispatch_pending_stream_actions_once()
            await netflix_sessions.retry_committed_import_pulls()
            from core.account_sync import retry_automatic_pulls
            await retry_automatic_pulls()
        except Exception as error:
            print(f"Stream action retry scheduler error: {type(error).__name__}")
        await asyncio.sleep(60)


async def _rating_push_scheduler():
    """Deliver queued completion-rating prompts while the PWA is closed."""
    from core.web_push import dispatch_rating_pushes
    from db import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    while True:
        await asyncio.sleep(30)
        try:
            async with factory() as db:
                await dispatch_rating_pushes(db)
        except Exception as error:
            print(f"Rating push scheduler error: {type(error).__name__}")


async def _emby_progress_poller():
    """Emby's webhook system has no progress event (only start/pause/unpause/
    stop), so a PlaybackSession opened by an Emby webhook freezes at the last
    event's position until the next one (#240). Jellyfin doesn't need this:
    its Webhook plugin can send PlaybackProgress, which /webhook/jellyfin
    already handles.

    Polls the Emby Sessions API for connections with playback sync enabled and
    refreshes the progress/state of sessions the webhooks already opened.
    Sessions without a matching PlaybackSession row are ignored, so the
    webhook flow stays the source of truth."""
    import logging
    log = logging.getLogger("uvicorn.error")

    try:
        from datetime import datetime

        import httpx

        from db import AsyncSessionLocal
        from models.connections import MediaServerConnection
    except Exception as e:
        log.error(f"Emby progress poller: failed to import dependencies: {e}")
        return

    POLL_INTERVAL = 60
    log.info("Emby progress poller: started")

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            async with AsyncSessionLocal() as db:
                conns = (await db.execute(
                    select(MediaServerConnection).where(
                        MediaServerConnection.type == "emby",
                        MediaServerConnection.sync_playback.is_(True),
                    )
                )).scalars().all()
                for conn in conns:
                    try:
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            resp = await client.get(
                                f"{conn.url.rstrip('/')}/Sessions",
                                headers={"X-Emby-Token": conn.token},
                            )
                            resp.raise_for_status()
                            sessions = resp.json()
                    except Exception:
                        continue
                    for s in sessions:
                        item = s.get("NowPlayingItem") or {}
                        play_state = s.get("PlayState") or {}
                        runtime = item.get("RunTimeTicks") or 0
                        position = play_state.get("PositionTicks") or 0
                        if not runtime or not position:
                            continue
                        key = f"emby:{conn.user_id}:{s.get('Id')}"
                        row = (await db.execute(
                            select(PlaybackSession).where(PlaybackSession.session_key == key)
                        )).scalar_one_or_none()
                        if not row:
                            continue
                        row.progress_percent = round(position / runtime, 4)
                        row.progress_seconds = int(position / 10_000_000)
                        row.state = "paused" if play_state.get("IsPaused") else "playing"
                        row.updated_at = datetime.utcnow()
                        # Commit per-row via the shared helper (routers/webhooks.py):
                        # a PlaybackStop webhook can delete this same session between
                        # the select above and this write, which SQLAlchemy surfaces
                        # as a StaleDataError. Batching every session into one final
                        # commit would let that one race discard every other Emby
                        # session's progress for this whole poll cycle - tolerating
                        # it per-row keeps the blast radius to just that session.
                        await playback_sessions._commit_playback_session_update(db)
        except Exception as e:
            log.error(f"Emby progress poller: {e}")


async def _show_metadata_refresher():
    """Keeps every TMDB-backed show's stored metadata (status plus the
    tmdb_data snapshot: seasons, last/next_episode_to_air, refreshed_at) at
    most ~a day old, so Next Up's missing-episode fallback
    (routers/history.py's _next_up_needs_live_fetch) can answer "did a new
    episode appear?" from the database alone instead of fan-out fetching
    TMDB on every cold home-page load (#294, #307, #332).

    This sweep is also what catches revivals: an unexpected renewal (e.g.
    Futurama) flips a locally Ended/Canceled status back within a day. It
    deliberately re-checks each show directly rather than using a
    delta/changes feed - if this process happened to be down during the
    window a changes feed would have caught a revival, that revival is gone
    for good. A direct per-show check has no such window: whether
    yesterday's sweep ran or not, today's checks every stale show from
    scratch.

    Runs shortly after startup (so fresh installs and restarts get their
    snapshots gated quickly), then daily. Shows refreshed less than
    STALE_AFTER ago are skipped, so a restart doesn't re-fetch what
    yesterday's sweep already covered. TVDB-sourced snapshots
    (tmdb_data.source == "tvdb") are never touched - their season layout is
    TVDB-shaped (#335) and their shows have no TMDB identity to fetch.
    """
    import logging
    from datetime import datetime, timedelta, timezone
    log = logging.getLogger("uvicorn.error")

    try:
        from core import tmdb as tmdb_client
        from core.season_releases import refresh_season_release_notifications
        from core.tracking_metadata import (
            refresh_tracked_catalogues,
            refresh_tracked_tvdb_show_summaries,
        )
        from db import AsyncSessionLocal
        from models.global_settings import GlobalSettings
        from models.show import Show
        from models.users import User, UserSettings
    except Exception as e:
        log.error(f"Show metadata refresher: failed to import dependencies: {e}")
        return

    FINAL_STATUSES = ("Ended", "Canceled")
    STARTUP_DELAY = 2 * 60
    SWEEP_INTERVAL = 24 * 60 * 60
    # Skip shows whose snapshot is younger than this - comfortably less than
    # SWEEP_INTERVAL so clock drift can't make the sweep skip everything, but
    # large enough that a restart right after a sweep re-fetches ~nothing.
    STALE_AFTER = timedelta(hours=20)
    FETCH_CONCURRENCY = 10
    log.info("Show metadata refresher: started")

    def _snapshot_is_fresh(show) -> bool:
        refreshed_at = (show.tmdb_data or {}).get("refreshed_at")
        if not refreshed_at:
            return False
        try:
            refreshed = datetime.fromisoformat(refreshed_at)
        except (TypeError, ValueError):
            return False
        if refreshed.tzinfo is None:
            refreshed = refreshed.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - refreshed < STALE_AFTER

    delay = STARTUP_DELAY
    while True:
        await asyncio.sleep(delay)
        delay = SWEEP_INTERVAL
        try:
            async with AsyncSessionLocal() as db:
                # Show is a shared, instance-wide table with no single
                # "current user" for this sweep to scope to, but a TMDB key
                # isn't tied to whichever account configured it - any valid
                # one fetches the same public show metadata. Global/environment -> an
                # admin's own key -> any user's, so installs that skip the
                # global key still get the sweep instead of it silently
                # never running, while preferring an admin's key over a
                # random member's when both exist.
                gs = (await db.execute(
                    select(GlobalSettings).where(GlobalSettings.id == 1)
                )).scalar_one_or_none()
                api_key = settings_store.get_server_tmdb_key(gs)
                tvdb_key, tvdb_pin = settings_store.get_server_tvdb_credentials(gs)
                if not settings_store.check_tmdb_key(api_key):
                    api_key = (await db.execute(
                        select(UserSettings.tmdb_api_key)
                        .join(User, User.id == UserSettings.user_id)
                        .where(UserSettings.tmdb_api_key.isnot(None), User.is_admin.is_(True))
                        .limit(1)
                    )).scalar_one_or_none()
                if not settings_store.check_tmdb_key(api_key):
                    api_key = (await db.execute(
                        select(UserSettings.tmdb_api_key)
                        .where(UserSettings.tmdb_api_key.isnot(None))
                        .limit(1)
                    )).scalar_one_or_none()
                if not tvdb_key:
                    tvdb_row = (await db.execute(
                        select(UserSettings.tvdb_api_key, UserSettings.tvdb_subscriber_pin)
                        .join(User, User.id == UserSettings.user_id)
                        .where(UserSettings.tvdb_api_key.isnot(None), User.is_admin.is_(True))
                        .limit(1)
                    )).first()
                    if tvdb_row:
                        tvdb_key, tvdb_pin = tvdb_row
                if not tvdb_key:
                    tvdb_row = (await db.execute(
                        select(UserSettings.tvdb_api_key, UserSettings.tvdb_subscriber_pin)
                        .where(UserSettings.tvdb_api_key.isnot(None))
                        .limit(1)
                    )).first()
                    if tvdb_row:
                        tvdb_key, tvdb_pin = tvdb_row
                if tvdb_key:
                    from core import tvdb as tvdb_client
                    tvdb_client.set_subscriber_pin(tvdb_key, tvdb_pin)
                if not settings_store.check_tmdb_key(api_key) and not tvdb_key:
                    log.info("Show metadata refresher: no TMDB or TVDB key configured anywhere, skipping")
                    # Cached confirmed dates can still reach their premiere
                    # while the metadata provider is temporarily unavailable.
                    await refresh_season_release_notifications(db)
                    continue

                all_shows = (
                    (await db.execute(select(Show).where(Show.tmdb_id.isnot(None)))).scalars().all()
                    if settings_store.check_tmdb_key(api_key) else []
                )
                shows = [
                    s for s in all_shows
                    if (s.tmdb_data or {}).get("source") != "tvdb" and not _snapshot_is_fresh(s)
                ]
                sem = asyncio.Semaphore(FETCH_CONCURRENCY)
                refreshed = 0
                revived = 0

                async def _check(show):
                    nonlocal refreshed, revived
                    was_final = show.status in FINAL_STATUSES
                    async with sem:
                        try:
                            # cache_ttl=None: a stale cached response would
                            # defeat the point of this sweep.
                            data = await tmdb_client.get_show(show.tmdb_id, api_key=api_key, cache_ttl=None)
                        except Exception:
                            return
                    show_metadata.apply_show_metadata(show, data)
                    refreshed += 1
                    if was_final and data.get("status") not in FINAL_STATUSES:
                        revived += 1

                if shows:
                    await asyncio.gather(*(_check(s) for s in shows))
                await db.commit()
                log.info(
                    f"Show metadata refresher: refreshed {refreshed}/{len(shows)} stale shows, "
                    f"{revived} revived"
                )
                catalogue = await refresh_tracked_catalogues(db, api_key, tvdb_api_key=tvdb_key)
                log.info(
                    "Tracking catalogue refresher: refreshed "
                    f"{catalogue['refreshed']} series / {catalogue['episodes']} episodes, "
                    f"{catalogue['skipped']} skipped, {catalogue['failed']} failed"
                )
                tvdb_summaries = await refresh_tracked_tvdb_show_summaries(db, tvdb_key)
                log.info(
                    "TVDB season summary refresher: refreshed "
                    f"{tvdb_summaries['refreshed']}, {tvdb_summaries['skipped']} skipped, "
                    f"{tvdb_summaries['failed']} failed"
                )
                season_events = await refresh_season_release_notifications(db)
                log.info(
                    "Season release notifications: "
                    f"{season_events['date_notices']} new dates, {season_events['release_notices']} releases"
                )
        except Exception as e:
            log.error(f"Show metadata refresher: {e}")


async def _watchlist_poller():
    import logging
    log = logging.getLogger("uvicorn.error")

    try:
        from core import plex as plex_client
        from core import radarr as radarr_client
        from core import sonarr as sonarr_client
        from db import async_sessionmaker
        from models.connections import MediaServerConnection
        from models.global_settings import GlobalSettings
        from models.users import UserSettings
    except Exception as e:
        log.error(f"Watchlist poller: failed to import dependencies: {e}")
        return

    CHECK_INTERVAL = 300
    log.info("Watchlist poller: started")

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with async_session() as db:
                result = await db.execute(
                    select(MediaServerConnection).where(
                        MediaServerConnection.type == "plex",
                        or_(
                            MediaServerConnection.watchlist_to_radarr.is_(True),
                            MediaServerConnection.watchlist_to_sonarr.is_(True),
                        ),
                    )
                )
                connections = result.scalars().all()

                for conn in connections:
                    try:
                        settings_q = await db.execute(
                            select(UserSettings).where(UserSettings.user_id == conn.user_id)
                        )
                        user_settings = settings_q.scalar_one_or_none()
                        gs_q = await db.execute(select(GlobalSettings).where(GlobalSettings.id == 1))
                        global_settings = gs_q.scalar_one_or_none()

                        radarr_cfg = arr_settings._effective_radarr(user_settings, global_settings) if conn.watchlist_to_radarr else None
                        sonarr_cfg = arr_settings._effective_sonarr(user_settings, global_settings) if conn.watchlist_to_sonarr else None

                        if not radarr_cfg and not sonarr_cfg:
                            log.info(f"Watchlist poller: connection {conn.id} — Radarr/Sonarr not configured, skipping")
                            continue

                        synced: set = set(conn.watchlist_synced_ids or [])
                        newly_synced: set = set()

                        async def _send_to_arr(item_type: str, guids, title: str, cache_key: str):
                            """Send one item to Radarr or Sonarr and mark it synced."""
                            tmdb_id = plex_client.extract_tmdb_id(guids)
                            if not tmdb_id:
                                return
                            if cache_key in synced or cache_key in newly_synced:
                                return
                            if item_type == "movie" and radarr_cfg:
                                try:
                                    await radarr_client.add_movie(
                                        url=radarr_cfg.radarr_url,
                                        token=radarr_cfg.radarr_token,
                                        tmdb_id=tmdb_id,
                                        title=title,
                                        root_folder=radarr_cfg.radarr_root_folder,
                                        quality_profile_id=radarr_cfg.radarr_quality_profile,
                                        tags=radarr_cfg.radarr_tags,
                                    )
                                    newly_synced.add(cache_key)
                                    log.info(f"Watchlist: queued movie tmdb:{tmdb_id} in Radarr for user {conn.user_id}")
                                except Exception as e:
                                    log.error(f"Watchlist: Radarr error for tmdb:{tmdb_id}: {e}")
                            elif item_type == "show" and sonarr_cfg:
                                tvdb_id = plex_client.extract_tvdb_id(guids)
                                if not tvdb_id:
                                    return
                                try:
                                    await sonarr_client.add_series(
                                        url=sonarr_cfg.sonarr_url,
                                        token=sonarr_cfg.sonarr_token,
                                        tvdb_id=int(tvdb_id),
                                        root_folder=sonarr_cfg.sonarr_root_folder,
                                        quality_profile_id=sonarr_cfg.sonarr_quality_profile,
                                        tags=sonarr_cfg.sonarr_tags,
                                        season_folder=sonarr_cfg.sonarr_season_folder if sonarr_cfg.sonarr_season_folder is not None else True,
                                    )
                                    newly_synced.add(cache_key)
                                    log.info(f"Watchlist: queued show tvdb:{tvdb_id} in Sonarr for user {conn.user_id}")
                                except Exception as e:
                                    log.error(f"Watchlist: Sonarr error for tvdb:{tvdb_id}: {e}")

                        # Admin's own watchlist via REST (returns GUIDs directly)
                        own_watchlist = await plex_client.get_watchlist(conn.plex_account_token)
                        for item in own_watchlist:
                            item_type = item.get("type")
                            guids = plex_client.get_guids(item)
                            tmdb_id = plex_client.extract_tmdb_id(guids)
                            if not tmdb_id:
                                continue
                            cache_key = f"{item_type}:{tmdb_id}"
                            await _send_to_arr(item_type, guids, item.get("title", ""), cache_key)

                        # Friends' watchlists via GraphQL (requires per-item enrichment for GUIDs)
                        if conn.watchlist_all_users:
                            all_friends = await plex_client.get_all_friends(conn.plex_account_token)
                            monitored = set(conn.watchlist_monitored_users or [])
                            friends = [f for f in all_friends if f["watchlist_id"] in monitored] if monitored else []
                            for friend in friends:
                                friend_items = await plex_client.get_friend_watchlist(conn.plex_account_token, friend["watchlist_id"])
                                for fi in friend_items:
                                    plex_id = fi.get("id")
                                    if not plex_id:
                                        continue
                                    cache_key = f"plex:{plex_id}"
                                    if cache_key in synced or cache_key in newly_synced:
                                        continue
                                    enriched = await plex_client.enrich_plex_item(conn.plex_account_token, plex_id)
                                    if not enriched:
                                        continue
                                    item_type = fi.get("type", "").lower()
                                    guids = plex_client.get_guids(enriched)
                                    await _send_to_arr(item_type, guids, fi.get("title", ""), cache_key)

                        if newly_synced:
                            conn.watchlist_synced_ids = list(synced | newly_synced)
                            await db.commit()

                    except Exception as e:
                        log.error(f"Watchlist poller: error on connection {conn.id}: {e}", exc_info=True)

        except Exception as e:
            log.error(f"Watchlist poller error: {e}", exc_info=True)


@asynccontextmanager
async def background_jobs():
    """Run periodic jobs for the application lifespan and cancel them on exit."""
    from core.statistics_snapshots import scheduler as statistics_scheduler

    jobs = (
        statistics_scheduler,
        _auto_sync_scheduler,
        _stream_action_retry_scheduler,
        _rating_push_scheduler,
        _watchlist_poller,
        _manual_session_completer,
        _emby_progress_poller,
        _show_metadata_refresher,
    )
    tasks = [asyncio.create_task(job(), name=job.__name__) for job in jobs]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                raise result
