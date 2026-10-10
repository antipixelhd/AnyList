"""Pure provider projections. Names never establish cross-provider identity."""

import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from core.countries import country_codes, game_countries, numeric_country, screen_countries


PRIMARY = {"movie": "tmdb", "series": "tmdb", "game": "igdb", "book": "hardcover"}


def regular_episode_count(seasons):
    if not isinstance(seasons, list):
        return None
    regular = [s for s in seasons if isinstance(s, dict) and isinstance(s.get("season_number"), int) and s["season_number"] > 0]
    if not regular or not all(isinstance(s.get("episode_count"), int) and not isinstance(s["episode_count"], bool) and s["episode_count"] >= 0 for s in regular):
        return None
    return sum(s["episode_count"] for s in regular)


NAMESPACE_KINDS = {
    "tmdb.movie": {"movie"},
    "tmdb.series": {"series"},
    "tvdb.series": {"series"},
    "imdb.title": {"movie", "series"},
    "tmdb.person": {"person"},
    "tvdb.person": {"person"},
    "tmdb.company": {"organization"},
    "tmdb.network": {"organization"},
    "tvdb.company": {"organization"},
    "igdb.game": {"game"},
    "igdb.character": {"character"},
    "igdb.company": {"organization"},
    "igdb.collection": {"game_collection"},
    "igdb.release": {"release"},
    "igdb.game_version": {"release"},
    "rawg.game": {"game"},
    "rawg.developer": {"organization"},
    "rawg.publisher": {"organization"},
    "rawg.release": {"release"},
    "hardcover.book": {"book"},
    "hardcover.author": {"person"},
    "hardcover.publisher": {"organization"},
    "hardcover.character": {"character"},
    "hardcover.edition": {"edition"},
    "hardcover.series": {"book_series"},
    "openlibrary.book": {"book"},
    "openlibrary.edition": {"edition"},
    "openlibrary.author": {"person"},
    "isbn.10": {"edition"},
    "isbn.13": {"edition"},
    "steam.app": {"game"},
    "itad.game": {"game"},
}


def identity(namespace, value):
    return {"namespace": namespace, "external_id": str(value)}


def image(value):
    if not value:
        return None
    if isinstance(value, dict):
        value = value.get("url")
    if not isinstance(value, str):
        return None
    if value.startswith("//"):
        value = "https:" + value
    # Untrusted metadata URLs must never become executable browser links.
    return value if urlparse(value).scheme == "https" else None


def steam_app(url):
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or parsed.hostname != "store.steampowered.com":
        return None
    match = re.match(r"^/app/([1-9][0-9]*)(?:/|$)", parsed.path)
    return match.group(1) if match else None


def isbn(value, length):
    value = re.sub(r"[\s-]", "", str(value or "")).upper()
    if length == 10 and re.fullmatch(r"[0-9]{9}[0-9X]", value):
        return (
            value
            if sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(value))
            % 11
            == 0
            else None
        )
    if length == 13 and re.fullmatch(r"97[89][0-9]{10}", value):
        return (
            value
            if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(value)) % 10
            == 0
            else None
        )
    return None


def date(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).date().isoformat()
    return str(value)[:10]


def entity(
    kind, namespace, raw, name=None, description=None, artwork=None, attributes=None
):
    return {
        "kind": kind,
        "identities": [identity(namespace, raw["id"])],
        "name": name
        or raw.get("name")
        or raw.get("title")
        or f"{namespace}:{raw['id']}",
        "description": description,
        "image_url": image(artwork),
        "attributes": attributes or {},
    }


def document(work):
    return {
        "work": work,
        "credits": [],
        "characters": [],
        "editions": [],
        "releases": [],
        "relationships": [],
        "coverage": {},
    }


