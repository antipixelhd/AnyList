"""Provider score conversion without losing the local rating."""
from decimal import Decimal, ROUND_HALF_UP


def project_score(value: float, increment: float = 1.0) -> float:
    """Round to a provider increment with exact midpoints rounded upward."""
    step = Decimal(str(increment))
    projected = (Decimal(str(value)) / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step
    return float(max(Decimal("0"), min(Decimal("10"), projected)))


def integer_provider_score(value: float) -> int:
    return max(1, min(10, int(project_score(value))))


def is_projected_echo(local_value: float | None, remote_value: float | None) -> bool:
    if local_value is None or remote_value is None:
        return False
    return integer_provider_score(local_value) == int(remote_value)
