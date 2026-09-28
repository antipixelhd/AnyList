"""Shared timestamp precision for storage, comparisons, and provider payloads."""
from datetime import datetime


def milliseconds(value: datetime | None) -> datetime | None:
    """Truncate, rather than round, while preserving timezone and null values."""
    if value is None:
        return None
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)