def normalize_tmdb(raw, kind):
    doc = document(
        entity(
            kind,
            f"tmdb.{kind}",
            raw,
            name=raw.get("title") or raw.get("name"),
            description=raw.get("overview"),
            artwork="https://image.tmdb.org/t/p/original" + raw["poster_path"]
            if raw.get("poster_path")
            else None,
            attributes={
                "release_date": raw.get("release_date") or raw.get("first_air_date"),
                "runtime": raw.get("runtime"),
                "origin_countries": raw.get("origin_country"),
                "production_countries": [
                    c.get("iso_3166_1") for c in raw.get("production_countries", [])
                ],
                "genres": raw.get("genres"),
                "episode_count": raw.get("number_of_episodes"),
                "episode_run_time": raw.get("episode_run_time"),
                "original_language": raw.get("original_language"),
                "regular_episode_count": regular_episode_count(raw.get("seasons")),
                **screen_countries(raw),
            },
        )
    )
    ids = raw.get("external_ids") or {}
    if ids.get("imdb_id") or raw.get("imdb_id"):
        doc["work"]["identities"].append(
            identity("imdb.title", ids.get("imdb_id") or raw["imdb_id"])
        )
    if kind == "series" and ids.get("tvdb_id"):
        doc["work"]["identities"].append(identity("tvdb.series", ids["tvdb_id"]))
    credits = raw.get("aggregate_credits") or raw.get("credits") or {}
    for group in ("cast", "crew"):
        for p in credits.get(group) or []:
            if not p.get("id"):
                continue
            person = entity(
                "person",
                "tmdb.person",
                p,
                artwork="https://image.tmdb.org/t/p/original" + p["profile_path"]
                if p.get("profile_path")
                else None,
            )
            roles = p.get("roles") if group == "cast" else p.get("jobs")
            for r in roles or [p]:
                job = r.get("job") or ("Actor" if group == "cast" else None)
                role = (
                    {
                        "Actor": "actor",
                        "Director": "director",
                        "Writer": "writer",
                        "Screenplay": "writer",
                        "Story": "writer",
                        "Teleplay": "writer",
                        "Producer": "producer",
                        "Executive Producer": "producer",
                    }.get(job, "contributor")
                    if job is not None
                    else None
                )
                doc["credits"].append(
                    {
                        "contributor": person,
                        "role": role,
                        "role_label": job,
                        "character_label": r.get("character"),
                        "source_key": r.get("credit_id")
                        or p.get("credit_id")
                        or f"{p['id']}:{job}:{r.get('character', '')}",
                        "scope": "series_aggregate"
                        if raw.get("aggregate_credits")
                        else "title",
                        "position": p.get("order"),
                    }
                )
    for field, namespace, role in [
        ("production_companies", "tmdb.company", "producer"),
        ("networks", "tmdb.network", "broadcaster"),
    ]:
        for p in raw.get(field) or []:
            doc["credits"].append(
                {
                    "contributor": entity(
                        "organization",
                        namespace,
                        p,
                        artwork="https://image.tmdb.org/t/p/original" + p["logo_path"]
                        if p.get("logo_path")
                        else None,
                    ),
                    "role": role,
                    "source_key": f"{field}:{p['id']}",
                }
            )
    doc["coverage"] = {
        "credits": "series_aggregate" if raw.get("aggregate_credits") else "title",
        "characters": "role_labels_only",
    }
    for r in (raw.get("recommendations") or {}).get("results", []):
        if r.get("id"):
            doc["relationships"].append(
                {
                    "target": entity(kind, f"tmdb.{kind}", r),
                    "relation": "recommendation",
                }
            )
    return doc


def normalize_tvdb(raw):
    doc = document(
        entity(
            "series",
            "tvdb.series",
            raw,
            description=raw.get("overview"),
            artwork=raw.get("image"),
            attributes={
                "release_date": raw.get("firstAired"),
                "origin_countries": country_codes(raw.get("originalCountry")),
                **screen_countries({"origin_country": country_codes(raw.get("originalCountry"))}),
                "original_language": raw.get("originalLanguage"),
                "genres": raw.get("genres"),
            },
        )
    )
    for x in raw.get("remoteIds") or []:
        ns = {"TheMovieDB.com": "tmdb.series", "IMDB": "imdb.title"}.get(
            x.get("sourceName")
        )
        if ns and x.get("id"):
            doc["work"]["identities"].append(identity(ns, x["id"]))
    for p in raw.get("characters") or []:
        pid = p.get("peopleId") or p.get("personId")
        if not pid:
            continue
        person = entity(
            "person",
            "tvdb.person",
            {"id": pid, "name": p.get("personName")},
            artwork=p.get("personImgURL"),
        )
        doc["credits"].append(
            {
                "contributor": person,
                "role": ("actor" if p["type"] == 3 else "contributor")
                if p.get("type") is not None
                else None,
                "role_label": p.get("peopleType"),
                "character_label": p.get("name"),
                "source_key": str(p["id"]),
                "position": p.get("sort"),
            }
        )
        # TVDB Character records are title/performer associations, not global
        # fictional identities. Artwork stays in the source snapshot.
    for p in raw.get("companies") or []:
        label = (p.get("companyType") or {}).get("companyTypeName", "")
        role = {
            "Network": "broadcaster",
            "Production Company": "producer",
            "Studio": "producer",
            "Distributor": "distributor",
        }.get(label, "contributor")
        doc["credits"].append(
            {
                "contributor": entity("organization", "tvdb.company", p),
                "role": role,
                "role_label": label,
                "source_key": f"company:{p['id']}:{role}",
            }
        )
    doc["coverage"] = {"characters": "title_performer_associations_only"}
    return doc


