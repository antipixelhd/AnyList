"""Exact provider country codes, never company-country or fuzzy-name inference."""

from functools import lru_cache
import pycountry


@lru_cache(maxsize=512)
def country_code(value: str) -> str | None:
    code = value.strip().upper()
    record = pycountry.countries.get(**({"alpha_2": code} if len(code) == 2 else {"alpha_3": code}))
    return record.alpha_2 if record else None


def country_codes(values) -> list[str]:
    if isinstance(values, str):
        values = [values]
    return sorted({code for value in values or [] if isinstance(value, str) and (code := country_code(value))})
