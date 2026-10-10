"""One canonical title cohort for every statistics metric. No network or writes."""

import math
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import or_, select

from core.countries import country_codes
from core.statistics_genres import genre_groups, poster
from core.statistics_actors import load_actors, actor_groups
from core.statistics_studios import load_studios, studio_groups
from core.tracking_rules import effective_score
from models.base import MediaType
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueLegacyLink, CatalogueShowLink
from models.events import WatchEvent
from models.global_settings import GlobalSettings
from models.media import Media
from models.show import Show
from models.tracking import TrackedEntry

SCOPES = ("all", "movie", "series")
STATUSES = ("watching", "completed", "paused", "dropped", "planning")
LENGTH_BINS = ((1, "1"), (6, "2–6"), (16, "7–16"), (28, "17–28"), (55, "29–55"), (100, "56–100"), (math.inf, "101+"))


def positive_number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (ValueError, TypeError):
        return None


def year_of(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10]).year
    except ValueError:
        return None


def genres_of(data):
    return {str(g if isinstance(g, str) else g.get("name", "")).strip() for g in data.get("genres", []) if isinstance(g, (str, dict))} - {""}


def anime(data):
    return "animation" in {g.lower() for g in genres_of(data)} and (
        data.get("original_language") in ("ja", "jpn") or "JP" in countries_of(data, "series")
    )


def countries_of(data, kind):
    origin = country_codes(data.get("origin_countries") or data.get("origin_country"))
    production = country_codes([v.get("iso_3166_1") if isinstance(v, dict) else v for v in data.get("production_countries", [])])
    return origin or production


