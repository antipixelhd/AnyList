"""Non-acting people, counted once per canonical listed title."""
from collections import defaultdict
from urllib.parse import quote

from sqlalchemy import or_, select

from core.statistics_genres import poster
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, CatalogueLegacyLink
from models.media import Media
from models.base import MediaType
from models.title_credits import TitleCredits

ROLE_ORDER = {"Director": 0, "Creator": 1, "Showrunner": 1, "Writer": 2, "Screenplay": 2, "Story": 2, "Teleplay": 2,
              "Executive Producer": 3, "Producer": 3, "Original Music Composer": 4, "Composer": 4,
              "Director of Photography": 5, "Editor": 6, "Production Design": 7}
ACTING = {"actor", "acting", "voice actor", "voice acting", "guest star", "gueststar", "self"}


def role_label(credit):
    return credit.role_label or (credit.role or "contributor").replace("_", " ").title()


def role_order(label):
    return ROLE_ORDER.get(label, 8)


async def load_staff(db, facts):
    titles = [t for t in facts if t["listed"]]
    ids = {t["entity_id"] for t in titles if t["entity_id"]}
    rows = (await db.execute(select(CatalogueCredit, CatalogueEntity).join(
        CatalogueEntity, CatalogueEntity.id == CatalogueCredit.contributor_id).where(
        CatalogueCredit.work_id.in_(ids), CatalogueCredit.role.is_not(None), CatalogueCredit.role != "actor",
        CatalogueEntity.kind == "person"))).all()
    by_work = defaultdict(list)
    for credit, person in rows:
        label = role_label(credit)
        if label.casefold() in ACTING:
            continue
        by_work[credit.work_id].append({"key": f"catalogue:{person.id}", "label": person.name,
                                      "image": poster(person.image_url), "role": label, "provider": credit.provider})
    native = {(t["kind"], m.tmdb_id) for t in titles for m in t["media"] if m.tmdb_id}
    cache = {(r.media_type, r.tmdb_id): r for r in await db.scalars(select(TitleCredits).where(or_(*[
        (TitleCredits.media_type == kind) & TitleCredits.tmdb_id.in_({id for k, id in native if k == kind})
        for kind in ("movie", "series")])))} if native else {}
    fallback = defaultdict(list)
    for title in titles:
        raw = [(p, "Creator") for p in title["data"].get("created_by") or []]
        for media in title["media"]:
            row = cache.get((title["kind"], media.tmdb_id))
            if row:
                raw += [(p, role) for field, role in (("directors", "Director"), ("writers", "Writer")) for p in getattr(row, field) or []]
        fallback[title["key"]] = [(p, role) for p, role in raw if isinstance(p, dict) and p.get("id") and p.get("name")]
    person_ids = {str(p["id"]) for raw in fallback.values() for p, _ in raw}
    aliases = {i.external_id: i.entity_id for i in await db.scalars(select(CatalogueIdentity).where(
        CatalogueIdentity.namespace == "tmdb.person", CatalogueIdentity.external_id.in_(person_ids)))} if person_ids else {}
    for title in titles:
        people = by_work.get(title["entity_id"], [])
        primary = {p["key"] for p in people if p["provider"] == "tmdb"}
        if primary:
            people = [p for p in people if p["provider"] == "tmdb" or p["key"] in primary]
        elif not people:
            people = [{"key": f"catalogue:{aliases[str(p['id'])]}" if str(p["id"]) in aliases else f"tmdb:{p['id']}",
                       "label": p["name"], "image": poster(p.get("profile_path")), "role": role, "provider": "legacy"}
                      for p, role in fallback[title["key"]]]
        # Creators are a distinct authoritative title field, including in old
        # snapshots whose canonical projection predates creator credits.
        people = list(people)
        for p, role in fallback[title["key"]]:
            if role == "Creator":
                key = f"catalogue:{aliases[str(p['id'])]}" if str(p["id"]) in aliases else f"tmdb:{p['id']}"
                people.append({"key": key, "label": p["name"], "image": poster(p.get("profile_path")), "role": role, "provider": "tmdb"})
        title["staff"] = people


