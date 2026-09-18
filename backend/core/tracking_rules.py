"""Provider-independent tracking rules. No network, database or wall-clock reads."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum


class TrackingStatus(str, Enum):
    planning = "planning"
    watching = "watching"
    paused = "paused"
    dropped = "dropped"
    completed = "completed"


def normalize_score(value: float | None) -> float | None:
    if value is None or value == 0:
        return None
    score = Decimal(str(value))
    if not score.is_finite() or not Decimal("0.5") <= score <= 10 or score % Decimal("0.5"):
        raise ValueError("Choose a half-point score from 0.5 to 10, or 0 for unrated")
    return float(score)


def effective_score(mode: str, manual: float | None, seasons: dict) -> float | None:
    if mode == "manual":
        return manual if manual and manual > 0 else None
    scores = [Decimal(str(v)) for k, v in seasons.items() if int(k) > 0 and v and v > 0]
    return float(sum(scores) / len(scores)) if scores else None


def project_score(score: float | None, increment: float = 1) -> float | None:
    if score is None:
        return None
    step = Decimal(str(increment))
    if step <= 0 or not step.is_finite():
        raise ValueError("Increment must be positive")
    return float((Decimal(str(score)) / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step)


def observed_status(previous: str | None, completed: bool, playing: bool) -> str:
    if previous == "completed" or completed:
        return "completed"
    return "watching" if playing else (previous or "planning")


def default_dates(previous: str | None, status: str, start: date | None, finish: date | None, today: date):
    """Only transitions create defaults; explicit field edits are applied afterwards."""
    if status != previous:
        if status == "watching" and start is None:
            start = today
        if status == "completed" and finish is None:
            finish = today
    return start, finish


def removed_keys(baseline: set | None, snapshot: set, complete: bool) -> set:
    """A first or incomplete snapshot is never evidence of removal."""
    return baseline - snapshot if baseline is not None and complete else set()
