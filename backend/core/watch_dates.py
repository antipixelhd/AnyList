"""Watch-date normalization and safe reconciliation of estimated dates."""

from datetime import date, datetime, time, timezone

from sqlalchemy import select

from models import WatchEvent
from core.timestamps import milliseconds


def normalize_watch_datetime(value: date | datetime | int | float | str | None) -> datetime | None:
    """Convert a provider/user timestamp to the naive UTC values used by the DB."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        if value.strip().replace('.', '', 1).isdigit():
            return normalize_watch_datetime(float(value))
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return milliseconds(parsed)


def inferred_watch_datetime(fallback: date | datetime | int | float | str | None = None) -> datetime:
    """Return an inferred timestamp from evidence, or the current UTC time."""
    return normalize_watch_datetime(fallback or datetime.now(timezone.utc))


def replace_inferred_watch_date(event: WatchEvent, watched_at: datetime | None, *, inferred: bool = False) -> bool:
    """Update dates without downgrading episode evidence or local estimates.

    A reliable timestamp can replace a prior estimate and clears its inferred
    marker. An inferred timestamp can replace only missing or shared evidence.
    """
    normalized = normalize_watch_datetime(watched_at)
    if normalized is None:
        return False
    shared = bool(getattr(event, 'date_shared', False))
    if event.watched_at is not None and not event.date_inferred and not shared:
        return False
    if inferred and event.watched_at is not None and not shared:
        return False
    event.watched_at = normalized
    event.date_inferred = inferred
    event.date_shared = False
    return True


async def reconcile_inferred_watch_date(db, user_id: int, media_id: int, watched_at: datetime, *, authoritative: bool = False) -> bool:
    """Replace the sole inferred completed watch for a media item, if safe.

    Several inferred rows may represent separate rewatches, so they remain
    ambiguous. A confident event with the incoming timestamp means the source
    record is already represented and must not be merged into another event.
    """
    normalized = normalize_watch_datetime(watched_at)
    if normalized is None:
        return False
    confident_match = (await db.execute(select(WatchEvent.id).where(
        WatchEvent.user_id == user_id,
        WatchEvent.media_id == media_id,
        WatchEvent.completed.is_(True),
        WatchEvent.date_inferred.is_(False),
        WatchEvent.watched_at == normalized,
    ).limit(1))).scalar_one_or_none()
    if confident_match is not None:
        return False
    candidates = (await db.execute(select(WatchEvent).where(
        WatchEvent.user_id == user_id,
        WatchEvent.media_id == media_id,
        WatchEvent.completed.is_(True),
        (WatchEvent.date_inferred.is_(True) | WatchEvent.date_shared.is_(True)),
    ))).scalars().all()
    if len(candidates) != 1:
        return False
    current = normalize_watch_datetime(candidates[0].watched_at)
    # An exact echo of our exported estimate is not new date evidence, even
    # when the receiving provider normally supplies precise episode dates.
    if current == normalized:
        return True
    # Providers often round outbound echoes to date-only precision. Keep a
    # same-day estimate inferred so an echoed value is not treated as proof.
    if (not candidates[0].date_shared and current is not None and current.date() == normalized.date()
            and (not authoritative or current == normalized or normalized.time() == time.min)):
        return True
    from core.sync_reconciliation import collecting, _source, collect_watch
    state = collecting(user_id)
    if state:
        collect_watch(media_id, normalized)
        state.date_corrections.append((_source.get(), media_id, candidates[0].id, candidates[0].watched_at, normalized))
        return True
    return replace_inferred_watch_date(candidates[0], normalized, inferred=False)
