"""First-import review for Jellyfin, Emby, and Plex connections."""
from __future__ import annotations

from datetime import datetime, timezone

from models.tracking import StreamBaseline, SyncReview


async def record_media_server_import(db, conn, stats: dict, *, complete: bool) -> bool:
    """Record a complete import summary; return whether outbound is approved."""
    if not complete:
        return False
    baseline = await db.get(StreamBaseline, conn.id)
    if baseline is None:
        baseline = StreamBaseline(
            connection_id=conn.id, user_id=conn.user_id, approved=False,
        )
        db.add(baseline)
        db.add(SyncReview(
            user_id=conn.user_id,
            connection_id=conn.id,
            kind="initial_import",
            message=(
                f"{conn.name}: initial import completed. Review your merged lists "
                "and confirm this summary before outbound synchronization."
            ),
        ))
    baseline.snapshot = {"kind": "media_server", "summary": dict(stats)}
    baseline.observed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return bool(baseline.approved)


async def reconcile_media_server_pull(
    db, conn, stats: dict, new_watched_ids: set[int], *, complete: bool,
) -> set[int]:
    """Merge a complete pull and return only watch IDs safe to export."""
    if not complete:
        return set()
    from core.tracking_import import import_tracking_history

    previous = await db.get(StreamBaseline, conn.id)
    previous_observed_at = previous.observed_at if previous else None
    newly_tracked: set[int] = set()
    stats["tracked_entries"] = await import_tracking_history(
        db, conn.user_id, added_media_ids=newly_tracked,
    )
    approved = bool(previous and previous.approved)
    if not approved:
        await record_media_server_import(db, conn, stats, complete=True)
        return set()
    from core.cloud_history_reconciliation import reconcile_cloud_watch_events

    accepted: set[int] = set()
    outcome = await reconcile_cloud_watch_events(
        db,
        user_id=conn.user_id,
        provider=conn.type,
        new_media_ids=new_watched_ids,
        applied_media_ids=accepted,
        connection_id=conn.id,
        initial_import_override=False,
        newly_tracked_ids=newly_tracked,
        observed_after=previous_observed_at,
    )
    stats["tracking_updates"] = outcome["applied"]
    stats["tracking_conflicts"] = outcome["conflicts"]
    await record_media_server_import(db, conn, stats, complete=True)
    return accepted
