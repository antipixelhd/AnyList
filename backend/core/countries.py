"""Country normalization with explicit provider semantics; no fuzzy geocoding."""

import json
from functools import lru_cache
from pathlib import Path
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


def numeric_country(value):
    """IGDB company countries use ISO 3166-1 numeric codes."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 999:
        return None
    record = pycountry.countries.get(numeric=f"{value:03d}")
    return record.alpha_2 if record else None


# Current MARC codes, normalized from the Library of Congress code list:
# https://www.loc.gov/marc/countries/countries_code.html (2026-10-10).
# MARC is a separate namespace: e.g. "br" means Burma, not Brazil.
MARC_COUNTRIES = json.loads(Path(__file__).with_name("marc_countries.json").read_text())


def publication_countries(data):
    code = data.get("publish_country")
    coded = MARC_COUNTRIES.get(code.strip().lower()) if isinstance(code, str) else None
    if coded:
        return [coded]
    result = set()
    places = data.get("publish_places") or []
    if isinstance(places, str):
        places = [places]
    for place in places:
        if not isinstance(place, str):
            continue
        # Only explicit country names, including a trailing country after a
        # city. Cities, language, subjects and publisher names imply nothing.
        name = place.rsplit(",", 1)[-1].strip(" []().")
        if len(name) <= 3:
            continue
        try:
            result.add(pycountry.countries.lookup(name).alpha_2)
        except LookupError:
            pass
    return sorted(result)


def game_countries(raw):
    developers, publishers = set(), set()
    for credit in raw.get("involved_companies") or []:
        code = numeric_country((credit.get("company") or {}).get("country"))
        if code:
            if credit.get("developer"):
                developers.add(code)
            if credit.get("publisher"):
                publishers.add(code)
    return {"developer_countries": sorted(developers), "publisher_countries": sorted(publishers),
            "countries": sorted(developers or publishers),
            "country_basis": "developer" if developers else "publisher" if publishers else None}


def screen_countries(data):
    origin = country_codes(data.get("origin_countries") or data.get("origin_country"))
    production = country_codes([v.get("iso_3166_1") if isinstance(v, dict) else v
                                for v in data.get("production_countries") or []])
    return {"countries": origin or production,
            "country_basis": "origin" if origin else "production" if production else None}