def staff_groups(listed):
    people, buckets = {}, defaultdict(dict)
    for title in listed:
        for person in title.get("staff", []):
            key = person["key"]
            if key not in people or (not people[key].get("image") and person.get("image")):
                people[key] = person
            buckets[key].setdefault(title["key"], {"title": title, "roles": set()})["roles"].add(person["role"])
    result = []
    for key, works in buckets.items():
        titles = [w["title"] for w in works.values()]
        rated = [t for t in titles if t["score"] is not None]
        roles = sorted({r for w in works.values() for r in w["roles"]}, key=lambda r: (role_order(r), r))
        best = sorted((t for t in titles if t["detail_media_id"] is not None), key=lambda t: (
            -(t["score"] if t["score"] is not None else -1), t["name"].casefold(), t["key"]))[:12]
        result.append({"key": key, "label": people[key]["label"], "image": people[key].get("image"),
            "href": f"/person/{quote(key, safe='')}", "roles": roles, "prominence": min(map(role_order, roles)),
            "titles": len(titles), "minutes": sum(t["minutes"] for t in titles), "rated_titles": len(rated),
            "mean_score": sum(t["score"] for t in rated) / len(rated) if rated else None,
            "runtime_missing_plays": sum(t["runtime_missing"] for t in titles),
            "top_titles": [{"key": t["key"], "title": t["name"], "poster": t["poster"], "href": f"/title/{t['detail_media_id']}",
                            "score": t["score"], "roles": sorted(works[t["key"]]["roles"], key=lambda r: (role_order(r), r))} for t in best]})
    selected = set()
    for metric in ("titles", "minutes", "mean_score"):
        ranked = sorted(result, key=lambda r: (-(r[metric] if r[metric] is not None else -1), -r["titles"], r["prominence"], r["label"].casefold(), r["key"]))
        selected.update(r["key"] for r in ranked[:30])
    return [r for r in result if r["key"] in selected]


