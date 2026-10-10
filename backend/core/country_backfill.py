"""Bounded country recovery for shared metadata, independent of page requests."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from core import catalogue, catalogue_normalize, openlibrary, tmdb, tvdb
from core.countries import country_codes, publication_countries
from core.catalogue_providers import ProviderError
from models import Media, Show
from models.base import MediaType
from models.catalogue import BookEdition, CatalogueEntity, CatalogueIdentity, MetadataSnapshot

BATCH_SIZE = 100
RETRY_AFTER = timedelta(days=7)
VERSION = 1
COUNTRY_FIELDS = {"countries", "country_basis", "origin_countries", "origin_country",
                  "production_countries", "publication_countries", "publish_country", "publish_places",
                  "developer_countries", "publisher_countries"}


def due(data, now):
    try:
        value = datetime.fromisoformat((data or {}).get("country_backfill_attempted_at", ""))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return now - value >= RETRY_AFTER
    except (ValueError, TypeError):
        return True


def movie_countries(data):
    origin = country_codes(data.get("origin_countries") or data.get("origin_country"))
    production = country_codes([v.get("iso_3166_1") if isinstance(v, dict) else v
                                for v in data.get("production_countries") or []])
    return origin or production


async def backfill_legacy_countries(db, api_key=None, *, tvdb_api_key=None, limit=BATCH_SIZE, force=False):
    """Fill missing origin countries; retain production fallback and other fields."""
    now = datetime.now(timezone.utc)
    cutoff = (now - RETRY_AFTER).isoformat()
    result = {"examined": 0, "recovered": 0, "unresolved": 0, "failed": 0}
    responses = {}
    for model in (Media, Show):
        attempted = model.tmdb_data["country_backfill_attempted_at"].astext
        query = select(model).where(
            or_(model.tmdb_data["origin_country"].astext.is_(None),
                model.tmdb_data["origin_country"].astext == "[]"),
            True if force else or_(attempted.is_(None), attempted <= cutoff),
        )
        if model is Media:
            query = query.where(Media.media_type.in_([MediaType.movie, MediaType.series]))
        # Reserve part of every sweep for shows, even with a large movie backlog.
        remaining = limit - result["examined"]
        batch = min(remaining, max(1, limit // 2)) if model is Media else remaining
        rows = await db.scalars(query.order_by(attempted.asc().nullsfirst(), model.id).limit(batch))
        for row in rows:
            if not force and not due(row.tmdb_data, now):
                continue
            data = dict(row.tmdb_data or {})
            if country_codes(data.get("origin_country")):
                continue
            result["examined"] += 1
            kind = "series" if model is Show else row.media_type.value
            failed = False
            try:
                response = {}
                if api_key and row.tmdb_id:
                    key = (kind, row.tmdb_id)
                    if key not in responses:
                        responses[key] = {}
                        fetch = tmdb.get_movie if kind == "movie" else tmdb.get_show
                        try:
                            responses[key] = await fetch(row.tmdb_id, api_key=api_key, cache_ttl=None)
                        except Exception:
                            failed = True  # TVDB can still recover the series.
                    raw = responses[key]
                    if raw.get("id") == row.tmdb_id:
                        response = {k: raw.get(k) for k in ("origin_country", "production_countries")}
                if not country_codes(response.get("origin_country")) and kind == "series" and tvdb_api_key and row.tvdb_id:
                    key = ("tvdb", row.tvdb_id)
                    if key not in responses:
                        responses[key] = {}
                        responses[key] = await tvdb.get_series(row.tvdb_id, tvdb_api_key, cache_ttl=None)
                    raw = responses[key]
                    if raw.get("id") == row.tvdb_id:
                        origin = country_codes(raw.get("originalCountry"))
                        if origin:
                            response["origin_country"] = origin
                data.update({k: v for k, v in response.items() if v})
            except Exception:
                failed = True  # Provider exception strings can contain secrets.
            result["failed"] += int(failed)
            data["country_backfill_attempted_at"] = now.isoformat()
            row.tmdb_data = data
            result["recovered" if movie_countries(data) else "unresolved"] += 1
        if result["examined"] >= limit:
            break
    await db.commit()
    return result


async def apply_document_countries(db, doc, source):
    """Only update existing, exactly identified records; never bind new identities."""
    values = [doc["work"], *(c["contributor"] for c in doc["credits"]),
              *(e["entity"] for e in doc["editions"])]
    for value in values:
        matches = {mapping.entity_id for x in value["identities"]
                   if (mapping := await catalogue.resolve_identity(db, x["namespace"], x["external_id"]))}
        if len(matches) > 1:
            raise catalogue.IdentityConflict("Country metadata identities disagree")
        if matches:
            row = await db.get(CatalogueEntity, matches.pop())
            if row.kind != value["kind"]:
                raise catalogue.IdentityConflict("Country metadata kind disagrees")
            attrs = {k: v for k, v in value["attributes"].items() if k in COUNTRY_FIELDS}
            catalogue.merge_fields(row, {"attributes": attrs}, source)


async def backfill_catalogue_countries(db, providers, *, limit=BATCH_SIZE, force=False):
    """Reproject cached metadata first; fetch missing game/edition countries.

    ISBN fallback identifies an edition, never an author's nationality or a
    work's setting. Provider lanes and budgets still govern network requests.
    """
    now = datetime.now(timezone.utc)
    attempted = CatalogueEntity.attributes["country_backfill_attempted_at"].astext
    rows = list(await db.scalars(select(CatalogueEntity).where(
        CatalogueEntity.kind.in_(["movie", "series", "game", "book", "edition"]),
        True if force else or_(attempted.is_(None), attempted <= (now - RETRY_AFTER).isoformat()),
        True if force else or_(CatalogueEntity.attributes["country_metadata_version"].astext.is_(None),
                              CatalogueEntity.attributes["country_metadata_version"].astext != str(VERSION),
                              CatalogueEntity.attributes["countries"].astext.is_(None),
                              CatalogueEntity.attributes["countries"].astext == "[]"),
    ).order_by(attempted.asc().nullsfirst(), CatalogueEntity.id).limit(limit)))
    result = {"examined": 0, "recovered": 0, "unresolved": 0, "failed": 0}
    for row in rows:
        if not force and not due(row.attributes, now):
            continue
        result["examined"] += 1
        try:
            async with db.begin_nested():
                snapshots = list(await db.scalars(select(MetadataSnapshot).where(MetadataSnapshot.entity_id == row.id,
                                                                                 MetadataSnapshot.payload.isnot(None))))
                for snapshot in snapshots:
                    raw = snapshot.payload
                    if snapshot.provider == "tmdb":
                        doc = catalogue_normalize.normalize_tmdb(raw, row.kind)
                    elif snapshot.provider == "tvdb":
                        doc = catalogue_normalize.normalize_tvdb(raw)
                    elif snapshot.provider == "igdb":
                        doc = catalogue_normalize.normalize_igdb(raw)
                    elif snapshot.provider == "openlibrary":
                        doc = openlibrary.normalize(raw)
                    else:
                        continue
                    await apply_document_countries(db, doc, snapshot.provider)
                ids = list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id == row.id)))
                attrs = dict(row.attributes or {})
                if row.kind in ("movie", "series"):
                    origin = country_codes(attrs.get("origin_countries") or attrs.get("origin_country"))
                    if not origin:
                        for namespace in (f"tmdb.{row.kind}", f"tvdb.{row.kind}"):
                            mapping = next((x for x in ids if x.namespace == namespace), None)
                            if mapping:
                                source = namespace.split(".")[0]
                                try:
                                    doc, _ = await providers.detail(source, row.kind, mapping.external_id)
                                except Exception:
                                    if source == "tmdb" and row.kind == "series":
                                        continue
                                    raise
                                await apply_document_countries(db, doc, source)
                                if movie_countries(row.attributes):
                                    break
                    countries = movie_countries(row.attributes)
                    if countries:
                        catalogue.merge_fields(row, {"attributes": {"countries": countries,
                            "country_basis": "origin" if country_codes(row.attributes.get("origin_countries") or row.attributes.get("origin_country")) else "production"}},
                            "tmdb" if any(x.namespace.startswith("tmdb.") for x in ids) else "tvdb")
                elif row.kind == "game" and not attrs.get("countries"):
                    mapping = next((x for x in ids if x.namespace == "igdb.game"), None)
                    if mapping:
                        providers.validate("igdb", "game")
                        # A country backfill needs companies only, not character/edition fan-outs.
                        raw = await providers.igdb("games", "fields id,name,involved_companies.developer,involved_companies.publisher,involved_companies.company.id,involved_companies.company.name,involved_companies.company.country; "
                                                   f"where id = {int(mapping.external_id)}; limit 1;")
                        if raw and str(raw[0].get("id")) == mapping.external_id:
                            await apply_document_countries(db, catalogue_normalize.normalize_igdb(raw[0]), "igdb")
                elif row.kind == "edition" and not attrs.get("publication_countries"):
                    countries = publication_countries(attrs)
                    if countries:
                        catalogue.merge_fields(row, {"attributes": {"publication_countries": countries, "countries": countries,
                                                                  "country_basis": "publication"}}, "openlibrary")
                    else:
                        mapping = next((x for x in ids if x.namespace == "openlibrary.edition"), None)
                        isbn = next((x for x in ids if x.namespace in ("isbn.13", "isbn.10")), None)
                        external_id = mapping.external_id if mapping else await providers.openlibrary_isbn(isbn.external_id) if isbn else None
                        if external_id:
                            raw = await openlibrary.get(providers.http, f"/books/{external_id}.json")
                            if openlibrary.olid(raw.get("key"), "M") != external_id:
                                raise ProviderError("identity_conflict")
                            if isbn and isbn.external_id not in {
                                catalogue_normalize.isbn(value, length) for length in (10, 13)
                                for value in raw.get(f"isbn_{length}", [])
                            }:
                                raise ProviderError("identity_conflict")
                            countries = publication_countries(raw)
                            catalogue.merge_fields(row, {"attributes": {
                                "publication_countries": countries, "countries": countries,
                                "country_basis": "publication" if countries else None,
                                "publish_country": raw.get("publish_country"),
                                "publish_places": raw.get("publish_places"),
                            }}, "openlibrary")
                if row.kind == "edition":
                    await db.flush()
                    edition = await db.get(BookEdition, row.id)
                    if edition:
                        await catalogue.rollup_book_countries(db, await db.get(CatalogueEntity, edition.work_id))
                elif row.kind == "book":
                    mapping = next((x for x in ids if x.namespace == "openlibrary.book"), None)
                    if mapping and not row.attributes.get("countries"):
                        raw = await openlibrary.get(providers.http, f"/works/{mapping.external_id}/editions.json",
                                                    params={"limit": openlibrary.EDITION_LIMIT, "offset": 0})
                        doc = openlibrary.normalize({"key": f"/works/{mapping.external_id}", "title": row.name,
                                                     "_editions": raw.get("entries", [])[:openlibrary.EDITION_LIMIT]})
                        await apply_document_countries(db, doc, "openlibrary")
                    await db.flush()
                    await catalogue.rollup_book_countries(db, row)
        except Exception:
            result["failed"] += 1
            # Savepoint rollback expires loaded fields; reload before marking retry.
            await db.refresh(row)
        row.attributes = {**row.attributes, "country_metadata_version": VERSION,
                          "country_backfill_attempted_at": now.isoformat()}
        result["recovered" if country_codes(row.attributes.get("countries")) else "unresolved"] += 1
        await db.commit()
    return result