def normalize_igdb(raw):
    doc = document(
        entity(
            "game",
            "igdb.game",
            raw,
            description=raw.get("summary"),
            artwork=raw.get("cover"),
            attributes={
                "release_date": date(raw.get("first_release_date")),
                "genres": raw.get("genres"),
                "artwork": [image(a) for a in raw.get("artworks") or []],
                **game_countries(raw),
            },
        )
    )
    for x in raw.get("external_games") or []:
        if (x.get("external_game_source") or {}).get("name") == "Steam" and steam_app(
            x.get("url")
        ) == str(x.get("uid")):
            doc["work"]["identities"].append(identity("steam.app", x["uid"]))
    for p in raw.get("involved_companies") or []:
        c = p.get("company")
        if not isinstance(c, dict):
            continue
        for role in ("developer", "publisher", "supporting", "porting"):
            if p.get(role):
                doc["credits"].append(
                    {
                        "contributor": entity(
                            "organization",
                            "igdb.company",
                            c,
                            description=c.get("description"),
                            artwork=c.get("logo"),
                            attributes={"countries": [code] if (code := numeric_country(c.get("country"))) else []},
                        ),
                        "role": role,
                        "source_key": f"{p['id']}:{role}",
                    }
                )
    for p in raw.get("_characters") or []:
        doc["characters"].append(
            {
                "character": entity(
                    "character",
                    "igdb.character",
                    p,
                    description=p.get("description"),
                    artwork=p.get("mug_shot"),
                )
            }
        )
    for r in raw.get("release_dates") or []:
        platform = r.get("platform")
        if not isinstance(platform, dict):
            continue
        e = entity(
            "release",
            "igdb.release",
            r,
            name=f"{raw['name']} — {platform.get('name', platform['id'])}",
        )
        doc["releases"].append(
            {
                "entity": e,
                "platform_namespace": "igdb.platform",
                "platform_id": str(platform["id"]),
                "platform_name": platform.get("name"),
                "region": str(r.get("region")) if r.get("region") else None,
                "release_date": date(r.get("date")),
            }
        )
    for field, rel in [
        ("dlcs", "dlc"),
        ("expansions", "expansion"),
        ("standalone_expansions", "standalone_expansion"),
        ("remakes", "remake"),
        ("remasters", "remaster"),
        ("ports", "port"),
        ("similar_games", "recommendation"),
    ]:
        for r in raw.get(field) or []:
            if not isinstance(r, dict):
                r = {"id": r}
            parent = r.get("version_parent")
            target = (
                entity(
                    "game",
                    "igdb.game",
                    parent if isinstance(parent, dict) else {"id": parent},
                )
                if parent
                else entity("game", "igdb.game", r)
            )
            if parent:
                target["identities"].append(identity("igdb.game", r["id"]))
            doc["relationships"].append(
                {
                    "target": target,
                    "relation": rel,
                    "reverse": rel != "recommendation",
                }
            )
    for r in raw.get("collections") or []:
        if isinstance(r, dict):
            doc["relationships"].append(
                {
                    "target": entity("game_collection", "igdb.collection", r),
                    "relation": "series_member",
                }
            )
    parent = raw.get("parent_game")
    relation = {
        "DLC Add-on": "dlc",
        "Expansion": "expansion",
        "Standalone Expansion": "standalone_expansion",
        "Remake": "remake",
        "Remaster": "remaster",
        "Port": "port",
    }.get((raw.get("game_type") or {}).get("type"))
    if isinstance(parent, dict) and relation:
        doc["relationships"].append(
            {"target": entity("game", "igdb.game", parent), "relation": relation}
        )
    doc["coverage"] = raw.get("_coverage", {})
    return doc


