"""Complete known credits plus cached provider discovery, without importing on reads."""

import asyncio
import json
import re
from collections import defaultdict
from urllib.parse import urlsplit

from sqlalchemy import or_, select

from core import settings_store, tmdb, tracking_projection
from core.browse import GENRES, anime_visible
from core.countries import country_codes
from core.statistics_genres import poster
from core.statistics_staff import ACTING, role_label, role_order
from core.tracking_editor import attach_editor_context
from models import Media
from models.base import MediaType
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, CatalogueLegacyLink
from models.title_credits import TitleCredits
from models.tracking import TrackedEntry

PAGE_SIZE = 24


def positive_id(value):
    return int(value) if str(value).isdecimal() and 0 < int(value) <= 2147483647 else None


def public_url(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        return value if url.scheme in ("http", "https") and url.hostname and not url.username and not url.password else None
    except ValueError:
        return None


def is_actor(credit):
    return credit.role == "actor" or role_label(credit).casefold() in ACTING


def accepted_role(kind, acting):
    return kind == "studio" or acting == (kind == "actor")


def merge_works(rows):
    """Keep movie/series IDs distinct and retain every job and character."""
    merged = {}
    aliases = {}
    for row in rows:
        external = (row["type"], row.get("tmdb_id")) if row.get("tmdb_id") else None
        key = aliases.get(external, row["key"]) if external else row["key"]
        item = merged.setdefault(key, {**row, "roles": [], "characters": []})
        if external:
            aliases[external] = key
        for field in ("roles", "characters"):
            item[field] = sorted(set(item[field]) | set(row.get(field) or []), key=lambda label: (role_order(label), label))
        for field in ("id", "poster", "backdrop", "release_date", "year"):
            if not item.get(field) and row.get(field):
                item[field] = row[field]
    return list(merged.values())


def remote_work(raw, kind, *, acting=None):
    media_type = kind or {"movie": "movie", "tv": "series"}.get(raw.get("media_type"))
    native = positive_id(raw.get("id"))
    title = raw.get("title") or raw.get("name")
    if media_type not in ("movie", "series") or not native or not title or raw.get("adult"):
        return None
    date = raw.get("release_date") or raw.get("first_air_date")
    return {"key": f"{media_type}:{native}", "id": None, "tmdb_id": native, "type": media_type,
            "title": title, "poster": poster(raw.get("poster_path")), "backdrop": poster(raw.get("backdrop_path")),
            "release_date": date, "year": (date or "")[:4], "roles": ["Actor" if acting else raw.get("job") or "Production"] if acting is not None else [],
            "characters": [raw["character"]] if acting and raw.get("character") else [],
            "_metadata": {**raw, "genres": [GENRES.get(id, "") for id in raw.get("genre_ids") or []]}}


async def known_works(db, entity, native, kind, viewer):
    rows = []
    media_candidates = defaultdict(list)
    work_identities = defaultdict(dict)
    own = set(await db.scalars(select(TrackedEntry.media_id).where(TrackedEntry.user_id == viewer.id))) if viewer else set()
    if entity:
        credits = list((await db.execute(select(CatalogueCredit, CatalogueEntity).join(
            CatalogueEntity, CatalogueEntity.id == CatalogueCredit.work_id).where(
            CatalogueCredit.contributor_id == entity.id, CatalogueEntity.kind.in_(("movie", "series")),
        ).order_by(CatalogueEntity.id, CatalogueCredit.id))).all())
        credits = [(c, w) for c, w in credits if (c.role == "producer" if kind == "studio" else accepted_role(kind, is_actor(c)))]
        ids = {w.id for _, w in credits}
        identities = list(await db.scalars(select(CatalogueIdentity).where(
            CatalogueIdentity.entity_id.in_(ids), CatalogueIdentity.namespace.in_(("tmdb.movie", "tmdb.series", "tvdb.movie", "tvdb.series")),
        ))) if ids else []
        terms = []
        for identity in identities:
            provider, media_type = identity.namespace.split(".")
            if native_id := positive_id(identity.external_id):
                work_identities[identity.entity_id][provider] = native_id
                terms.append((Media.media_type == MediaType(media_type)) & (getattr(Media, provider + "_id") == native_id))
        mapped = list(await db.scalars(select(Media).where(or_(*terms)).order_by(Media.id))) if terms else []
        for identity in identities:
            provider, media_type = identity.namespace.split(".")
            media_candidates[identity.entity_id].extend(m for m in mapped if m.media_type.value == media_type and str(getattr(m, provider + "_id")) == identity.external_id)
        links = (await db.execute(select(CatalogueLegacyLink.entity_id, Media).join(Media, Media.id == CatalogueLegacyLink.media_id).where(
            CatalogueLegacyLink.entity_id.in_(ids), Media.media_type.in_((MediaType.movie, MediaType.series)),
        ))).all() if ids else []
        for work_id, media in links:
            media_candidates[work_id].append(media)
        for credit, work in credits:
            # Prefer the viewer's actual tracking row over an older metadata alias.
            candidates = sorted(media_candidates[work.id], key=lambda m: (m.id not in own, m.id))
            media = candidates[0] if candidates else None
            data = {**(media.tmdb_data or {} if media else {}), **(work.attributes or {})}
            if media and media.adult:
                data["adult"] = True
            ids = work_identities[work.id]
            rows.append({"key": f"catalogue:{work.id}", "id": media.id if media else None,
                "tmdb_id": ids.get("tmdb") or (media.tmdb_id if media else None), "type": work.kind,
                "title": work.name, "poster": poster(work.image_url or (media.poster_path if media else None)),
                "backdrop": poster(media.backdrop_path if media else data.get("backdrop_path")),
                "release_date": data.get("release_date") or data.get("first_air_date") or (media.release_date if media else None),
                "year": (data.get("release_date") or data.get("first_air_date") or (media.release_date if media else "") or "")[:4],
                "roles": [role_label(credit)], "characters": [credit.character_label] if is_actor(credit) and credit.character_label else [],
                "href": f"https://www.thetvdb.com/dereferrer/{'movie' if work.kind == 'movie' else 'series'}/{ids['tvdb']}" if not media and not ids.get("tmdb") and ids.get("tvdb") else None,
                "_metadata": data})
    if native:
        fields = ("studios",) if kind == "studio" else ("cast",) if kind == "actor" else ("directors", "writers")
        cached = list(await db.scalars(select(TitleCredits).where(or_(*[
            getattr(TitleCredits, field).contains([{"id": native}]) for field in fields
        ]))))
        terms = [(Media.media_type == MediaType(row.media_type)) & (Media.tmdb_id == row.tmdb_id) for row in cached if row.media_type in ("movie", "series")]
        metadata_field = "production_companies" if kind == "studio" else "created_by"
        if kind != "actor":
            terms.append(Media.tmdb_data[metadata_field].contains([{"id": native}]))
        local = list(await db.scalars(select(Media).where(Media.media_type.in_((MediaType.movie, MediaType.series)), or_(*terms)))) if terms else []
        for media in sorted(local, key=lambda m: (m.id not in own, m.id)):
            matches = []
            for cached_row in cached:
                if (cached_row.media_type, cached_row.tmdb_id) != (media.media_type.value, media.tmdb_id):
                    continue
                for field in fields:
                    matches += [(p, {"cast": "Actor", "directors": "Director", "writers": "Writer", "studios": "Production"}[field]) for p in getattr(cached_row, field) or [] if isinstance(p, dict) and positive_id(p.get("id")) == native]
            matches += [(p, "Production" if kind == "studio" else "Creator") for p in (media.tmdb_data or {}).get(metadata_field) or [] if kind != "actor" and isinstance(p, dict) and positive_id(p.get("id")) == native]
            item = tracking_projection.media_data(media)
            rows.append({**item, "key": f"{media.media_type.value}:{media.tmdb_id or 'local-' + str(media.id)}",
                "roles": [role for _, role in matches], "characters": [p["character"] for p, role in matches if role == "Actor" and p.get("character")], "_metadata": {**(media.tmdb_data or {}), "adult": media.adult or (media.tmdb_data or {}).get("adult", False)}})
    return merge_works(rows)


async def studio_timeline(db, viewer, known, native, provider_key, *, media_type, list_scope, show_anime, cursor, scope):
    positions = [0, 1, 0, 1, 0]
    if cursor:
        parsed = json.loads(cursor)
        if not isinstance(parsed, dict) or parsed.get("scope") != scope:
            raise ValueError("Invalid timeline cursor")
        positions = parsed.get("positions")
        if (not isinstance(positions, list) or len(positions) != 5
                or any(type(i) is not int or not 0 <= i <= 1_000_000 for i in positions)
                or any(not 1 <= positions[i] <= 501 for i in (1, 3))
                or any(positions[i] > 20 for i in (2, 4))):
            raise ValueError("Invalid timeline cursor")
    # Three sorted streams: complete known works, movie discovery and series
    # discovery. Retain provider offsets so later pages cannot jump backwards.
    known_external = {(w["type"], w.get("tmdb_id")) for w in known if w.get("tmdb_id")}
    local = [w for w in known if (media_type == "all" or w["type"] == media_type)
             and (list_scope != "out" or not w.get("list_status"))]
    local.sort(key=lambda w: (w.get("release_date") or "", w["key"]), reverse=True)
    buffers, requests = {}, 0
    types = [("movie", 1, 2), ("series", 3, 4)]
    output = []
    while len(output) < PAGE_SIZE:
        heads = [(local[positions[0]], 0)] if positions[0] < len(local) else []
        unresolved = False
        for media, page_slot, index_slot in types:
            if media_type not in ("all", media):
                positions[page_slot] = 501
            while positions[page_slot] <= 500:
                provider_page = positions[page_slot]
                buffer_key = (media, provider_page)
                if buffer_key not in buffers:
                    if requests >= 8:
                        unresolved = True
                        break
                    requests += 1
                    response = await (tmdb.discover_movies if media == "movie" else tmdb.discover_shows)(
                        page=provider_page, with_companies=native, vote_count_min=0,
                        sort_by="primary_release_date.desc" if media == "movie" else "first_air_date.desc", api_key=provider_key)
                    raw_rows = response.get("results") or []
                    candidates = [remote_work(r, media) for r in raw_rows]
                    await attach_editor_context(db, viewer, [r for r in candidates if r])
                    candidates = [r if r and (r["type"], r["tmdb_id"]) not in known_external
                                  and anime_visible(r["_metadata"], show_anime)
                                  and (list_scope != "out" or not r.get("list_status")) else None for r in candidates]
                    buffers[buffer_key] = (candidates, provider_page < min(response.get("total_pages") or 1, 500))
                candidates, more = buffers[buffer_key]
                while positions[index_slot] < len(candidates) and candidates[positions[index_slot]] is None:
                    positions[index_slot] += 1
                if positions[index_slot] < len(candidates):
                    heads.append((candidates[positions[index_slot]], index_slot))
                    break
                positions[page_slot] = provider_page + 1 if more else 501
                positions[index_slot] = 0
        if unresolved or not heads:
            break
        item, position_slot = max(heads, key=lambda pair: (pair[0].get("release_date") or "", pair[0]["key"]))
        output.append(item)
        positions[position_slot] += 1
    for media, page_slot, index_slot in types:
        buffer = buffers.get((media, positions[page_slot]))
        if buffer:
            candidates, has_next = buffer
            while positions[index_slot] < len(candidates) and candidates[positions[index_slot]] is None:
                positions[index_slot] += 1
            if positions[index_slot] >= len(candidates):
                positions[page_slot] = positions[page_slot] + 1 if has_next else 501
                positions[index_slot] = 0
    more = positions[0] < len(local) or any(positions[i] <= 500 for i in (1, 3))
    return output, more, json.dumps({"scope": scope, "positions": positions}, separators=(",", ":")) if more else None


async def contributor_page(db, viewer, *, kind, key, media_type="all", list_scope="all", status="", page=1, cursor=None, source=""):
    namespace, raw_id = key.split(":")
    native = positive_id(raw_id) if namespace == "tmdb" else None
    expected_kind, provider_kind = ("organization", "company") if kind == "studio" else ("person", "person")
    entity = await db.get(CatalogueEntity, int(raw_id)) if namespace == "catalogue" else await db.scalar(select(CatalogueEntity).join(
        CatalogueIdentity, CatalogueIdentity.entity_id == CatalogueEntity.id).where(
        CatalogueIdentity.namespace == f"tmdb.{provider_kind}", CatalogueIdentity.external_id == raw_id))
    if namespace == "catalogue" and (entity is None or entity.kind != expected_kind):
        return None
    if entity and entity.kind != expected_kind:
        return None
    identities = list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id == entity.id))) if entity else []
    if entity:
        native = next((positive_id(i.external_id) for i in identities if i.namespace == f"tmdb.{provider_kind}"), None)
        if kind == "studio" and not await db.scalar(select(CatalogueCredit.id).where(CatalogueCredit.contributor_id == entity.id, CatalogueCredit.role == "producer").limit(1)):
            return None
    local = await known_works(db, entity, native, kind, viewer)
    attrs = dict(entity.attributes or {}) if entity else {}
    legacy = await db.scalar(select(Media).where(Media.media_type == MediaType.person, Media.tmdb_id == native).order_by(Media.id).limit(1)) if native and kind != "studio" else None
    if legacy:
        attrs = {**(legacy.tmdb_data or {}), **attrs}
    profile = {}
    remote = []
    notice = None
    provider_key = await settings_store.get_user_tmdb_key(db, viewer.id if viewer else -1)
    if native and provider_key:
        try:
            async with asyncio.timeout(12):
                profile = await (tmdb.get_company(native, api_key=provider_key) if kind == "studio" else tmdb.get_person(native, api_key=provider_key))
                # Reject an unexpected provider response rather than borrowing another person's data.
                if positive_id(profile.get("id")) != native:
                    raise ValueError("Provider identity mismatch")
                if kind != "studio":
                    field = "cast" if kind == "actor" else "crew"
                    remote = [item for raw in (profile.get("combined_credits") or {}).get(field) or []
                              if not (kind == "staff" and str(raw.get("job", "")).casefold() in ACTING)
                              and (item := remote_work(raw, None, acting=kind == "actor"))]
        except Exception:
            if profile and positive_id(profile.get("id")) != native:
                profile = {}
            notice = "Online metadata is temporarily unavailable. Showing saved information."
    if not entity and not legacy and not profile.get("name") and not local:
        return None
    # Empty cached fields must not hide a real provider value. Explicitly
    # protected empty fields are intentional edits and remain authoritative.
    protected = set(entity.protected_fields or []) if entity else set()
    attrs = {**profile, **{field:value for field,value in attrs.items()
        if value not in (None, '', [], {}) or 'attributes.' + field in protected}}
    name = entity.name if entity else legacy.title if legacy else profile.get("name")
    # Cached company records and people credits supply names even without API keys.
    if not entity and not legacy and not profile.get("name") and native:
        fields = ("studios",) if kind == "studio" else ("cast", "directors", "writers")
        cache = await db.scalar(select(TitleCredits).where(or_(*[getattr(TitleCredits, f).contains([{"id": native}]) for f in fields])).limit(1))
        raw = next((p for f in fields for p in getattr(cache, f) or [] if isinstance(p, dict) and positive_id(p.get("id")) == native), {}) if cache else {}
        if not raw and local:
            media = await db.get(Media, local[0]["id"])
            raw = next((p for p in (media.tmdb_data or {}).get("production_companies" if kind == "studio" else "created_by") or [] if isinstance(p, dict) and positive_id(p.get("id")) == native), {}) if media else {}
        name = raw.get("name") or name
        attrs = {**raw, **attrs}
    if not name:
        return None
    if kind == "studio" and cursor and source != "local" and not profile:
        raise RuntimeError("Unable to load more studio works")
    # The caller supplies the current instance preference, including when metadata is offline.
    from models import GlobalSettings
    settings = await db.get(GlobalSettings, 1)
    show_anime = True if settings is None else settings.show_anime
    local = [r for r in local if not r.get("_metadata", {}).get("adult") and anime_visible(r.get("_metadata", {}), show_anime)]
    remote = [r for r in remote if not r.get("_metadata", {}).get("adult") and anime_visible(r.get("_metadata", {}), show_anime)]
    all_known = merge_works(local + (remote if kind != "studio" else []))
    for row in all_known + remote:
        row.update(entry=None, list_status=None, score=None, rating_mode="manual")
    await attach_editor_context(db, viewer, all_known)
    list_count = sum(bool(r.get("list_status")) for r in all_known) if viewer else None
    roles = sorted({role for row in all_known for role in row["roles"]}, key=lambda role: (role_order(role), role))
    filtered = [r for r in all_known if (media_type == "all" or r["type"] == media_type)
                and (list_scope == "all" or bool(r.get("list_status")) == (list_scope == "in"))
                and (not status or r.get("list_status") == status)]
    filtered.sort(key=lambda r: (r.get("release_date") or "", r["title"], r["key"]), reverse=True)
    start = (page - 1) * PAGE_SIZE
    works = filtered[start:start + PAGE_SIZE]
    has_more = start + PAGE_SIZE < len(filtered)
    total = len(filtered)
    next_cursor = None
    result_source = "local"
    if kind == "studio" and native and provider_key and profile and source != "local" and list_scope != "in" and not status:
        try:
            async with asyncio.timeout(12):
                works, has_more, next_cursor = await studio_timeline(db, viewer, all_known, native, provider_key,
                    media_type=media_type, list_scope=list_scope, show_anime=show_anime, cursor=cursor,
                    scope=f"{key}:{media_type}:{list_scope}")
            result_source = "provider"
            total = None  # Local and provider pages overlap; never invent a combined total.
        except (ValueError, json.JSONDecodeError):
            raise ValueError("Invalid timeline cursor") from None
        except Exception:
            if cursor:
                raise RuntimeError("Unable to load more studio works") from None
            notice = "Online metadata is temporarily unavailable. Showing saved information."
    links = []
    if native:
        links.append({"label": "TMDB", "href": f"https://www.themoviedb.org/{'company' if kind == 'studio' else 'person'}/{native}"})
    imdb_id = attrs.get("imdb_id")
    if isinstance(imdb_id, str) and re.fullmatch(r"nm[0-9]+", imdb_id):
        links.append({"label": "IMDb", "href": f"https://www.imdb.com/name/{imdb_id}/"})
    if homepage := public_url(attrs.get("homepage")):
        links.append({"label": "Official website", "href": homepage})
    description = entity.description if entity and (entity.description or 'description' in protected) else legacy.overview if legacy and legacy.overview else attrs.get("biography") or attrs.get("description")
    profile_image = poster(entity.image_url if entity and (entity.image_url or 'image_url' in protected) else legacy.poster_path if legacy and legacy.poster_path else attrs.get("profile_path") or attrs.get("logo_path") or attrs.get("image_url"))
    banner = public_url(attrs.get('studio_banner')) if kind == 'studio' else None
    if kind == 'studio' and not banner and 'attributes.studio_banner' not in protected:
        banner = profile_image
    return {"kind": kind, "key": f"catalogue:{entity.id}" if entity else key, "name": name,
        "banner": banner,
        "image": profile_image,
        "description": description, "birthday": attrs.get("birthday"), "deathday": attrs.get("deathday"),
        "place_of_birth": attrs.get("place_of_birth"), "department": attrs.get("known_for_department"),
        "aliases": [n for n in attrs.get("also_known_as") or [] if isinstance(n, str) and n != name],
        "countries": country_codes(attrs.get("origin_country") or attrs.get("country")), "headquarters": attrs.get("headquarters"),
        "links": links, "roles": roles, "works": works, "known_works": len(all_known), "list_count": list_count,
        "total": total, "page": page, "has_more": has_more, "notice": notice,
        "next_cursor": next_cursor, "source": result_source}
