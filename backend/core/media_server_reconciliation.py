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
