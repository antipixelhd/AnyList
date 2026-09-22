"""First-import review for Jellyfin, Emby, and Plex connections."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from core.cloud_rating_reconciliation import _entry_score, _set_entry_score
from models import Rating
from models.ratings import RatingChanges, RatingKey
from models.tracking import StreamBaseline, SyncReview, TrackedEntry


def _rating_key(key: RatingKey) -> str:
    return f"{key[0]}:{key[1] if key[1] is not None else ''}"


async def _reconcile_ratings(db, conn, observed: RatingChanges, previous: StreamBaseline | None):
    changed: RatingChanges = {}
    if not observed:
        return changed, 0
    media_ids = {media_id for media_id, _ in observed}
    ratings = (await db.execute(select(Rating).where(
        Rating.user_id == conn.user_id,
        Rating.media_id.in_(media_ids),
        Rating.episode_order.is_(None),
    ))).scalars().all()
    by_key = {(row.media_id, row.season_number): row for row in ratings}
    entries = (await db.execute(select(TrackedEntry).where(
        TrackedEntry.user_id == conn.user_id,
        TrackedEntry.media_id.in_(media_ids),
    ))).scalars().all()
    by_media = {row.media_id: row for row in entries}
    previous_ratings = (previous.snapshot or {}).get("ratings", {}) if previous else {}
    conflicts = 0
    for key, remote_score in observed.items():
        source_key = _rating_key(key)
        prior_source_score = previous_ratings.get(source_key)
        if prior_source_score is not None and float(prior_source_score) == remote_score:
            continue
        row = by_key.get(key)
        entry = by_media.get(key[0])
        local_score = _entry_score(entry, key[1])
        if local_score is None and row is not None:
            local_score = row.rating
        if local_score == remote_score:
            continue
        local_changed_since_baseline = bool(
            previous and row and row.rated_at and previous.observed_at
            and row.rated_at > previous.observed_at
        )
        concurrent = local_score is not None and (
            prior_source_score is None
            or float(prior_source_score) != local_score
            or local_changed_since_baseline
        )
        if concurrent:
            pending = (await db.execute(select(SyncReview).where(
                SyncReview.user_id == conn.user_id,
                SyncReview.connection_id == conn.id,
                SyncReview.media_id == key[0],
                SyncReview.season_number == key[1],
                SyncReview.kind == "rating_conflict",
                SyncReview.state == "pending",
            ).limit(1))).scalar_one_or_none()
            if pending is None:
                db.add(SyncReview(
                    user_id=conn.user_id, connection_id=conn.id,
                    provider=conn.type, media_id=key[0],
                    season_number=key[1], kind="rating_conflict",
                    previous_score=local_score, proposed_score=remote_score,
                    message=(f"{conn.name}: the imported rating differs from your local "
                             "rating, and their order cannot be established. Your local value was kept."),
                ))
            else:
                pending.proposed_score = remote_score
            conflicts += 1
            continue
        if row is None:
            row = Rating(
                user_id=conn.user_id, media_id=key[0],
                season_number=key[1], rating=remote_score,
            )
            db.add(row)
            by_key[key] = row
        else:
            row.rating = remote_score
            row.rated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if entry is not None:
            _set_entry_score(entry, key[1], remote_score)
        changed[key] = remote_score
    return changed, conflicts


async def record_media_server_import(
    db, conn, stats: dict, *, complete: bool, observed_ratings: RatingChanges | None = None,
) -> bool:
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
    baseline.snapshot = {
        "kind": "media_server", "summary": dict(stats),
        "ratings": {_rating_key(key): score for key, score in (observed_ratings or {}).items()},
    }
    baseline.observed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return bool(baseline.approved)


async def reconcile_media_server_pull(
    db, conn, stats: dict, new_watched_ids: set[int], observed_ratings: RatingChanges,
    *, complete: bool,
) -> tuple[set[int], RatingChanges]:
    """Merge a complete pull and return only fields safe to export."""
    if not complete:
        return set(), {}
    from core.tracking_import import import_tracking_history

    previous = await db.get(StreamBaseline, conn.id)
    previous_observed_at = previous.observed_at if previous else None
    valid_ratings = {
        key: score for key, score in observed_ratings.items()
        if 0 < score <= 10
    }
    stats["skipped"] = stats.get("skipped", 0) + len(observed_ratings) - len(valid_ratings)
    changed_ratings, rating_conflicts = await _reconcile_ratings(
        db, conn, valid_ratings, previous,
    )
    stats["ratings"] = len(changed_ratings)
    stats["rating_conflicts"] = rating_conflicts
    newly_tracked: set[int] = set()
    stats["tracked_entries"] = await import_tracking_history(
        db, conn.user_id, added_media_ids=newly_tracked,
    )
    approved = bool(previous and previous.approved)
    if not approved:
        await record_media_server_import(
            db, conn, stats, complete=True, observed_ratings=valid_ratings,
        )
        return set(), {}
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
    await record_media_server_import(
        db, conn, stats, complete=True, observed_ratings=valid_ratings,
    )
    return accepted, changed_ratings
