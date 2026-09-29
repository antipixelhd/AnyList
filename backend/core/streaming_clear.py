"""Durable, connection-scoped clear operations for Stremio and Nuvio."""

from __future__ import annotations

import copy
import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm.attributes import set_committed_value

from db import AsyncSessionLocal
from models.collection import Collection, CollectionFile
from models.connections import MediaServerConnection
from models.base import CollectionSource
from models.sync import SyncJob, SyncStatus
from models.tracking import StreamAction, StreamBaseline
from models.users import User
from models.watch_intent import WatchIntent

logger = logging.getLogger("uvicorn.error")


def target_identity(conn: MediaServerConnection) -> dict:
    if conn.type == "nuvio":
        try:
            profile_id = int(conn.server_user_id or "")
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid Nuvio profile index") from exc
        if not 1 <= profile_id <= 6:
            raise ValueError("Invalid Nuvio profile index")
        return {
            "url_sha256": hashlib.sha256(conn.url.encode("utf-8")).hexdigest(),
            "profile_id": profile_id,
            "token_sha256": hashlib.sha256(conn.token.encode("utf-8")).hexdigest(),
        }
    if conn.type == "stremio":
        return {"token_sha256": hashlib.sha256(conn.token.encode("utf-8")).hexdigest()}
    raise ValueError("Clear is supported only for Stremio and Nuvio connections")


def selected_scope(conn: MediaServerConnection) -> dict[str, bool]:
    return {
        "collection": bool(conn.push_collection),
        "watched": bool(conn.push_watched),
        "playback": bool(conn.push_playback),
    }


async def pull_blocked_by_clear(db, conn: MediaServerConnection) -> bool:
    baseline = await db.get(StreamBaseline, conn.id)
    snapshot = baseline.snapshot if baseline and isinstance(baseline.snapshot, dict) else {}
    return bool(snapshot.get("clear_guard"))


def _naive_utc(value: datetime | str | None) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


