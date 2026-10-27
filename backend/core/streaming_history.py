"""Source-independent observations consumed by streaming-history matching."""
from dataclasses import asdict, dataclass, field
from typing import Any
from math import isfinite

MIN_WATCH_SECONDS = 600


def observed_watch_seconds(row):
    """Prefer actual viewing time; providers without a counter expose offsets.

    A present but missing/invalid watch-time counter must never fall back to a seek.
    All normalized connection times are milliseconds.
    """
    value = row.get('watched_time') if 'watched_time' in row else row.get('position')
    seconds = float(value or 0) / 1000
    if not isfinite(seconds):
        raise ValueError('Invalid watch time')
    return max(0., seconds)


@dataclass(slots=True)
class WatchObservation:
    """One distinct source title/date observation after duplicate removal."""

    row: int
    source_title: str
    watched_at: str
    show_title: str | None = None
    season_label: str | None = None
    season_number: int | None = None
    episode_title: str | None = None
    source_rows: list[int] = field(default_factory=list)
    watched_seconds: float | None = None
    completed_evidence: bool = False
    episode_number: int | None = None
    duration_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ParsedHistory:
    rows: list[WatchObservation] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    total_rows: int = 0
    duplicate_rows: int = 0
    excluded_rows: int = 0
    language: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [row.to_dict() for row in self.rows],
            "errors": [dict(error) for error in self.errors],
            "total_rows": self.total_rows,
            "duplicate_rows": self.duplicate_rows,
            "excluded_rows": self.excluded_rows,
            "language": self.language,
        }


