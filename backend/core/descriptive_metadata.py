"""Descriptive TMDB fields retained by every movie/series metadata writer."""

from core import tmdb

FIELDS = (
    "original_language",
    "origin_country",
    "production_countries",
    "number_of_episodes",
    "episode_run_time",
    "production_companies",
    "created_by",
    "cast",
)


def retained_fields(previous):
    return {key: previous[key] for key in FIELDS if (previous or {}).get(key)}


def tvdb_fields(data, previous=None):
    fields = retained_fields(previous or {})
    for key in ("origin_country", "original_language"):
        if data.get(key):
            fields[key] = data[key]
    return fields


def tmdb_fields(data, previous=None):
    fields = retained_fields(previous or {})
    for key in (
        "original_language",
        "origin_country",
        "production_countries",
        "number_of_episodes",
        "episode_run_time",
        "created_by",
    ):
        if data.get(key):
            fields[key] = data[key]
    if data.get("production_companies"):
        fields["production_companies"] = [
            {**c, "logo_path": tmdb.poster_url(c.get("logo_path"), size="w185")}
            for c in data["production_companies"]
            if c.get("name")
        ]
    # Legacy screens use a preview; complete credits live in the catalogue.
    cast = (data.get("credits") or {}).get("cast")
    if cast:
        fields["cast"] = [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "character": c.get("character", ""),
                "profile_path": tmdb.poster_url(c.get("profile_path"), size="w185"),
            }
            for c in cast[:10]
        ]
    return fields