def normalize_rawg(raw):
    doc = document(
        entity(
            "game",
            "rawg.game",
            raw,
            description=raw.get("description_raw"),
            artwork=raw.get("background_image"),
            attributes={
                "release_date": raw.get("released"),
                "genres": raw.get("genres"),
                "metacritic": raw.get("metacritic"),
            },
        )
    )
    for x in raw.get("_stores") or []:
        app = steam_app(x.get("url"))
        if app:
            doc["work"]["identities"].append(identity("steam.app", app))
    for field, role in [("developers", "developer"), ("publishers", "publisher")]:
        for p in raw.get(field) or []:
            doc["credits"].append(
                {
                    "contributor": entity(
                        "organization",
                        f"rawg.{role}",
                        p,
                        artwork=p.get("image_background"),
                    ),
                    "role": role,
                    "source_key": f"{role}:{p['id']}",
                }
            )
    for r in raw.get("platforms") or []:
        p = r.get("platform") or {}
        if not p.get("id"):
            continue
        e = entity(
            "release",
            "rawg.release",
            {
                "id": f"{raw['id']}:{p['id']}",
                "name": f"{raw['name']} — {p.get('name')}",
            },
        )
        doc["releases"].append(
            {
                "entity": e,
                "platform_namespace": "rawg.platform",
                "platform_id": str(p["id"]),
                "platform_name": p.get("name"),
                "release_date": r.get("released_at"),
            }
        )
    doc["coverage"] = {
        "characters": "unavailable",
        "people": "not_ingested",
        "stores": raw.get("_stores_complete", False),
    }
    return doc


def normalize_hardcover(raw):
    doc = document(
        entity(
            "book",
            "hardcover.book",
            raw,
            name=raw.get("title"),
            description=raw.get("description"),
            artwork=raw.get("cached_image"),
            attributes={
                "release_date": raw.get("release_date"),
                "pages": raw.get("pages"),
            },
        )
    )
    for c in (raw.get("_contributions") or []) + (
        raw.get("_edition_contributions") or []
    ):
        p = c.get("author")
        if not p:
            continue
        label = (c["contribution"] or "Author") if "contribution" in c else None
        role = (
            {
                "author": "author",
                "narrator": "narrator",
                "translator": "translator",
                "illustrator": "illustrator",
                "editor": "editor",
            }.get(label.lower(), "contributor")
            if label is not None
            else None
        )
        credit = {
            "contributor": entity(
                "person",
                "hardcover.author",
                p,
                description=p.get("bio"),
                artwork=p.get("cached_image"),
            ),
            "role": role,
            "role_label": label,
            "source_key": str(c["id"]),
        }
        if c in (raw.get("_edition_contributions") or []):
            credit.update(
                edition_identity=identity("hardcover.edition", c["contributable_id"]),
                scope="edition",
            )
        doc["credits"].append(credit)
    for c in raw.get("_characters") or []:
        p = c.get("character")
        if p:
            doc["characters"].append(
                {
                    "character": entity(
                        "character",
                        "hardcover.character",
                        p,
                        description=p.get("biography"),
                        artwork=p.get("_image"),
                    ),
                    "attributes": {
                        "spoiler": c.get("spoiler"),
                        "only_mentioned": c.get("only_mentioned"),
                    },
                }
            )
    for c in raw.get("_series") or []:
        p = c.get("series")
        if p:
            doc["relationships"].append(
                {
                    "target": entity(
                        "book_series",
                        "hardcover.series",
                        p,
                        description=p.get("description"),
                    ),
                    "relation": "series_member",
                    "attributes": {
                        "position": c.get("position"),
                        "compilation": c.get("compilation"),
                    },
                }
            )
    for p in raw.get("_editions") or []:
        e = entity(
            "edition",
            "hardcover.edition",
            p,
            name=p.get("title") or raw.get("title"),
            artwork=p.get("cached_image"),
        )
        for length in (10, 13):
            value = isbn(p.get(f"isbn_{length}"), length)
            if value:
                e["identities"].append(identity(f"isbn.{length}", value))
        publisher = p.get("publisher")
        doc["editions"].append(
            {
                "entity": e,
                "publisher": entity("organization", "hardcover.publisher", publisher)
                if publisher
                else None,
                "release_date": p.get("release_date"),
                "format": str(p["edition_format"]) if p.get("edition_format") else None,
                "language": (p.get("language") or {}).get("code2"),
                "pages": p.get("pages"),
            }
        )
    doc["coverage"] = raw.get("_coverage", {})
    return doc
