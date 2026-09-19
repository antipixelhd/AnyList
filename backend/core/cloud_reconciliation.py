"""First-import approval gates for Trakt, Simkl, and MDBList."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from models.tracking import CloudBaseline, SyncReview

SUPPORTED = {"trakt", "simkl", "mdblist"}


async def record_cloud_import(db, user_id: int, provider: str, summary: dict) -> CloudBaseline:
    if provider not in SUPPORTED:
        raise ValueError("Unsupported cloud tracker")
    baseline = (await db.execute(select(CloudBaseline).where(
        CloudBaseline.user_id == user_id,
        CloudBaseline.provider == provider,
    ))).scalar_one_or_none()
    first = baseline is None
    if first:
        baseline = CloudBaseline(user_id=user_id, provider=provider, approved=False)
        db.add(baseline)
    baseline.snapshot = dict(summary or {})
    baseline.observed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if first:
        label = {"trakt": "Trakt", "simkl": "Simkl", "mdblist": "MDBList"}[provider]
        db.add(SyncReview(
            user_id=user_id,
            provider=provider,
            kind="initial_cloud_import",
            message=(f"{label}: initial import completed. Review your merged lists and "
                     "confirm this summary before any outbound synchronization."),
        ))
    return baseline


async def require_cloud_reconciliation(db, user_id: int, provider: str) -> CloudBaseline:
    baseline = (await db.execute(select(CloudBaseline).where(
        CloudBaseline.user_id == user_id,
        CloudBaseline.provider == provider,
    ))).scalar_one_or_none()
    if not baseline or not baseline.approved:
        raise HTTPException(409, "Run an import and confirm its summary in Recent events before pushing to this provider")
    unresolved = (await db.execute(select(SyncReview.id).where(
        SyncReview.user_id == user_id,
        SyncReview.provider == provider,
        SyncReview.state == "pending",
        SyncReview.kind.in_(["rating_conflict", "cloud_conflict", "unmatched_import"]),
    ))).first()
    if unresolved:
        raise HTTPException(409, "Resolve this provider's conflicts in Recent events before pushing")
    return baseline


async def cloud_push_is_approved(db, user_id: int, provider: str) -> bool:
    try:
        await require_cloud_reconciliation(db, user_id, provider)
        return True
    except HTTPException:
        return False
