"""Merge cloud ratings without silently replacing local list scores."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from core.rating_projection import is_projected_echo
from core.tracking_rules import effective_score
from models import Rating
from models.tracking import SyncReview, TrackedEntry


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _entry_score(entry: TrackedEntry | None, season_number: int | None) -> float | None:
    if entry is None:
        return None
    if season_number is None:
        return effective_score(entry.rating_mode, entry.manual_score, entry.season_scores or {})
    value = (entry.season_scores or {}).get(str(season_number))
    return float(value) if value is not None else None


def _set_entry_score(entry: TrackedEntry, season_number: int | None, score: float) -> None:
    if season_number is None:
        entry.manual_score = score
        entry.rating_mode = "manual"
        return
    entry.season_scores = {**(entry.season_scores or {}), str(season_number): score}


async def _pending_conflict(db, user_id: int, provider: str, media_id: int, season_number: int | None):
    query = select(SyncReview).where(
        SyncReview.user_id == user_id,
        SyncReview.provider == provider,
        SyncReview.media_id == media_id,
        SyncReview.kind == "rating_conflict",
        SyncReview.state == "pending",
    )
    if season_number is None:
        query = query.where(SyncReview.season_number.is_(None))
    else:
        query = query.where(SyncReview.season_number == season_number)
    return (await db.execute(query)).scalar_one_or_none()


async def reconcile_cloud_rating(
    db,
    *,
    provider: str,
    user_id: int,
    media,
    season_number: int | None,
    remote_score: float,
    remote_rated_at: datetime | None,
    existing: dict[tuple[int, int | None], Rating],
    changed: dict[tuple[int, int | None], float],
) -> str:
    """Return ``applied``, ``skipped``, or ``conflict`` for one remote value."""
    remote_score = float(remote_score)
    if not 0 < remote_score <= 10:
        raise ValueError("Rating must be between 0.5 and 10")
    remote_rated_at = _naive_utc(remote_rated_at)
    key = (media.id, season_number)
    current = existing.get(key)
    entry = (await db.execute(select(TrackedEntry).where(
        TrackedEntry.user_id == user_id,
        TrackedEntry.media_id == media.id,
    ))).scalar_one_or_none()
    local_score = _entry_score(entry, season_number)
    if local_score is None and current is not None and current.rating is not None:
        local_score = float(current.rating)

    if local_score is not None and (
        local_score == remote_score or is_projected_echo(local_score, remote_score)
    ):
        # Keep the provider-facing row aligned to the precise local value. A
        # returned integer projection is an acknowledgment, not a new edit.
        if current is not None and current.rating != local_score:
            current.rating = local_score
        return "skipped"

    local_rated_at = _naive_utc(current.rated_at) if current is not None else None
    if local_rated_at is None and entry is not None:
        local_rated_at = _naive_utc(entry.updated_at)

    # A whole-show provider value cannot silently replace a calculated season
    # average. The user can explicitly accept it, which switches to manual mode
    # while retaining all individual season scores.
    calculated_show = bool(entry and season_number is None and entry.rating_mode == "average")
    remote_is_newer = bool(
        remote_rated_at is not None
        and local_rated_at is not None
        and remote_rated_at > local_rated_at
    )
    local_is_newer = bool(
        remote_rated_at is not None
        and local_rated_at is not None
        and remote_rated_at < local_rated_at
    )

    if local_score is not None and (calculated_show or not remote_is_newer):
        # Restore the legacy provider row to the list's authoritative value so
        # an unrelated later push cannot export the conflicting remote value.
        if current is None:
            current = Rating(
                user_id=user_id,
                media_id=media.id,
                season_number=season_number,
                rating=local_score,
                rated_at=local_rated_at or datetime.now(timezone.utc).replace(tzinfo=None),
            )
            db.add(current)
            existing[key] = current
        else:
            current.rating = local_score
        if local_is_newer:
            return "skipped"
        review = await _pending_conflict(db, user_id, provider, media.id, season_number)
        label = {"trakt": "Trakt", "simkl": "Simkl", "mdblist": "MDBList"}.get(provider, provider)
        scope = f"season {season_number}" if season_number is not None else "title"
        if review is None:
            db.add(SyncReview(
                user_id=user_id,
                media_id=media.id,
                provider=provider,
                kind="rating_conflict",
                previous_score=local_score,
                proposed_score=remote_score,
                season_number=season_number,
                message=(
                    f"{label}: the imported {scope} rating differs from your local rating, "
                    "and their order cannot be established reliably. Your local value was kept."
                ),
            ))
        else:
            review.proposed_score = remote_score
            review.message = (
                f"{label}: the imported {scope} rating still differs from your local rating. "
                "Your local value was kept."
            )
        return "conflict"

    rated_at = remote_rated_at or datetime.now(timezone.utc).replace(tzinfo=None)
    if current is None:
        current = Rating(
            user_id=user_id,
            media_id=media.id,
            season_number=season_number,
            rating=remote_score,
            rated_at=rated_at,
        )
        db.add(current)
        existing[key] = current
    else:
        current.rating = remote_score
        current.rated_at = rated_at
    if entry is not None:
        _set_entry_score(entry, season_number, remote_score)
    changed[key] = remote_score
    return "applied"
