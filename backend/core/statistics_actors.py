"""Actor statistics use listed canonical titles and verified person identities."""
from collections import defaultdict
from urllib.parse import urlencode, quote

from sqlalchemy import or_, select

from core.statistics_genres import poster
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity
from models.title_credits import TitleCredits
from core.screen_characters import character_images


async def load_actors(db, facts, *, show_anime=True):
    titles = [t for t in facts if t["listed"]]
    entity_ids = {t["entity_id"] for t in titles if t["entity_id"]}
    credits = list((await db.execute(select(CatalogueCredit, CatalogueEntity).join(
        CatalogueEntity, CatalogueEntity.id == CatalogueCredit.contributor_id).where(
        CatalogueCredit.work_id.in_(entity_ids), CatalogueCredit.role == "actor", CatalogueEntity.kind == "person"))).all())
    by_work = defaultdict(list)
    images = await character_images(db, {c.screen_character_id for c, _ in credits if getattr(c, 'screen_character_id', None)})
    for credit, person in credits:
        character_id = getattr(credit, 'screen_character_id', None)
        candidates = images.get(character_id, [])
        if not show_anime:
            from core.statistics_facts import anime
            candidates = [i for i in candidates if not anime(i['attributes'])]
        current = poster(credit.character_image_url)
        alternatives = list(dict.fromkeys(([current] if current else []) + [i['url'] for i in candidates if i['work_id'] == credit.work_id]
            + [i['url'] for i in candidates if i['work_id'] != credit.work_id]))[:4]
        by_work[credit.work_id].append({"key": f"catalogue:{person.id}", "label": person.name,
            "image": poster(person.image_url), "character": credit.character_label,
            "character_image": current, "character_images": alternatives, "character_id": character_id,
            "provider": credit.provider, "position": credit.position})
    tmdb = {(t["kind"], m.tmdb_id) for t in titles for m in t["media"] if m.tmdb_id}
    cache = {}
    if tmdb:
        rows = await db.scalars(select(TitleCredits).where(or_(*[
            (TitleCredits.media_type == kind) & TitleCredits.tmdb_id.in_({id for k, id in tmdb if k == kind})
            for kind in ("movie", "series")
        ])))
        cache = {(r.media_type, r.tmdb_id): r.cast or [] for r in rows}
    person_ids = {str(p["id"]) for cast in cache.values() for p in cast if p.get("id")}
    aliases = {i.external_id: i.entity_id for i in await db.scalars(select(CatalogueIdentity).where(
        CatalogueIdentity.namespace == "tmdb.person", CatalogueIdentity.external_id.in_(person_ids)))} if person_ids else {}
    for title in titles:
        actors = list(by_work.get(title["entity_id"], []))
        primary = {a["key"] for a in actors if a["provider"] == "tmdb"}
        if primary:
            actors = [a for a in actors if a["provider"] == "tmdb" or a["key"] in primary]
        # Reduced legacy credits are only a fallback; never duplicate or replace
        # the complete canonical actor/role list for a title.
        if not actors:
            for media in title["media"]:
                for p in cache.get((title["kind"], media.tmdb_id), []):
                    if not p.get("id") or not p.get("name"):
                        continue
                    key = f"catalogue:{aliases[str(p['id'])]}" if str(p["id"]) in aliases else f"tmdb:{p['id']}"
                    actors.append({"key": key, "label": p["name"], "image": poster(p.get("profile_path")),
                                   "character": p.get("character"), "character_image": None, "provider": "legacy", "position": None})
        title["actors"] = actors


def actor_groups(listed):
    buckets = defaultdict(dict)
    people = {}
    for title in listed:
        for actor in title.get("actors", []):
            key = actor["key"]
            people.setdefault(key, actor)
            if not people[key].get("image") and actor.get("image"):
                people[key] = {**people[key], "image": actor["image"]}
            buckets[key].setdefault(title["key"], {"title": title, "roles": []})["roles"].append(actor)
    result = []
    for key, works in buckets.items():
        person = people[key]
        titles = [w["title"] for w in works.values()]
        rated = [t for t in titles if t["score"] is not None]
        top = sorted((t for t in titles if t["detail_media_id"] is not None), key=lambda t: (
            -(t["score"] if t["score"] is not None else -1), t["name"].casefold(), t["key"]))[:12]
        top_titles = []
        for title in top:
            roles = works[title["key"]]["roles"]
            # Keep all role labels, but one poster per actor/title. Prefer actual
            # character artwork; an actor portrait never substitutes for it.
            names = sorted({r["character"].strip() for r in roles if r.get("character") and r["character"].strip()})
            local = [r["character_image"] for r in roles if r.get("character_image")]
            alternatives = list(dict.fromkeys(local + [url for r in roles for url in r.get('character_images', [])]))[:4]
            art = alternatives[0] if alternatives else None
            top_titles.append({"key": title["key"], "title": title["name"], "poster": title["poster"],
                "href": f"/title/{title['detail_media_id']}", "score": title["score"], "character": " / ".join(names) or None,
                "character_image": art, "character_images": alternatives,
                "character_ids": sorted({r['character_id'] for r in roles if r.get('character_id')})})
        result.append({"key": key, "label": person["label"], "image": person.get("image"),
            "href": f"/actors/{quote(key, safe='')}?{urlencode({'name': person['label']})}",
            "titles": len(titles), "minutes": sum(t["minutes"] for t in titles),
            "rated_titles": len(rated), "mean_score": sum(t["score"] for t in rated) / len(rated) if rated else None,
            "runtime_missing_plays": sum(t["runtime_missing"] for t in titles), "top_titles": top_titles})
    # Preserve every possible top-thirty sort without shipping an entire cast
    # catalogue to the browser (at most ninety cards across the three metrics).
    selected = set()
    for metric in ("titles", "minutes", "mean_score"):
        ranked = sorted(result, key=lambda r: (-(r[metric] if r[metric] is not None else -1), -r["titles"], r["label"].casefold(), r["key"]))
        selected.update(r["key"] for r in ranked[:30])
    return sorted((r for r in result if r["key"] in selected), key=lambda r: (-r["titles"], r["label"].casefold(), r["key"]))