async def _fresh_baseline(db, connection_id: int) -> StreamBaseline | None:
    return (await db.execute(
        select(StreamBaseline)
        .where(StreamBaseline.connection_id == connection_id)
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()


async def assert_pull_snapshot_current(db, user_id: int, conn: MediaServerConnection,
                                       started_at: datetime, *, full_resync: bool = False) -> None:
    """Lock reconciliation against a clear and reject snapshots fetched before it."""
    (await db.execute(select(User.id).where(User.id == user_id).with_for_update())).scalar_one()
    active_clear = (await db.execute(select(SyncJob.id).where(
        SyncJob.connection_id == conn.id,
        SyncJob.job_type == "clear",
        SyncJob.status.in_((SyncStatus.pending, SyncStatus.running)),
    ).limit(1))).scalar_one_or_none()
    if active_clear is not None:
        raise RuntimeError("A provider clear is in progress; retry this sync after it completes")
    baseline = await _fresh_baseline(db, conn.id)
    snapshot = baseline.snapshot if baseline and isinstance(baseline.snapshot, dict) else {}
    guard = snapshot.get("clear_guard")
    if guard:
        clear_started_at = _naive_utc(guard.get("started_at")) if isinstance(guard, dict) else None
        if not full_resync or (clear_started_at is not None and started_at < clear_started_at):
            raise RuntimeError("A provider clear is incomplete or this snapshot predates it; retry Clear data or run a new full resync")
    if (
        baseline is not None
        and baseline.observed_at is not None
        and started_at is not None
        and baseline.observed_at > started_at
    ):
        raise RuntimeError("This provider snapshot became stale during sync; retry after the concurrent operation")


async def _set_job_failed(db, job_id: int, message: str) -> None:
    await db.execute(
        update(SyncJob).where(SyncJob.id == job_id).values(
            status=SyncStatus.failed,
            error_message=str(message)[:900],
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    await db.commit()


def _project_cleared_snapshot(snapshot: dict, scope: dict[str, bool]) -> dict:
    projected = copy.deepcopy(snapshot if isinstance(snapshot, dict) else {})
    records = copy.deepcopy(projected.get("records") or {})
    if scope.get("collection"):
        projected["library"] = []
        records["library"] = []
    if scope.get("watched"):
        projected["watched"] = []
        records["watched"] = []
    if scope.get("playback"):
        projected["progress"] = {}
        projected["resume"] = {}
        projected["outbound"] = {}
        projected["progress_completed"] = []
        records["progress"] = []
    if records or "records" in projected:
        projected["records"] = records
    projected.pop("clear_guard", None)
    return projected


async def _freeze_outbound_work(db, user_id: int, connection_id: int, scope: dict[str, bool]) -> None:
    if scope.get("collection"):
        from core.streaming_library import clear_pending_library_deliveries
        await clear_pending_library_deliveries(db, user_id, connection_id)
    if scope.get("watched"):
        intents = (await db.execute(select(WatchIntent).where(
            WatchIntent.user_id == user_id,
            WatchIntent.connection_id == connection_id,
            WatchIntent.state == "pending",
        ).with_for_update())).scalars().all()
        for intent in intents:
            intent.state = "cleared"
    if scope.get("watched") or scope.get("playback"):
        actions = (await db.execute(select(StreamAction).where(
            StreamAction.user_id == user_id,
            StreamAction.connection_id == connection_id,
            StreamAction.state == "pending",
            StreamAction.action.in_(("dismiss", "restore", "reset", "upsert")),
        ).with_for_update())).scalars().all()
        for action in actions:
            if scope.get("playback") or action.action == "reset":
                action.state = "cleared"


async def _set_guard(db, user_id: int, conn: MediaServerConnection, job_id: int,
                     scope: dict[str, bool], identity: dict) -> tuple[dict, bool]:
    baseline = await _fresh_baseline(db, conn.id)
    previous_snapshot = copy.deepcopy(baseline.snapshot or {}) if baseline else {}
    previous_approved = bool(baseline.approved) if baseline else False
    if baseline is None:
        baseline = StreamBaseline(connection_id=conn.id, user_id=user_id, snapshot={}, approved=False)
        db.add(baseline)
    snapshot = copy.deepcopy(previous_snapshot)
    snapshot["clear_guard"] = {
        "job_id": job_id,
        "scope": scope,
        "identity": identity,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if scope.get("watched"):
        snapshot.pop("playback_clear_watch_visibility_suppressed", None)
    elif scope.get("playback"):
        pending_watch_media_ids = set((await db.execute(select(WatchIntent.media_id).where(
            WatchIntent.user_id == user_id,
            WatchIntent.connection_id == conn.id,
            WatchIntent.state == "pending",
        ).with_for_update())).scalars().all())
        previous_suppressed = {
            int(media_id)
            for media_id in snapshot.get("playback_clear_watch_visibility_suppressed", [])
            if str(media_id).isdigit()
        }
        snapshot["playback_clear_watch_visibility_suppressed"] = sorted(
            previous_suppressed | pending_watch_media_ids,
        )
    baseline.snapshot = snapshot
    baseline.approved = False
    if scope.get("collection"):
        # Keep local AnyList truth if a later full resync observes that the
        # cleared provider library is empty, including after a partial failure.
        await _anchor_local_memberships(
            db, user_id, await _connection_collection_media_ids(db, user_id, conn),
        )
    await _freeze_outbound_work(db, user_id, conn.id, scope)
    await db.commit()
    return previous_snapshot, previous_approved


async def _connection_collection_media_ids(db, user_id: int, conn: MediaServerConnection) -> list[int]:
    source = CollectionSource.nuvio if conn.type == "nuvio" else CollectionSource.stremio
    return list((await db.execute(
        select(Collection.media_id)
        .join(CollectionFile, CollectionFile.collection_id == Collection.id)
        .where(
            Collection.user_id == user_id,
            CollectionFile.connection_id == conn.id,
            CollectionFile.source == source,
        )
        .distinct()
    )).scalars().all())


async def _anchor_local_memberships(db, user_id: int, media_ids: list[int]) -> None:
    if not media_ids:
        return
    from core.streaming_library import _set_manual_anchor
    for media_id in media_ids:
        await _set_manual_anchor(db, user_id, media_id, True)


async def _restore_guard_before_mutation(db, conn: MediaServerConnection, job_id: int,
                                        snapshot: dict, approved: bool) -> None:
    baseline = await _fresh_baseline(db, conn.id)
    marker = (baseline.snapshot or {}).get("clear_guard") if baseline else None
    if baseline and isinstance(marker, dict) and marker.get("job_id") == job_id:
        baseline.snapshot = snapshot
        baseline.approved = approved
        await db.commit()


async def _run_clear_data_job(user_id: int, connection_id: int, job_id: int) -> None:
    """Run a clear from its frozen job scope; partial failure leaves a pull gate."""
    async with AsyncSessionLocal() as db:
        job = await db.get(SyncJob, job_id)
        if not job or job.status == SyncStatus.cancelled:
            return
        if job.status != SyncStatus.pending:
            return
        started = await db.execute(
            update(SyncJob).where(SyncJob.id == job_id, SyncJob.status == SyncStatus.pending)
            .values(status=SyncStatus.running, current_step="Preparing remote clear", processed_items=0,
                    total_items=0, updated_at=datetime.now(timezone.utc).replace(tzinfo=None))
            .returning(SyncJob.id)
        )
        if started.scalar_one_or_none() is None:
            await db.commit()
            return
        await db.commit()

        stats = copy.deepcopy(job.stats or {})
        scope = stats.get("clear_scope") or {}
        identity = stats.get("target_identity") or {}
        if set(scope) != {"collection", "watched", "playback"} or not any(scope.values()):
            await _set_job_failed(db, job_id, "Clear job has no valid frozen scope")
            return

        conn = (await db.execute(select(MediaServerConnection).where(
            MediaServerConnection.id == connection_id,
            MediaServerConnection.user_id == user_id,
        ))).scalar_one_or_none()
        if not conn:
            await _set_job_failed(db, job_id, "Connection no longer exists")
            return
        try:
            actual_identity = target_identity(conn)
        except Exception as exc:
            await _set_job_failed(db, job_id, f"Invalid clear target identity: {exc}")
            return
        if actual_identity != identity:
            await _set_job_failed(db, job_id, "Connection profile or credentials changed before clear started; retry after reviewing the target")
            return

        try:
            # The persisted guard closes the gap between clear scheduling and
            # network completion: ordinary pulls cannot infer removals from a
            # partially-cleared remote account, including after process restart.
            await db.execute(select(User.id).where(User.id == user_id).with_for_update())
            previous_snapshot, previous_approved = await _set_guard(
                db, user_id, conn, job_id, scope, identity,
            )

            # Reacquire the user lock after committing the crash-safe guard.
            # Local dispatchers use this same lock before provider locks.
            await db.execute(select(User.id).where(User.id == user_id).with_for_update())
            await db.refresh(conn)
            if target_identity(conn) != identity:
                await _restore_guard_before_mutation(db, conn, job_id, previous_snapshot, previous_approved)
                raise RuntimeError("Connection profile or credentials changed before clear started; retry after reviewing the target")

            if conn.type == "nuvio":
                from core import nuvio
                lock = nuvio.connection_lock(conn.id)
            else:
                from core import stremio
                lock = stremio.connection_lock(conn.id)
            async with lock:
                await db.refresh(conn)
                if target_identity(conn) != identity:
                    await _restore_guard_before_mutation(db, conn, job_id, previous_snapshot, previous_approved)
                    raise RuntimeError("Connection profile or credentials changed before clear started; retry after reviewing the target")

                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(
                    current_step="Clearing selected provider data",
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                ))

                if conn.type == "nuvio":
                    from core import nuvio

                    async def persist_refresh(session) -> None:
                        # The clear transaction owns the User lock. Persist the
                        # one-time refresh token in an independent transaction.
                        identity["token_sha256"] = hashlib.sha256(
                            session.refresh_token.encode("utf-8")
                        ).hexdigest()
                        async with AsyncSessionLocal() as token_db:
                            result = await token_db.execute(update(MediaServerConnection).where(
                                MediaServerConnection.id == conn.id,
                                MediaServerConnection.user_id == user_id,
                                MediaServerConnection.url == conn.url,
                                MediaServerConnection.server_user_id == conn.server_user_id,
                                MediaServerConnection.token == conn.token,
                            ).values(token=session.refresh_token))
                            if result.rowcount != 1:
                                raise RuntimeError("Nuvio connection profile changed during clear")
                            baseline_row = await _fresh_baseline(token_db, conn.id)
                            if baseline_row is not None:
                                updated_snapshot = copy.deepcopy(baseline_row.snapshot or {})
                                guard = updated_snapshot.get("clear_guard")
                                if isinstance(guard, dict) and guard.get("job_id") == job_id:
                                    guard["identity"] = dict(identity)
                                    baseline_row.snapshot = updated_snapshot
                            await token_db.commit()
                        set_committed_value(conn, "token", session.refresh_token)

                    counts = await nuvio.clear_sync_data(
                        conn.url, conn.token, identity["profile_id"],
                        collection=scope["collection"], watched=scope["watched"],
                        playback=scope["playback"], on_refresh=persist_refresh,
                    )
                else:
                    from core import stremio

                    counts = await stremio.clear_datastore_data(
                        conn.token, collection=scope["collection"],
                        watched=scope["watched"], playback=scope["playback"],
                    )

                baseline = await _fresh_baseline(db, conn.id)
                if baseline is None:
                    baseline = StreamBaseline(connection_id=conn.id, user_id=user_id, snapshot={}, approved=True)
                    db.add(baseline)
                baseline.snapshot = _project_cleared_snapshot(baseline.snapshot or {}, scope)
                baseline.approved = True
                baseline.observed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                if scope["collection"]:
                    conn.stremio_pushed_library_ids = []
                counts = {key: int(counts.get(key, 0)) for key in ("collection", "watched", "playback")}
                stats["clear_scope"] = scope
                stats["cleared"] = counts
                total = sum(counts.values())
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(
                    status=SyncStatus.completed, stats=stats, processed_items=total,
                    total_items=total, current_step="Clear complete", error_message=None,
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                ))
                await db.commit()
        except Exception as exc:
            logger.exception("Provider clear job %s failed", job_id)
            await db.rollback()
            # The guard is deliberately retained after a possibly partial
            # remote operation. A full resync or successful retry can clear it.
            await _set_job_failed(db, job_id, str(exc))


async def run_clear_data_job(user_id: int, connection_id: int, job_id: int) -> None:
    await _run_clear_data_job(user_id, connection_id, job_id)