def regular_total(data):
    count = data.get("regular_episode_count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    ids = data.get("tracking_episode_ids")
    if isinstance(ids, list) and data.get("tracking_catalogue_refreshed_at"):
        return len(set(ids))
    seasons = data.get("seasons")
    if isinstance(seasons, list) and seasons:
        regular = [s for s in seasons if isinstance(s, dict) and isinstance(s.get("season_number"), int) and s["season_number"] > 0]
        if regular and all(isinstance(s.get("episode_count"), int) and s["episode_count"] >= 0 for s in regular):
            return sum(s["episode_count"] for s in regular)
    return None


def runtime_for(media, title):
    direct = positive_number(media.runtime) or positive_number((media.tmdb_data or {}).get("runtime"))
    if direct:
        return direct, "known"
    if media.media_type == MediaType.movie:
        value = positive_number(title["data"].get("runtime"))
        return value, "known" if value else "missing"
    values = title["data"].get("episode_run_time") or []
    if not isinstance(values, list):
        values = [values]
    value = next((v for raw in values if (v := positive_number(raw))), None)
    return value, "estimated" if value else "missing"


async def load_facts(db, user_id):
    """Bound reads to this user's titles/history and their shared local metadata."""
    entries = list((await db.execute(select(TrackedEntry, Media).join(Media).where(
        TrackedEntry.user_id == user_id, Media.media_type.in_([MediaType.movie, MediaType.series]),
    ))).all())
    events = list((await db.execute(select(WatchEvent, Media).join(Media).where(
        WatchEvent.user_id == user_id, WatchEvent.completed.is_(True),
        Media.media_type.in_([MediaType.movie, MediaType.episode]),
    ))).all())
    movie_ids = {m.id for _, m in events if m.media_type == MediaType.movie}
    title_rows = {m.id: m for _, m in entries}
    title_rows.update({m.id: m for _, m in events if m.id in movie_ids})
    tmdb_ids = {m.tmdb_id for m in title_rows.values() if m.media_type == MediaType.series and m.tmdb_id}
    tvdb_ids = {m.tvdb_id for m in title_rows.values() if m.media_type == MediaType.series and m.tvdb_id}
    show_ids = {m.show_id for _, m in events if m.show_id}
    shows = list((await db.scalars(select(Show).where(or_(Show.id.in_(show_ids), Show.tmdb_id.in_(tmdb_ids), Show.tvdb_id.in_(tvdb_ids))))).all())
    show_ids.update(s.id for s in shows)
    # Include whole-series media for history-only titles, without making them listed.
    if shows:
        extra = await db.scalars(select(Media).where(Media.media_type == MediaType.series, or_(
            Media.tmdb_id.in_({s.tmdb_id for s in shows if s.tmdb_id}),
            Media.tvdb_id.in_({s.tvdb_id for s in shows if s.tvdb_id}),
        )))
        title_rows.update({m.id: m for m in extra})
    links = dict((await db.execute(select(CatalogueLegacyLink.media_id, CatalogueLegacyLink.entity_id).where(CatalogueLegacyLink.media_id.in_(title_rows)))).all())
    show_links = dict((await db.execute(select(CatalogueShowLink.show_id, CatalogueShowLink.entity_id).where(CatalogueShowLink.show_id.in_(show_ids)))).all())
    identity_terms = []
    for kind in ("movie", "series"):
        for provider in ("tmdb", "tvdb"):
            ids = {str(getattr(m, provider + "_id")) for m in title_rows.values() if m.media_type.value == kind and getattr(m, provider + "_id")}
            if kind == "series":
                ids.update(str(getattr(s, provider + "_id")) for s in shows if getattr(s, provider + "_id"))
            if ids:
                identity_terms.append((CatalogueIdentity.namespace == f"{provider}.{kind}") & CatalogueIdentity.external_id.in_(ids))
    identities = {}
    if identity_terms:
        identities = {(i.namespace, i.external_id): i.entity_id for i in await db.scalars(select(CatalogueIdentity).where(or_(*identity_terms)))}
    entity_ids = set(links.values()) | set(show_links.values()) | set(identities.values())
    entities = {e.id: e for e in await db.scalars(select(CatalogueEntity).where(CatalogueEntity.id.in_(entity_ids), CatalogueEntity.kind.in_(["movie", "series"])))}
    episodes = list((await db.scalars(select(Media).where(Media.show_id.in_(show_ids), Media.media_type == MediaType.episode))).all())
    settings = await db.get(GlobalSettings, 1)
    facts, coverage = title_facts(entries, events, shows, list(title_rows.values()), episodes, links, show_links, identities, entities,
                                 show_anime=bool(settings and settings.show_anime))
    await load_actors(db, facts)
    await load_studios(db, facts)
    return facts, coverage


def title_facts(entries, events, shows, media, episodes, links, show_links, identities, entities, *, show_anime):
    facts, by_media, by_show = {}, {}, {}
    coverage = {"orphan_episode_plays": 0, "identity_conflicts": 0, "hidden_title_count": 0}

    def canonical(row, kind, bridge=None):
        candidates = {identities[(f"{p}.{kind}", str(getattr(row, p + "_id")))] for p in ("tmdb", "tvdb")
                      if getattr(row, p + "_id") and (f"{p}.{kind}", str(getattr(row, p + "_id"))) in identities}
        if bridge:
            candidates.add(bridge)
        candidates = {value for value in candidates if value in entities and entities[value].kind == kind}
        if len(candidates) == 1:
            return "catalogue:" + str(next(iter(candidates)))
        if len(candidates) > 1:
            coverage["identity_conflicts"] += 1
        return None

    def fact(key, kind, row, data):
        if key not in facts:
            entity = entities.get(int(key.split(":")[1])) if key.startswith("catalogue:") else None
            facts[key] = {"key": key, "kind": kind, "entity_id": entity.id if entity else None,
                          "data": {}, "listed": False, "status": None, "score": None,
                          "minutes": 0.0, "plays": 0, "episode_plays": 0, "episodes": set(),
                          "years": {}, "runtime_known": 0, "runtime_estimated": 0, "runtime_missing": 0,
                          "authoritative_plays": 0, "unattributed_plays": 0, "entry_order": None,
                          "planned_minutes": 0.0, "planned_unknown_episodes": 0, "planned_partial": False,
                          "planned_known_episodes": 0, "media": [], "shows": [], "progress": 0}
            facts[key].update(name=getattr(entity, "name", None) or getattr(row, "title", None) or key,
                              poster=poster(getattr(entity, "image_url", None) or getattr(row, "poster_path", None)),
                              detail_media_id=None)
        target = facts[key]
        if target["name"] == key and getattr(row, "title", None):
            target["name"] = row.title
        if not target["poster"]:
            target["poster"] = poster(getattr(row, "poster_path", None))
        # Canonical fields win; retained local metadata fills missing fields.
        target["data"].update({k: v for k, v in data.items() if v is not None and v != [] and v != ""})
        entity = entities.get(target["entity_id"])
        if entity:
            target["data"].update({k: v for k, v in entity.attributes.items() if v is not None and v != [] and v != ""})
        return target

    for show in shows:
        key = canonical(show, "series", show_links.get(show.id)) or f"show:{show.id}"
        data = {**(show.tmdb_data or {}), "release_date": show.first_air_date}
        fact(key, "series", show, data)["shows"].append(show)
        by_show[show.id] = key
    for row in media:
        kind = row.media_type.value
        key = canonical(row, kind, links.get(row.id))
        if kind == "series":
            matches = {by_show[s.id] for s in shows if (row.tmdb_id and s.tmdb_id == row.tmdb_id) or (row.tvdb_id and s.tvdb_id == row.tvdb_id)}
            if key:
                matches.add(key)
            if len(matches) == 1:
                key = next(iter(matches))
            elif len(matches) > 1:
                coverage["identity_conflicts"] += 1
                key = f"media:{row.id}"
        key = key or (f"tmdb.{kind}:{row.tmdb_id}" if row.tmdb_id else f"media:{row.id}")
        data = {**(row.tmdb_data or {})}
        if row.release_date:
            data["release_date"] = row.release_date
        if kind == "movie" and row.runtime:
            data["runtime"] = row.runtime
        fact(key, kind, row, data)["media"].append(row)
        if facts[key]["detail_media_id"] is None:
            facts[key]["detail_media_id"] = row.id
        by_media[row.id] = key
    hidden = {key for key, value in facts.items() if not show_anime and anime(value["data"])}
    coverage["hidden_title_count"] = len(hidden)
    for entry, row in entries:
        target = facts[by_media[row.id]]
        order = (entry.updated_at or datetime.min, entry.id or 0)
        if target["entry_order"] is not None and target["entry_order"] >= order:
            continue
        try:
            score = positive_number(effective_score(entry.rating_mode, entry.manual_score, entry.season_scores or {}))
        except (ValueError, TypeError, ArithmeticError):
            score = None
        target.update(listed=True, status=entry.status, score=score if score and score <= 10 else None,
                      progress=max(entry.progress or 0, 0), entry_order=order, detail_media_id=row.id)
    for event, row in events:
        key = by_media.get(row.id) if row.media_type == MediaType.movie else by_show.get(row.show_id)
        plays = max(event.play_count or 1, 1)
        if key is None:
            coverage["orphan_episode_plays"] += plays
            continue
        if key in hidden:
            continue
        target = facts[key]
        minutes, quality = runtime_for(row, target)
        target["plays"] += plays
        target["minutes"] += (minutes or 0) * plays
        target["runtime_" + quality] += plays
        if row.media_type == MediaType.episode:
            target["episode_plays"] += plays
            target["episodes"].add(row.id)
        dated = bool(event.watched_at and not (event.date_inferred or event.date_shared or event.provisional))
        if dated:
            # A collapsed repeat row establishes one play's date, never all repeats.
            year = target["years"].setdefault(event.watched_at.year, {"minutes": 0.0, "plays": 0})
            year["minutes"] += minutes or 0
            year["plays"] += 1
            target["authoritative_plays"] += 1
        target["unattributed_plays"] += plays - int(dated)
    episodes_by_show = defaultdict(list)
    for episode in episodes:
        episodes_by_show[episode.show_id].append(episode)
    today = datetime.now(timezone.utc).date().isoformat()
    for key, target in facts.items():
        if target["status"] != "planning" or key in hidden:
            continue
        if target["kind"] == "movie":
            if target["plays"]:
                continue
            value = positive_number(target["data"].get("runtime"))
            target["planned_minutes"] = value or 0
            target["planned_partial"] = value is None
            continue
        catalogues = [m for m in target["media"] if isinstance((m.tmdb_data or {}).get("tracking_episode_ids"), list)
                      and (m.tmdb_data or {}).get("tracking_catalogue_refreshed_at")]
        if not catalogues or (target["progress"] > len(target["episodes"])):
            target["planned_partial"] = True
            continue
        remaining = {}
        complete = True
        for source in catalogues:
            metadata = source.tmdb_data or {}
            ids = set(metadata["tracking_episode_ids"])
            provider = metadata.get("tracking_catalogue_provider", "tmdb")
            if provider not in ("tmdb", "tvdb"):
                complete = False
                continue
            linked_shows = [s for s in target["shows"] if (source.tmdb_id and s.tmdb_id == source.tmdb_id) or (source.tvdb_id and s.tvdb_id == source.tvdb_id)]
            retained = [e for s in linked_shows for e in episodes_by_show[s.id] if (e.season_number or 0) > 0 and getattr(e, provider + "_id") in ids]
            complete = complete and ids <= {getattr(e, provider + "_id") for e in retained}
            for e in retained:
                if e.id in target["episodes"]:
                    continue
                known_release = year_of(e.release_date) is not None
                imported_released = (e.tmdb_data or {}).get("tracking_import_released") is True
                if imported_released or (known_release and e.release_date[:10] <= today):
                    remaining[e.id] = e
                elif not known_release:
                    complete = False
        target["planned_partial"] = not complete
        for episode in remaining.values():
            value, quality = runtime_for(episode, target)
            target["planned_minutes"] += value or 0
            if quality == "missing":
                target["planned_unknown_episodes"] += 1
                target["planned_partial"] = True
            else:
                target["planned_known_episodes"] += 1
    return [value for key, value in facts.items() if key not in hidden], coverage


def grouped(values, label, *, year=None):
    scores = [t["score"] for t in values if t["score"] is not None]
    return {"key": str(label), "label": str(label), "titles": len(values),
            "minutes": sum(t["minutes"] if year is None else t["years"][year]["minutes"] for t in values),
            "mean_score": sum(scores) / len(scores) if scores else None, "rated_titles": len(scores)}


def yearly(values, watched=False):
    buckets = defaultdict(list)
    for title in values:
        for year in title["years"] if watched else [year_of(title["data"].get("release_date"))]:
            buckets[year].append(title)
    years = sorted(y for y in buckets if y is not None)
    result = [grouped(buckets[y], y, year=y if watched else None) for y in range(years[0], years[-1] + 1)] if years else []
    if None in buckets and not watched:
        result.append(grouped(buckets[None], "Unknown"))
    return result


def aggregate_scope(facts, coverage, scope):
    titles = [t for t in facts if scope == "all" or t["kind"] == scope]
    listed = [t for t in titles if t["listed"]]
    watched = [t for t in titles if t["plays"]]
    rated = [t["score"] for t in watched if t["score"] is not None]
    planned = [t for t in listed if t["status"] == "planning"]
    average = sum(rated) / len(rated) if rated else None
    score_bins = [grouped([t for t in watched if t["score"] is not None and i / 2 < t["score"] <= (i + 1) / 2], f"{(i + 1) / 2:g}") for i in range(20)]
    episode_bins = defaultdict(list)
    for title in watched:
        if title["kind"] != "series":
            continue
        total = regular_total(title["data"])
        label = next((label for ceiling, label in LENGTH_BINS if total and total <= ceiling), "Unknown")
        episode_bins[label].append(title)
    countries = defaultdict(lambda: {"titles": 0, "share": 0.0})
    for title in watched:
        codes = countries_of(title["data"], title["kind"]) or ["Unknown"]
        for code in codes:
            countries[code]["titles"] += 1
            countries[code]["share"] += 1 / len(codes)
    statuses = [{"key": status, "label": status, "titles": sum(t["status"] == status for t in listed)} for status in STATUSES]
    formats = [{"key": kind, "label": "Movies" if kind == "movie" else "Series", "titles": sum(t["kind"] == kind for t in watched)} for kind in ("movie", "series")]
    known_minutes = sum(t["minutes"] for t in watched)
    planned_minutes = sum(t["planned_minutes"] for t in planned)
    runtime_known = sum(t["runtime_known"] for t in watched)
    runtime_estimated = sum(t["runtime_estimated"] for t in watched)
    return {
        "media_type": scope,
        "totals": {"listed_titles": len(listed), "watched_titles": len(watched),
                   "episode_plays": sum(t["episode_plays"] for t in watched), "distinct_episodes": sum(len(t["episodes"]) for t in watched),
                   "watch_minutes": known_minutes, "watch_days": known_minutes / 1440,
                   "planned_minutes": planned_minutes, "planned_days": planned_minutes / 1440,
                   "mean_score": average, "standard_deviation": math.sqrt(sum((s - average) ** 2 for s in rated) / len(rated)) if rated else None,
                   "rated_titles": len(rated)},
        "scores": score_bins,
        "episode_counts": [grouped(episode_bins[label], label) for label in [*(label for _, label in LENGTH_BINS), "Unknown"]],
        "statuses": statuses, "formats": formats,
        "countries": [{"key": k, "label": k, **v} for k, v in sorted(countries.items(), key=lambda kv: (-kv[1]["share"], kv[0]))],
        "release_years": yearly(watched), "watch_years": yearly(watched, watched=True),
        "genres": genre_groups(watched),
        "actors": actor_groups(listed),
        "studios": studio_groups(watched),
        "coverage": {**coverage, "runtime_known_plays": runtime_known, "runtime_estimated_plays": runtime_estimated,
                     "runtime_missing_plays": sum(t["runtime_missing"] for t in watched),
                     "authoritative_dated_plays": sum(t["authoritative_plays"] for t in watched),
                     "unattributed_date_plays": sum(t["unattributed_plays"] for t in watched),
                     "planned_unknown_titles": sum(t["planned_partial"] for t in planned),
                     "planned_unknown_episodes": sum(t["planned_unknown_episodes"] for t in planned),
                     "planned_known_episodes": sum(t["planned_known_episodes"] for t in planned),
                     "unwatched_listed_titles": sum(not t["plays"] for t in listed),
                     "uncatalogued_titles": sum(t["entity_id"] is None for t in titles)},
    }


def build_overviews(facts, coverage):
    return {scope: aggregate_scope(facts, coverage, scope) for scope in SCOPES}
