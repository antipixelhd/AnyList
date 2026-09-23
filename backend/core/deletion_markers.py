"""Keep deletion markers aligned with reset work that can still be delivered."""

from sqlalchemy import select

from models.tracking import CloudAction, StreamAction, SyncReview, TrackingDeletion


async def settle_marker_target(db, user_id: int, media_id: int, target: str) -> None:
    """Remove a target once it has no live reset row; retain the tombstone."""
    # Callers normally update an action immediately before settling its marker.
    # Explicitly flush so this is correct with autoflush disabled as well.
    await db.flush()
    if target.startswith("connection:"):
        target_id = int(target.split(":", 1)[1])
        live = (await db.execute(select(StreamAction.id).where(
            StreamAction.user_id == user_id,
            StreamAction.media_id == media_id,
            StreamAction.connection_id == target_id,
            StreamAction.action == "reset",
            StreamAction.state.in_(("pending", "conflict")),
        ).limit(1))).first()
    else:
        live = (await db.execute(select(CloudAction.id).where(
            CloudAction.user_id == user_id,
            CloudAction.media_id == media_id,
            CloudAction.provider == target,
            CloudAction.action == "reset",
            CloudAction.state.in_(("pending", "conflict")),
        ).limit(1))).first()
    if live:
        return

    marker = (await db.execute(select(TrackingDeletion).where(
        TrackingDeletion.user_id == user_id,
        TrackingDeletion.media_id == media_id,
    ))).scalar_one_or_none()
    if not marker:
        return
    marker.pending_connections = [item for item in marker.pending_connections or [] if item != target]
    if marker.pending_connections:
        return
    reviews = (await db.execute(select(SyncReview).where(
        SyncReview.user_id == user_id,
        SyncReview.media_id == media_id,
        SyncReview.kind == "outbound_pending",
        SyncReview.state == "pending",
    ))).scalars()
    for review in reviews:
        review.state = "corrected"