async def person_detail(db, key, *, show_anime):
    namespace, native = key.split(":", 1)
    person = await db.get(CatalogueEntity, int(native)) if namespace == "catalogue" else None
    if namespace == "tmdb":
        mapping = await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace == "tmdb.person", CatalogueIdentity.external_id == native))
        if mapping:
            return await person_detail(db, f"catalogue:{mapping.entity_id}", show_anime=show_anime)
    if namespace == "catalogue" and (not person or person.kind != "person"):
        return None
    if person:
        native = await db.scalar(select(CatalogueIdentity.external_id).where(CatalogueIdentity.entity_id == person.id, CatalogueIdentity.namespace == "tmdb.person"))
    legacy = await db.scalar(select(Media).where(Media.media_type == MediaType.person, Media.tmdb_id == int(native)).order_by(Media.id).limit(1)) if native and native.isdecimal() else None
    attrs = dict(legacy.tmdb_data or {}) if legacy else {}
    if person:
        attrs.update({k: v for k, v in (person.attributes or {}).items() if v is not None})
    works, jobs = {}, set()
    if person:
        rows = (await db.execute(select(CatalogueCredit, CatalogueEntity).join(
            CatalogueEntity, CatalogueEntity.id == CatalogueCredit.work_id).where(
            CatalogueCredit.contributor_id == person.id, CatalogueEntity.kind.in_(["movie", "series"])).order_by(CatalogueEntity.id))).all()
        entity_ids = {w.id for _, w in rows}
        links = (await db.execute(select(CatalogueLegacyLink, Media).join(Media, Media.id == CatalogueLegacyLink.media_id).where(
            CatalogueLegacyLink.entity_id.in_(entity_ids), Media.media_type.in_([MediaType.movie, MediaType.series])).order_by(Media.id))).all()
        media_by_work = {}
        for link, media in links:
            media_by_work.setdefault(link.entity_id, media)
        identities = list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id.in_(entity_ids),
            CatalogueIdentity.namespace.in_(["tmdb.movie", "tmdb.series", "tvdb.movie", "tvdb.series"]))))
        terms = [(Media.media_type == MediaType(i.namespace.split('.')[1])) &
                 (getattr(Media, i.namespace.split('.')[0] + '_id') == int(i.external_id)) for i in identities if i.external_id.isdecimal() and int(i.external_id) <= 2147483647]
        mapped = list(await db.scalars(select(Media).where(or_(*terms)).order_by(Media.id))) if terms else []
        for identity in identities:
            provider, kind = identity.namespace.split('.')
            for media in mapped:
                if media.media_type.value == kind and str(getattr(media, provider + '_id')) == identity.external_id:
                    media_by_work.setdefault(identity.entity_id, media)
        from core.statistics_facts import anime
        for credit, work in rows:
            media = media_by_work.get(work.id)
            if not media:
                continue
            data = {**(media.tmdb_data or {}), **(work.attributes or {})}
            if not show_anime and anime(data):
                continue
            item = works.setdefault(f"catalogue:{work.id}", {"title": work.name, "poster": poster(work.image_url or media.poster_path),
                "href": f"/title/{media.id}", "release_date": data.get("release_date") or media.release_date, "roles": set()})
            label = role_label(credit)
            item["roles"].add(label); jobs.add(label)
    if native and native.isdecimal():
        cached = list(await db.scalars(select(TitleCredits).where(or_(*[
            getattr(TitleCredits, field).contains([{"id": int(native)}]) for field in ("cast", "directors", "writers")]))))
        terms = [(Media.media_type == MediaType(kind)) & Media.tmdb_id.in_({r.tmdb_id for r in cached if r.media_type == kind}) for kind in ('movie', 'series')]
        media_rows = await db.scalars(select(Media).where(or_(*terms)).order_by(Media.id)) if cached else []
        media_by_native = {}
        for media in media_rows:
            media_by_native.setdefault((media.media_type.value, media.tmdb_id), media)
        for row in cached:
            matches = [p for field in ("cast", "directors", "writers") for p in getattr(row, field) or [] if str(p.get("id")) == native]
            if not matches:
                continue
            if not person and not legacy:
                attrs.setdefault("name", matches[0].get("name")); attrs.setdefault("profile_path", matches[0].get("profile_path"))
            media = media_by_native.get((row.media_type, row.tmdb_id))
            if not media or any(w["href"] == f"/title/{media.id}" for w in works.values()):
                continue
            from core.statistics_facts import anime
            if not show_anime and anime(media.tmdb_data or {}):
                continue
            labels = {role for field, role in (("cast", "Actor"), ("directors", "Director"), ("writers", "Writer"))
                      if any(str(p.get("id")) == native for p in getattr(row, field) or [])}
            jobs.update(labels)
            works[f"{row.media_type}:{row.tmdb_id}"] = {"title": media.title, "poster": poster(media.poster_path), "href": f"/title/{media.id}",
                                                       "release_date": media.release_date, "roles": labels}
        # Older title projections retain creators on the title itself.
        creators = await db.scalars(select(Media).where(Media.media_type.in_([MediaType.movie, MediaType.series]),
            Media.tmdb_data['created_by'].contains([{'id': int(native)}])).order_by(Media.id))
        for media in creators:
            from core.statistics_facts import anime
            if not show_anime and anime(media.tmdb_data or {}):
                continue
            match = next((p for p in media.tmdb_data.get('created_by', []) if isinstance(p, dict) and str(p.get('id')) == native), None)
            if not match:
                continue
            attrs.setdefault('name', match.get('name'))
            attrs.setdefault('profile_path', match.get('profile_path'))
            jobs.add('Creator')
            existing = next((w for w in works.values() if w['href'] == f'/title/{media.id}'), None)
            if existing:
                existing['roles'].add('Creator')
            else:
                works[f'{media.media_type.value}:{media.tmdb_id or media.id}'] = {
                    'title': media.title, 'poster': poster(media.poster_path), 'href': f'/title/{media.id}',
                    'release_date': media.release_date, 'roles': {'Creator'}}
    if not person and not legacy and not attrs.get("name"):
        return None
    ordered = sorted(works.values(), key=lambda w: (w["release_date"] or "", w["title"]), reverse=True)[:36]
    return {"key": key, "name": person.name if person else legacy.title if legacy else attrs["name"],
        "image": poster(person.image_url if person and person.image_url else legacy.poster_path if legacy and legacy.poster_path else attrs.get("profile_path")),
        "biography": person.description if person and person.description else legacy.overview if legacy and legacy.overview else attrs.get("biography"),
        "birthday": attrs.get("birthday"), "deathday": attrs.get("deathday"), "place_of_birth": attrs.get("place_of_birth"),
        "roles": sorted(jobs, key=lambda r: (role_order(r), r)),
        "works": [{**w, "roles": sorted(w["roles"], key=lambda r: (role_order(r), r))} for w in ordered]}
