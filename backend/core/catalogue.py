"""Canonical identity resolution and resumable metadata ingestion.

Provider calls happen before catalogue writes. Only authoritative provider
cross-references (or an administrator's reviewed binding) link identities.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from models.catalogue import (
    CatalogueEntity,
    CatalogueIdentity,
    CatalogueLegacyLink,
    CatalogueShowLink,
    CatalogueCredit,
    CatalogueRelationship,
    CharacterAppearance,
    CharacterPerformance,
    BookEdition,
    GameRelease,
    MetadataSnapshot,
    SteamPriceSnapshot,
)
from .catalogue_normalize import NAMESPACE_KINDS, PRIMARY, identity, document, image
from .catalogue_providers import ProviderError


class IdentityConflict(Exception):
    pass


def validate_identity(namespace, external_id, kind=None):
    kinds = NAMESPACE_KINDS.get(namespace)
    if not kinds or not external_id or len(str(external_id)) > 200:
        raise IdentityConflict("Invalid identity namespace or identifier")
    if kind and kind not in kinds:
        raise IdentityConflict("Identity namespace has a different entity kind")
    external_id = str(external_id).strip()
    if not external_id:
        raise IdentityConflict("Empty external identifier")
    if namespace.startswith(("tmdb.", "tvdb.", "hardcover.", "igdb.")) or namespace in (
        "steam.app",
        "rawg.game",
        "rawg.developer",
        "rawg.publisher",
    ):
        if not external_id.isdigit() or int(external_id) <= 0:
            raise IdentityConflict("Expected a positive provider identifier")
        external_id = str(int(external_id))
    if namespace == "imdb.title":
        import re

        if not re.fullmatch(r"tt[0-9]+", external_id):
            raise IdentityConflict("Invalid IMDb title identifier")
    if namespace.startswith("isbn."):
        from .catalogue_normalize import isbn

        external_id = isbn(external_id, int(namespace.split(".")[1]))
        if not external_id:
            raise IdentityConflict("Invalid ISBN checksum")
    return external_id


async def write_lock(db):
    # A small catalogue uses one short write lane. This also prevents races
    # between imports with disjoint native IDs but a common authoritative ID.
    await db.execute(text("SELECT pg_advisory_xact_lock(735903601)"))


async def resolve_identity(db, namespace, external_id):
    external_id = validate_identity(namespace, external_id)
    return (
        await db.execute(
            select(CatalogueIdentity).where(
                CatalogueIdentity.namespace == namespace,
                CatalogueIdentity.external_id == external_id,
            )
        )
    ).scalar_one_or_none()


async def bind_identity(db, entity_id, namespace, external_id, source, evidence=None):
    entity = await db.get(CatalogueEntity, entity_id)
    if not entity:
        raise IdentityConflict("Unknown canonical entity")
    external_id = validate_identity(namespace, external_id, entity.kind)
    previous = await resolve_identity(db, namespace, external_id)
    if previous:
        if previous.entity_id != entity_id:
            raise IdentityConflict(
                "Identifier already belongs to another entity; review before merging"
            )
        return previous
    row = CatalogueIdentity(
        entity_id=entity_id,
        namespace=namespace,
        external_id=external_id,
        source=source,
        evidence=evidence or {},
    )
    db.add(row)
    await db.flush()
    return row


def source_rank(kind, source):
    if source in ("manual", "reviewed"):
        return 1000
    if source == "legacy":
        return 200
    if source == PRIMARY.get(kind):
        return 100
    if source == "rawg":
        return 20
    return 80


def placeholder_name(values):
    return values.get("name") in {
        f"{x['namespace']}:{x['external_id']}" for x in values.get("identities", [])
    }


def merge_fields(row, values, source):
    sources = dict(row.field_sources or {})
    protected = set(row.protected_fields or [])
    for field in ("name", "description", "image_url"):
        value = values.get(field)
        if (
            not value
            or field in protected
            or (field == "name" and placeholder_name(values))
        ):
            continue
        old = getattr(row, field)
        old_source = sources.get(field)
        placeholder = field == "name" and ":" in (old or "") and not old_source
        if (
            not old
            or placeholder
            or (
                old_source
                and source_rank(row.kind, source) >= source_rank(row.kind, old_source)
            )
        ):
            setattr(row, field, value)
            sources[field] = source
    attrs = dict(row.attributes or {})
    for key, value in (values.get("attributes") or {}).items():
        field = "attributes." + key
        if (
            value is None
            or value == []
            or value == {}
            or value == ""
            or field in protected
        ):
            continue
        old_source = sources.get(field)
        if attrs.get(key) is None or (
            old_source
            and source_rank(row.kind, source) >= source_rank(row.kind, old_source)
        ):
            attrs[key] = value
            sources[field] = source
    row.attributes = attrs
    row.field_sources = sources


async def upsert_entity(db, values, source):
    ids = values.get("identities") or []
    if not ids:
        raise IdentityConflict("Canonical records require an identity")
    matches = set()
    for x in ids:
        validate_identity(x["namespace"], x["external_id"], values["kind"])
        found = await resolve_identity(db, x["namespace"], x["external_id"])
        if found:
            matches.add(found.entity_id)
    if len(matches) > 1:
        raise IdentityConflict(
            "Provider cross-references disagree with existing mappings"
        )
    if matches:
        row = await db.get(CatalogueEntity, matches.pop())
        if row.kind != values["kind"]:
            raise IdentityConflict(
                "Provider cross-reference has a different entity kind"
            )
    else:
        row = CatalogueEntity(
            kind=values["kind"],
            name=values["name"],
            attributes={},
            field_sources={} if placeholder_name(values) else {"name": source},
            protected_fields=[],
        )
        db.add(row)
        await db.flush()
    merge_fields(row, values, source)
    for x in ids:
        await bind_identity(
            db,
            row.id,
            x["namespace"],
            x["external_id"],
            source,
            {"cross_reference": ids[0]},
        )
    return row


def has_value(value):
    return value is not None and value != "" and value != [] and value != {}


async def upsert(db, model, values, keys, preserve_missing=False):
    if preserve_missing:
        values = {k: v for k, v in values.items() if k in keys or has_value(v)}
        if "attributes" in values:
            values["attributes"] = {
                k: v for k, v in values["attributes"].items() if has_value(v)
            }
    insert_values = (
        {"role": "contributor", **values} if model is CatalogueCredit else values
    )
    statement = insert(model).values(**insert_values)
    update = {k: statement.excluded[k] for k in values if k not in keys}
    if preserve_missing and "attributes" in update:
        update["attributes"] = model.attributes.op("||")(statement.excluded.attributes)
    if update:
        statement = statement.on_conflict_do_update(index_elements=keys, set_=update)
    else:
        statement = statement.on_conflict_do_nothing(index_elements=keys)
    await db.execute(statement)
    return (
        await db.execute(
            select(model).where(*(getattr(model, k) == values[k] for k in keys))
        )
    ).scalar_one()


async def ingest_document(db, doc, provider):
    await write_lock(db)
    work = await upsert_entity(db, doc["work"], provider)
    if work.kind not in ("movie", "series", "game", "book", "person"):
        raise IdentityConflict("Invalid catalogue work kind")
    for c in doc["credits"]:
        if c.get("edition_identity"):
            continue
        person = await upsert_entity(db, c["contributor"], provider)
        if person.kind not in ("person", "organization"):
            raise IdentityConflict("Credits require a person or organization")
        await upsert(
            db,
            CatalogueCredit,
            {
                "work_id": work.id,
                "contributor_id": person.id,
                "provider": provider,
                "source_key": str(c["source_key"])[:200],
                "role": c["role"],
                "role_label": c.get("role_label"),
                "character_label": c.get("character_label"),
                "scope": c.get("scope", "title"),
                "position": c.get("position"),
            },
            ["work_id", "provider", "source_key"],
            preserve_missing=True,
        )
    for c in doc["characters"]:
        character = await upsert_entity(db, c["character"], provider)
        if character.kind != "character":
            raise IdentityConflict("Appearance requires a fictional character")
        await upsert(
            db,
            CharacterAppearance,
            {
                "work_id": work.id,
                "character_id": character.id,
                "provider": provider,
                "attributes": c.get("attributes", {}),
            },
            ["work_id", "character_id", "provider"],
            preserve_missing=True,
        )
    for e in doc["editions"]:
        if work.kind != "book":
            raise IdentityConflict("Edition requires a book work")
        edition = await upsert_entity(db, e["entity"], provider)
        publisher = (
            await upsert_entity(db, e["publisher"], provider)
            if e.get("publisher")
            else None
        )
        old = await db.get(BookEdition, edition.id)
        if old and old.work_id != work.id:
            raise IdentityConflict("Edition already belongs to another book work")
        values = {
            "entity_id": edition.id,
            "work_id": work.id,
            "publisher_id": publisher.id if publisher else None,
            **{k: e.get(k) for k in ("release_date", "language", "format", "pages")},
        }
        # Partial editions may not clear previously known descriptive fields.
        if old:
            values = {k: v for k, v in values.items() if has_value(v)}
        await upsert(db, BookEdition, values, ["entity_id"])
    for c in doc["credits"]:
        if not c.get("edition_identity"):
            continue
        x = c["edition_identity"]
        mapping = await resolve_identity(db, x["namespace"], x["external_id"])
        edition = await db.get(BookEdition, mapping.entity_id) if mapping else None
        if not edition or edition.work_id != work.id:
            raise IdentityConflict(
                "Edition credit requires an edition of the same work"
            )
        person = await upsert_entity(db, c["contributor"], provider)
        await upsert(
            db,
            CatalogueCredit,
            {
                "work_id": work.id,
                "edition_id": edition.entity_id,
                "contributor_id": person.id,
                "provider": provider,
                "source_key": str(c["source_key"]),
                "role": c["role"],
                "role_label": c.get("role_label"),
                "scope": "edition",
            },
            ["work_id", "provider", "source_key"],
            preserve_missing=True,
        )
    for r in doc["releases"]:
        if work.kind != "game":
            raise IdentityConflict("Release requires a game work")
        release = await upsert_entity(db, r["entity"], provider)
        old = await db.get(GameRelease, release.id)
        if old and old.work_id != work.id:
            raise IdentityConflict("Release already belongs to another game work")
        values = {
            "entity_id": release.id,
            "work_id": work.id,
            **{
                k: r.get(k)
                for k in (
                    "platform_namespace",
                    "platform_id",
                    "platform_name",
                    "region",
                    "release_date",
                )
            },
        }
        if old:
            values = {k: v for k, v in values.items() if has_value(v)}
        await upsert(db, GameRelease, values, ["entity_id"])
    for r in doc["relationships"]:
        target = await upsert_entity(db, r["target"], provider)
        source_id, target_id = (
            (target.id, work.id) if r.get("reverse") else (work.id, target.id)
        )
        if source_id == target_id:
            continue
        await upsert(
            db,
            CatalogueRelationship,
            {
                "source_id": source_id,
                "target_id": target_id,
                "relation": r["relation"],
                "provider": provider,
                "attributes": r.get("attributes", {}),
            },
            ["source_id", "target_id", "relation", "provider"],
            preserve_missing=True,
        )
    await db.flush()
    return work


async def link_performance(db, credit_id, appearance_id, language=""):
    credit = await db.get(CatalogueCredit, credit_id)
    appearance = await db.get(CharacterAppearance, appearance_id)
    if not credit or not appearance or credit.work_id != appearance.work_id:
        raise IdentityConflict("Performance must belong to the same work")
    person = await db.get(CatalogueEntity, credit.contributor_id)
    if person.kind != "person" or credit.role not in (
        "actor",
        "voice_actor",
        "narrator",
    ):
        raise IdentityConflict("Performance requires a performer credit")
    return await upsert(
        db,
        CharacterPerformance,
        {
            "work_id": credit.work_id,
            "credit_id": credit.id,
            "appearance_id": appearance.id,
            "language": language,
        },
        ["credit_id", "appearance_id", "language"],
    )


async def unlink_performance(db, performance_id):
    await write_lock(db)
    row = await db.get(CharacterPerformance, performance_id)
    if not row:
        return False
    await db.delete(row)
    return True


def refresh_result(row, status):
    return {
        "entity_id": row.entity_id,
        "status": status,
        "fetched_at": row.fetched_at,
        "error_code": row.error_code,
        "next_attempt_at": row.next_attempt_at,
        "coverage": row.coverage or {},
    }


def cached_refresh_status(row, now):
    if row.lease_until and row.lease_until > now:
        return "refreshing"
    if row.expires_at and row.expires_at > now:
        return "cached"
    if row.next_attempt_at and row.next_attempt_at > now:
        return "last_good" if row.fetched_at else "retry_pending"
    return None


async def record_refresh_failure(db, row, token, error, base_delay):
    # Use the same lock order as claims/publication, including after a rollback.
    try:
        await write_lock(db)
        await db.refresh(row, with_for_update=True)
        if row.lease_token != token:
            return "refreshing", False
        row.attempts += 1
        row.error_code = (
            error.code
            if isinstance(error, ProviderError)
            else "identity_conflict"
            if isinstance(error, IdentityConflict)
            else "timeout"
            if isinstance(error, asyncio.TimeoutError)
            else "ingestion_failed"
        )
        row.next_attempt_at = datetime.now(timezone.utc).replace(
            tzinfo=None
        ) + timedelta(
            seconds=max(
                getattr(error, "retry_after", 0) or 0,
                min(86400, base_delay * 2 ** min(row.attempts, 10)),
            )
        )
        return ("last_good" if row.fetched_at else "failed"), True
    except Exception:
        await db.rollback()
        raise ProviderError("ingestion_failed") from None


async def refresh_metadata(
    db, providers, provider, kind, external_id, edition_page=None
):
    providers.validate(provider, kind)
    external_id = validate_identity(f"{provider}.{kind}", external_id, kind)
    if edition_page is not None and (
        provider != "hardcover" or kind != "book" or not 1 <= edition_page <= 10000
    ):
        raise ProviderError("invalid_request")
    snapshot_kind = "edition_page" if edition_page else kind
    snapshot_id = f"{external_id}:{edition_page}" if edition_page else external_id
    token = uuid.uuid4().hex
    await write_lock(db)
    await db.execute(
        insert(MetadataSnapshot)
        .values(provider=provider, kind=snapshot_kind, external_id=snapshot_id)
        .on_conflict_do_nothing()
    )
    row = (
        await db.execute(
            select(MetadataSnapshot)
            .where(
                MetadataSnapshot.provider == provider,
                MetadataSnapshot.kind == snapshot_kind,
                MetadataSnapshot.external_id == snapshot_id,
            )
            .with_for_update()
        )
    ).scalar_one()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cached_status = cached_refresh_status(row, now)
    if cached_status:
        result = refresh_result(row, cached_status)
        await db.commit()
        return result
    row.lease_token = token
    row.lease_until = now + timedelta(minutes=3)
    await db.commit()
    try:
        doc, payload = (
            await providers.book_editions_page(int(external_id), edition_page)
            if edition_page
            else await providers.detail(provider, kind, external_id)
        )
        # Recheck ownership before publishing. Late workers cannot overwrite.
        await write_lock(db)
        await db.refresh(row, with_for_update=True)
        if row.lease_token != token:
            result = refresh_result(row, "refreshing")
            await db.commit()
            return result
        async with db.begin_nested():
            entity = await ingest_document(db, doc, provider)
            row.entity_id = entity.id
            row.payload = payload
            row.coverage = doc.get("coverage", {})
            row.fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
            row.expires_at = row.fetched_at + timedelta(days=7)
            row.attempts = 0
            row.error_code = None
            row.next_attempt_at = None
        status = "updated"
    except (ProviderError, IdentityConflict, asyncio.TimeoutError) as e:
        status, owned = await record_refresh_failure(db, row, token, e, 60)
        if not owned:
            result = refresh_result(row, status)
            await db.commit()
            return result
    except Exception as e:
        # Reset a failed transaction, then retain a durable, redacted retry state.
        await db.rollback()
        status, owned = await record_refresh_failure(db, row, token, e, 60)
        if not owned:
            result = refresh_result(row, status)
            await db.commit()
            return result
    row.lease_token = None
    row.lease_until = None
    result = refresh_result(row, status)
    await db.commit()
    return result


async def refresh_prices(db, providers, work_id, appid, country):
    appid = validate_identity("steam.app", appid, "game")
    mapping = await resolve_identity(db, "steam.app", appid)
    work = await db.get(CatalogueEntity, work_id)
    if not mapping or mapping.entity_id != work_id or not work or work.kind != "game":
        raise IdentityConflict("Prices require a verified Steam AppID for this game")
    country = country.upper()
    # The same durable lease/cache machinery bounds price refreshes across users.
    key = f"{appid}:{country}"
    await write_lock(db)
    await db.execute(
        insert(MetadataSnapshot)
        .values(provider="itad", kind="price", external_id=key)
        .on_conflict_do_nothing()
    )
    state = (
        await db.execute(
            select(MetadataSnapshot)
            .where(
                MetadataSnapshot.provider == "itad",
                MetadataSnapshot.kind == "price",
                MetadataSnapshot.external_id == key,
            )
            .with_for_update()
        )
    ).scalar_one()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cached_status = cached_refresh_status(state, now)
    if cached_status:
        result = refresh_result(state, cached_status)
        await db.commit()
        return result
    token = uuid.uuid4().hex
    state.lease_token = token
    state.lease_until = now + timedelta(minutes=3)
    await db.commit()
    try:
        prices = await providers.steam_prices(appid, country)
        await write_lock(db)
        await db.refresh(state, with_for_update=True)
        if state.lease_token != token:
            result = refresh_result(state, "refreshing")
            await db.commit()
            return result
        async with db.begin_nested():
            await write_lock(db)
            await bind_identity(
                db,
                work_id,
                "itad.game",
                prices["itad_id"],
                "itad",
                {"steam_app_id": appid},
            )
            # A successful no-offer result clears current price, but a missing
            # historical low never erases the previous verified low/date.
            values = {
                k: prices[k]
                for k in (
                    "current",
                    "historical_low",
                    "historical_low_at",
                    "url",
                    "payload",
                )
            }
            if not values["historical_low"] or not values["historical_low_at"]:
                values.pop("historical_low")
                values.pop("historical_low_at")
            await upsert(
                db,
                SteamPriceSnapshot,
                {
                    "work_id": work_id,
                    "steam_app_id": appid,
                    "country": country,
                    "fetched_at": datetime.now(timezone.utc).replace(tzinfo=None),
                    **values,
                },
                ["work_id", "steam_app_id", "country"],
            )
            state.entity_id = work_id
            state.payload = prices["payload"]
            state.fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
            state.expires_at = state.fetched_at + timedelta(hours=6)
            state.error_code = None
            state.next_attempt_at = None
            state.attempts = 0
        status = "updated"
    except (ProviderError, IdentityConflict, asyncio.TimeoutError) as e:
        status, owned = await record_refresh_failure(db, state, token, e, 120)
        if not owned:
            result = refresh_result(state, status)
            await db.commit()
            return result
    except Exception as e:
        await db.rollback()
        status, owned = await record_refresh_failure(db, state, token, e, 120)
        if not owned:
            result = refresh_result(state, status)
            await db.commit()
            return result
    state.lease_token = None
    state.lease_until = None
    result = refresh_result(state, status)
    await db.commit()
    return result


async def backfill_legacy(db, media_cursor=0, show_cursor=0, limit=50):
    from models.media import Media
    from models.show import Show
    from models.title_credits import TitleCredits
    from models.base import MediaType

    result = {
        "media_cursor": media_cursor,
        "show_cursor": show_cursor,
        "imported": 0,
        "conflicts": [],
    }
    rows = (
        (
            await db.execute(
                select(Media)
                .where(
                    Media.id > media_cursor,
                    Media.media_type.in_(
                        [MediaType.movie, MediaType.series, MediaType.person]
                    ),
                )
                .order_by(Media.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    async def one(row, show=False):
        kind = "series" if show else row.media_type.value
        ids = []
        if row.tmdb_id:
            ids.append(identity(f"tmdb.{kind}", row.tmdb_id))
        if getattr(row, "tvdb_id", None) and kind == "series":
            ids.append(identity("tvdb.series", row.tvdb_id))
        if getattr(row, "imdb_id", None) and kind != "person":
            ids.append(identity("imdb.title", row.imdb_id))
        if not ids:
            return
        values = {
            "kind": kind,
            "name": row.title,
            "description": getattr(row, "overview", None),
            "image_url": image(getattr(row, "poster_path", None)),
            "identities": ids,
            "attributes": {
                "release_date": getattr(row, "release_date", None)
                or getattr(row, "first_air_date", None),
                "runtime": getattr(row, "runtime", None),
            },
        }
        try:
            async with db.begin_nested():
                doc = document(values)
                # Reduced compatibility snapshots still have verified person IDs.
                if row.tmdb_id and kind in ("movie", "series"):
                    cache = (
                        await db.execute(
                            select(TitleCredits).where(
                                TitleCredits.tmdb_id == row.tmdb_id,
                                TitleCredits.media_type == kind,
                            )
                        )
                    ).scalar_one_or_none()
                    if cache:
                        for field, role, namespace in [
                            ("cast", "actor", "tmdb.person"),
                            ("directors", "director", "tmdb.person"),
                            ("writers", "writer", "tmdb.person"),
                            ("studios", "producer", "tmdb.company"),
                            ("networks", "broadcaster", "tmdb.network"),
                        ]:
                            for p in getattr(cache, field) or []:
                                if p.get("id"):
                                    doc["credits"].append(
                                        {
                                            "contributor": {
                                                "kind": "person"
                                                if namespace == "tmdb.person"
                                                else "organization",
                                                "name": p.get("name")
                                                or f"{namespace}:{p['id']}",
                                                "identities": [
                                                    identity(namespace, p["id"])
                                                ],
                                            },
                                            "role": role,
                                            "source_key": f"{field}:{p['id']}",
                                        }
                                    )
                entity = await ingest_document(db, doc, "legacy")
                await upsert(
                    db,
                    CatalogueShowLink if show else CatalogueLegacyLink,
                    {"show_id" if show else "media_id": row.id, "entity_id": entity.id},
                    ["show_id" if show else "media_id"],
                )
                result["imported"] += 1
        except IdentityConflict:
            result["conflicts"].append(
                {"table": "shows" if show else "media", "id": row.id}
            )

    for row in rows:
        await one(row)
        result["media_cursor"] = row.id
    shows = (
        (
            await db.execute(
                select(Show).where(Show.id > show_cursor).order_by(Show.id).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for row in shows:
        await one(row, True)
        result["show_cursor"] = row.id
    result["has_more"] = len(rows) == limit or len(shows) == limit
    await db.commit()
    return result
