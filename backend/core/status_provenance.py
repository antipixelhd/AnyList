"""Status-specific provenance used by reconciliation ordering."""

from datetime import datetime, timezone


def naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def status_changed_at(entry) -> datetime | None:
    """Use dedicated status time, falling back for pre-migration rows."""
    return naive_utc(entry.status_changed_at or entry.updated_at)


def mark_status_change(entry, source: str, changed_at: datetime | None = None) -> None:
    entry.status_source = source[:64]
    entry.status_changed_at = naive_utc(changed_at) or datetime.now(timezone.utc).replace(tzinfo=None)


def provider_changed_at(row: dict | None) -> datetime | None:
    """Extract a reliable provider timestamp when its adapter supplied one."""
    if not row:
        return None
    for field in ("last_watched", "watched_at", "updated_at", "modified_at"):
        value = row.get(field)
        if value in (None, ""):
            continue
        if isinstance(value, datetime):
            return naive_utc(value)
        if isinstance(value, (int, float)):
            seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
            return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)
        try:
            return naive_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        except ValueError:
            continue
    return None
