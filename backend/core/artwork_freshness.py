"""Revalidate saved title artwork more often near release."""
from datetime import date, datetime, time, timedelta

from core.game_release_dates import bounds


def artwork_expiry(release, fetched_at: datetime) -> datetime:
    interval = bounds(release)
    if not interval:
        return fetched_at + timedelta(days=30)
    first, last = map(date.fromisoformat, interval)
    observed = fetched_at.date()
    if observed < first:
        days = 3 if (first - observed).days <= 30 else 14
        release_day = datetime.combine(first, time.min, tzinfo=fetched_at.tzinfo)
        return min(fetched_at + timedelta(days=days), release_day)
    age = (observed - last).days
    days = 3 if age < 30 else 7 if age < 180 else 30 if age < 365 else 90 if age < 1095 else 365
    return fetched_at + timedelta(days=days)


def cache_time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def saved_expiry(cache, release, legacy_days):
    """Recalculate legacy successful entries using their original observation time."""
    stored = cache_time(cache.get('expires_at'))
    fetched = cache_time(cache.get('fetched_at'))
    if fetched is None and stored:
        fetched = stored - timedelta(days=legacy_days)
    return artwork_expiry(release, fetched) if fetched else None
