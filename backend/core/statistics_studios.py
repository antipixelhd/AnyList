"""Production-company statistics from the canonical watched-title cohort."""
from collections import defaultdict
from urllib.parse import quote

from sqlalchemy import select

from core.countries import country_codes
from core.statistics_genres import poster
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity
from models.title_credits import TitleCredits


def company_metadata(raw):
    return {"name": raw.get("name"), "image": poster(raw.get("logo_path") or raw.get("image_url")),
            "countries": country_codes(raw.get("origin_country") or raw.get("country")),
            "description": raw.get("description") or None}


async def load_studios(db, facts):
    watched = [t for t in facts if t["plays"]]
    ids = {t["entity_id"] for t in watched if t["entity_id"]}
    credits = (await db.execute(select(CatalogueCredit, CatalogueEntity).join(
        CatalogueEntity, CatalogueEntity.id == CatalogueCredit.contributor_id).where(
        CatalogueCredit.work_id.in_(ids), CatalogueCredit.role == "producer", CatalogueEntity.kind == "organization"))).all()
    by_work = defaultdict(list)
    for credit, company in credits:
        by_work[credit.work_id].append({"key": f"catalogue:{company.id}", "label": company.name, "provider": credit.provider})
    # Older imports retain verified TMDB company IDs in local metadata/credits.
    # Names do not establish identity, and secondary providers do not duplicate
    # an authoritative production-company list for the same work.
    native_ids = {m.tmdb_id for t in watched for m in t["media"] if m.tmdb_id}
    cache = {(r.media_type, r.tmdb_id): r.studios or [] for r in await db.scalars(
        select(TitleCredits).where(TitleCredits.tmdb_id.in_(native_ids)))} if native_ids else {}
    company_ids = {str(p["id"]) for t in watched for p in t["data"].get("production_companies") or []
                   if isinstance(p, dict) and p.get("id")}
    company_ids.update(str(p["id"]) for rows in cache.values() for p in rows if isinstance(p, dict) and p.get("id"))
    aliases = {i.external_id: i.entity_id for i in await db.scalars(select(CatalogueIdentity).where(
        CatalogueIdentity.namespace == "tmdb.company", CatalogueIdentity.external_id.in_(company_ids)))} if company_ids else {}
    for title in watched:
        rows = by_work.get(title["entity_id"], [])
        primary = [r for r in rows if r["provider"] == "tmdb"]
        raw = title["data"].get("production_companies") or []
        if not raw:
            raw = [p for m in title["media"] for p in cache.get((title["kind"], m.tmdb_id), [])]
        fallback = [{"key": f"catalogue:{aliases[str(p['id'])]}" if str(p["id"]) in aliases else f"tmdb:{p['id']}",
                     "label": p["name"], "provider": "tmdb"} for p in raw
                    if isinstance(p, dict) and p.get("id") and p.get("name")]
        legacy = [r for r in rows if r["provider"] == "legacy"]
        title["studios"] = primary or fallback or legacy or rows


def studio_groups(watched):
    companies, buckets = {}, defaultdict(dict)
    for title in watched:
        for company in title.get("studios", []):
            companies.setdefault(company["key"], company)
            buckets[company["key"]][title["key"]] = title
    result = []
    for key, works in buckets.items():
        titles = list(works.values())
        rated = [t for t in titles if t["score"] is not None]
        best = sorted((t for t in rated if t["detail_media_id"] is not None), key=lambda t: (
            -t["score"], t["name"].casefold(), t["key"]))[:12]
        result.append({"key": key, "label": companies[key]["label"], "href": f"/studio/{quote(key, safe='')}",
            "titles": len(titles), "minutes": sum(t["minutes"] for t in titles), "rated_titles": len(rated),
            "mean_score": sum(t["score"] for t in rated) / len(rated) if rated else None,
            "runtime_missing_plays": sum(t["runtime_missing"] for t in titles),
            "top_titles": [{"key": t["key"], "title": t["name"], "poster": t["poster"],
                            "href": f"/title/{t['detail_media_id']}", "score": t["score"]} for t in best]})
    selected = set()
    for metric in ("titles", "minutes", "mean_score"):
        ranked = sorted(result, key=lambda r: (-(r[metric] if r[metric] is not None else -1), -r["titles"], r["label"].casefold(), r["key"]))
        selected.update(r["key"] for r in ranked[:18])
    return [r for r in result if r["key"] in selected]


async def studio_detail(db, key):
    namespace, native = key.split(":", 1)
    company = None
    if namespace == "catalogue":
        company = await db.get(CatalogueEntity, int(native))
        if not company or company.kind != "organization":
            return None
        # A person, broadcaster, publisher, etc. cannot be viewed as a studio.
        producer = await db.scalar(select(CatalogueCredit.id).where(
            CatalogueCredit.contributor_id == company.id, CatalogueCredit.role == "producer").limit(1))
        if not producer:
            return None
        native = await db.scalar(select(CatalogueIdentity.external_id).where(
            CatalogueIdentity.entity_id == company.id, CatalogueIdentity.namespace == "tmdb.company"))
    else:
        mapping = await db.scalar(select(CatalogueIdentity).where(
            CatalogueIdentity.namespace == "tmdb.company", CatalogueIdentity.external_id == native))
        if mapping:
            return await studio_detail(db, f"catalogue:{mapping.entity_id}")
    metadata = {}
    if native:
        from models.media import Media
        # These are shared provider metadata, never a user's lists or ratings.
        raw = await db.scalar(select(Media.tmdb_data).where(
            Media.tmdb_data.contains({"production_companies": [{"id": int(native)}]})).order_by(Media.id).limit(1))
        record = next((p for p in (raw or {}).get("production_companies") or [] if str(p.get("id")) == native), None)
        if not record:
            cached = await db.scalar(select(TitleCredits.studios).where(
                TitleCredits.studios.contains([{"id": int(native)}])).limit(1))
            record = next((p for p in cached or [] if str(p.get("id")) == native), None)
        if record:
            metadata = company_metadata(record)
    if not company and not metadata:
        return None
    attrs = company.attributes if company else {}
    return {"key": key, "name": company.name if company else metadata["name"],
            "image": poster(company.image_url) if company and company.image_url else metadata.get("image"),
            "description": company.description if company and company.description else metadata.get("description"),
            "countries": country_codes(attrs.get("origin_country") or attrs.get("country")) or metadata.get("countries", [])}
