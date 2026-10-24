"""Authenticated shared metadata API; admin-only identity corrections/backfills."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import catalogue, settings_store
from core.catalogue_normalize import image
from core.catalogue_normalize import PRIMARY, isbn as valid_isbn
from core.catalogue_providers import (
    ATTRIBUTION,
    CatalogueProviders,
    ProviderError,
    ProviderHTTP,
)
from core.limiter import limiter
from db import get_db, AsyncSessionLocal
from dependencies import get_current_user, get_optional_user, require_admin
from models.users import User
from models.catalogue import (
    CatalogueEntity,
    CatalogueIdentity,
    CatalogueCredit,
    CharacterAppearance,
    CharacterPerformance,
    CatalogueRelationship,
    BookEdition,
    GameRelease,
    SteamPriceSnapshot,
    MetadataSnapshot,
    ScreenCharacter,
)
from schemas_characters import ScreenCharacterDetail, ScreenCharacterList
from core.screen_characters import character_detail
from core.game_access import require as require_games, allowed as games_allowed

router = APIRouter(prefix="/catalogue", tags=["catalogue"])
DB = Annotated[AsyncSession, Depends(get_db)]
Caller = Annotated[User, Depends(get_current_user)]
OptionalCaller = Annotated[User | None, Depends(get_optional_user)]
Admin = Annotated[User, Depends(require_admin)]
Provider = Literal["tmdb", "tvdb", "igdb", "hardcover", "rawg", "openlibrary"]
WorkKind = Literal["movie", "series", "game", "book"]


class EntityOut(BaseModel):
    id: int
    kind: str
    name: str
    description: str | None = None
    image_url: str | None = None
    attributes: dict = Field(default_factory=dict)
    field_sources: dict = Field(default_factory=dict)
    model_config = {"from_attributes": True}


class IdentityBinding(BaseModel):
    namespace: str = Field(min_length=1, max_length=80)
    external_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=3, max_length=1000)


class PerformanceBinding(BaseModel):
    credit_id: int = Field(gt=0)
    appearance_id: int = Field(gt=0)
    language: str = Field(default="", max_length=80)


class ManualCorrection(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20000)
    image_url: str | None = Field(default=None, max_length=2000)


async def authorize_entity(db, entity, viewer):
    if games_allowed(viewer):
        return
    if entity.kind in ('game', 'release', 'game_collection'):
        require_games(viewer)
    if entity.kind in ('person', 'organization', 'character'):
        from sqlalchemy import or_
        games = select(CatalogueEntity.id).where(CatalogueEntity.kind == 'game')
        others = select(CatalogueEntity.id).where(CatalogueEntity.kind != 'game')
        credit = CatalogueCredit.contributor_id == entity.id
        character = CharacterAppearance.character_id == entity.id
        restricted, public = (await db.execute(select(
            or_(select(CatalogueCredit.id).where(credit, CatalogueCredit.work_id.in_(games)).exists(),
                select(CharacterAppearance.id).where(character, CharacterAppearance.work_id.in_(games)).exists()),
            or_(select(CatalogueCredit.id).where(credit, CatalogueCredit.work_id.in_(others)).exists(),
                select(CharacterAppearance.id).where(character, CharacterAppearance.work_id.in_(others)).exists(),
                select(CatalogueIdentity.id).where(CatalogueIdentity.entity_id == entity.id,
                    CatalogueIdentity.namespace.in_(('tmdb.person', 'tmdb.company', 'tmdb.network'))).exists())
        ))).one()
        if restricted and not public:
            require_games(viewer)


async def providers(db, user):
    return CatalogueProviders(
        ProviderHTTP(session_factory=AsyncSessionLocal),
        tmdb_key=await settings_store.get_user_tmdb_key(db, user.id),
        tvdb_key=await settings_store.get_user_tvdb_key(db, user.id),
    )


def safe_error(error):
    if isinstance(error, catalogue.IdentityConflict):
        return HTTPException(409, str(error))
    return HTTPException(
        422 if error.code in ("unsupported", "invalid_request") else 503,
        detail={"code": error.code},
    )


@router.get("/providers")
async def provider_sources(current_user: Caller) -> dict:
    return {"providers": ATTRIBUTION}


@router.get("/search")
@limiter.limit("30/minute")
async def search(
    request: Request,
    db: DB,
    current_user: Caller,
    kind: WorkKind,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    provider: Provider | None = None,
    page: Annotated[int, Query(ge=1, le=100)] = 1,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    try:
        if kind == 'game':
            require_games(current_user)
        provider = provider or ("openlibrary" if kind == "book" else PRIMARY[kind])
        client = await providers(db, current_user)
        rows = await client.search(provider, kind, q, page, limit)
        return {
            "provider": provider,
            "kind": kind,
            "page": page,
            "results": [{**r, "image_url": image(r.get("image_url"))} for r in rows],
            "attribution": ATTRIBUTION[provider],
        }
    except ProviderError as e:
        raise safe_error(e) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, detail={"code": "unavailable"}) from None


@router.post("/books/lookup/{isbn}")
@limiter.limit("15/minute")
async def lookup_book(
    request: Request,
    isbn: Annotated[str, Path(max_length=30)],
    db: DB,
    current_user: Caller,
) -> dict:
    """Match an edition through a verified ISBN using the key-free provider."""
    value = valid_isbn(isbn, 13) or valid_isbn(isbn, 10)
    if not value:
        raise HTTPException(422, detail={"code": "invalid_request"})
    try:
        client = await providers(db, current_user)
        mapping = await catalogue.resolve_identity(db, f"isbn.{len(value)}", value)
        native = None
        if mapping:
            native = await db.scalar(
                select(CatalogueIdentity.external_id).where(
                    CatalogueIdentity.entity_id == mapping.entity_id,
                    CatalogueIdentity.namespace == "openlibrary.edition",
                )
            )
        native = native or await client.openlibrary_isbn(value)
        return {
            "provider": "openlibrary",
            **await catalogue.refresh_metadata(
                db, client, "openlibrary", "book", native
            ),
        }
    except (ProviderError, catalogue.IdentityConflict) as e:
        raise safe_error(e) from None


@router.get("/resolve")
async def resolve(
    db: DB,
    current_user: Caller,
    namespace: Annotated[str, Query(max_length=80)],
    external_id: Annotated[str, Query(max_length=200)],
) -> dict:
    try:
        mapping = await catalogue.resolve_identity(db, namespace, external_id)
    except catalogue.IdentityConflict as e:
        raise safe_error(e) from None
    if mapping:
        entity = await db.get(CatalogueEntity, mapping.entity_id)
        if entity:
            await authorize_entity(db, entity, current_user)
    return {"entity_id": mapping.entity_id if mapping else None}


@router.post("/refresh/{provider}/{kind}/{external_id}")
@limiter.limit("15/minute")
async def refresh(
    request: Request,
    provider: Provider,
    kind: WorkKind,
    external_id: Annotated[str, Path(max_length=200)],
    db: DB,
    current_user: Caller,
    edition_page: Annotated[int | None, Query(ge=1, le=10000)] = None,
) -> dict:
    if kind == 'game':
        require_games(current_user)
    try:
        return await catalogue.refresh_metadata(
            db,
            await providers(db, current_user),
            provider,
            kind,
            external_id,
            edition_page,
        )
    except (ProviderError, catalogue.IdentityConflict) as e:
        raise safe_error(e) from None


@router.get("/entities/{entity_id}", response_model=EntityOut)
async def detail(entity_id: int, db: DB, current_user: Caller):
    row = await db.get(CatalogueEntity, entity_id)
    if not row:
        raise HTTPException(404, "Catalogue entity not found")
    await authorize_entity(db, row, current_user)
    out = EntityOut.model_validate(row)
    if row.kind in ("edition", "release"):
        item = await db.get(
            BookEdition if row.kind == "edition" else GameRelease, row.id
        )
        if item:
            fields = (
                (
                    "work_id",
                    "publisher_id",
                    "release_date",
                    "language",
                    "format",
                    "pages",
                )
                if row.kind == "edition"
                else (
                    "work_id",
                    "platform_namespace",
                    "platform_id",
                    "platform_name",
                    "region",
                    "release_date",
                )
            )
            out.attributes = {
                **out.attributes,
                **{field: getattr(item, field) for field in fields},
            }
    return out


@router.get("/characters", response_model=ScreenCharacterList)
async def screen_characters(db: DB, current_user: Caller,
    actor_id: Annotated[int | None, Query(gt=0)] = None,
    work_id: Annotated[int | None, Query(gt=0)] = None,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 30):
    if actor_id is None and work_id is None:
        raise HTTPException(422, "Select an actor or media")
    if work_id is not None:
        work = await db.get(CatalogueEntity, work_id)
        if work:
            await authorize_entity(db, work, current_user)
    query = select(ScreenCharacter.id)
    if actor_id is not None:
        query = query.where(ScreenCharacter.actor_id == actor_id)
    if work_id is not None:
        query = query.where(select(CatalogueCredit.id).where(CatalogueCredit.work_id == work_id,
            CatalogueCredit.screen_character_id == ScreenCharacter.id).exists())
    ids = list(await db.scalars(query.order_by(ScreenCharacter.id).offset((page-1)*limit).limit(limit)))
    return {'page': page, 'limit': limit, 'results': [await character_detail(db, id) for id in ids]}


@router.get("/characters/{character_id}", response_model=ScreenCharacterDetail)
async def screen_character(character_id: Annotated[int, Path(gt=0)], db: DB, current_user: Caller):
    result = await character_detail(db, character_id)
    if result is None:
        raise HTTPException(404, "Character not found")
    return result


@router.get("/entities/{entity_id}/details")
async def media_detail(entity_id: Annotated[int, Path(gt=0)], db: DB, current_user: OptionalCaller, response:Response,
    country:Annotated[str|None,Query(pattern=r'^[A-Za-z]{2}$')]=None) -> dict:
    from core.media_details import catalogue_title
    from core.tracking_access import catalog_access
    await catalog_access(db, current_user)
    work = await db.get(CatalogueEntity, entity_id)
    if not work or work.kind not in ('game', 'book'):
        raise HTTPException(404, "Game or book not found")
    await authorize_entity(db, work, current_user)
    response.headers['Cache-Control']='private, no-store'
    identities=None;metadata_status=None
    if work.kind=='game':
        from core.game_metadata import GameMetadata
        from sqlalchemy.ext.asyncio import async_sessionmaker
        identities=list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id==work.id)))
        service=GameMetadata(async_sessionmaker(db.bind,expire_on_commit=False),scope=db.bind)
        metadata_status,changed=await service.ensure(db,work,identities=identities)
        if changed:await db.refresh(work);identities=None
    try:data=await catalogue_title(db, work, current_user.id if current_user else None,store_country=country,enqueue_offers=True,enqueue_ratings=True,enqueue_info=True,identities=identities)
    except ProviderError as error:raise safe_error(error) from None
    if work.kind=='game':
        from core.game_imdb_enrichment import request as request_credits
        data['game_metadata_status']=metadata_status
        state=await request_credits(db,work.id)
        await db.commit()
        data['game_credit_status']=state
        from core.game_time_to_beat import view as time_estimates
        times=await time_estimates(db,work,enqueue=True)
        await db.commit()
        data['completion_estimates']=times['estimates']
        data['game_estimate_status']=times['status']
    return data


@router.get("/entities/{entity_id}/{section}")
async def related(
    entity_id: int,
    section: Literal[
        "identities",
        "credits",
        "characters",
        "performances",
        "editions",
        "releases",
        "relationships",
        "prices",
        "sources",
    ],
    db: DB,
    current_user: Caller,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    entity = await db.get(CatalogueEntity, entity_id)
    if not entity:
        raise HTTPException(404, "Catalogue entity not found")
    await authorize_entity(db, entity, current_user)
    models = {
        "identities": (
            CatalogueIdentity,
            CatalogueIdentity.entity_id == entity_id,
            ["namespace", "external_id", "source"],
        ),
        "credits": (
            CatalogueCredit,
            CatalogueCredit.work_id == entity_id,
            [
                "id",
                "contributor_id",
                "edition_id",
                "role",
                "role_label",
                "character_label",
                "character_image_url",
                "screen_character_id",
                "provider",
                "scope",
                "position",
            ],
        ),
        "characters": (
            CharacterAppearance,
            CharacterAppearance.work_id == entity_id,
            ["id", "character_id", "provider", "attributes"],
        ),
        "performances": (
            CharacterPerformance,
            CharacterPerformance.work_id == entity_id,
            ["id", "credit_id", "appearance_id", "language"],
        ),
        "editions": (
            BookEdition,
            BookEdition.work_id == entity_id,
            [
                "entity_id",
                "publisher_id",
                "release_date",
                "language",
                "format",
                "pages",
            ],
        ),
        "releases": (
            GameRelease,
            GameRelease.work_id == entity_id,
            [
                "entity_id",
                "platform_namespace",
                "platform_id",
                "platform_name",
                "region",
                "release_date",
            ],
        ),
        "relationships": (
            CatalogueRelationship,
            (CatalogueRelationship.source_id == entity_id)
            | (CatalogueRelationship.target_id == entity_id),
            ["source_id", "target_id", "relation", "provider", "attributes"],
        ),
        "prices": (
            SteamPriceSnapshot,
            SteamPriceSnapshot.work_id == entity_id,
            [
                "steam_app_id",
                "country",
                "current",
                "historical_low",
                "historical_low_at",
                "url",
                "fetched_at",
            ],
        ),
        "sources": (
            MetadataSnapshot,
            MetadataSnapshot.entity_id == entity_id,
            [
                "provider",
                "kind",
                "external_id",
                "coverage",
                "fetched_at",
                "expires_at",
                "error_code",
                "next_attempt_at",
            ],
        ),
    }
    model, condition, fields = models[section]
    if not games_allowed(current_user):
        if section == 'relationships':
            permitted = select(CatalogueEntity.id).where(CatalogueEntity.kind.not_in(('game', 'release', 'game_collection')))
            condition = condition & model.source_id.in_(permitted) & model.target_id.in_(permitted)
        elif section == 'sources':
            condition = condition & (model.provider != 'imdb')
    pk = list(model.__table__.primary_key)[0]
    rows = (
        (
            await db.execute(
                select(model)
                .where(condition)
                .order_by(pk)
                .offset((page - 1) * limit)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    out = [{field: getattr(row, field) for field in fields} for row in rows[:limit]]
    return {"results": out, "page": page, "has_more": len(rows) > limit}


@router.post("/entities/{entity_id}/identities")
async def bind(
    entity_id: int, body: IdentityBinding, db: DB, current_user: Admin
) -> dict:
    try:
        await catalogue.write_lock(db)
        mapping = await catalogue.bind_identity(
            db,
            entity_id,
            body.namespace,
            body.external_id,
            "reviewed",
            {"reviewer_id": current_user.id, "reason": body.reason},
        )
        await db.commit()
        return {
            "entity_id": mapping.entity_id,
            "namespace": mapping.namespace,
            "external_id": mapping.external_id,
        }
    except catalogue.IdentityConflict as e:
        await db.rollback()
        raise safe_error(e) from None


@router.patch("/entities/{entity_id}", response_model=EntityOut)
async def correct(entity_id: int, body: ManualCorrection, db: DB, current_user: Admin):
    await catalogue.write_lock(db)
    row = await db.get(CatalogueEntity, entity_id)
    if not row:
        raise HTTPException(404, "Catalogue entity not found")
    if body.image_url and not image(body.image_url):
        raise HTTPException(422, "Artwork must be an HTTPS URL")
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise HTTPException(422, "Name cannot be null")
    for field, value in changes.items():
        setattr(row, field, value)
    row.protected_fields = sorted(set(row.protected_fields or []) | set(changes))
    row.field_sources = {
        **(row.field_sources or {}),
        **{field: "manual" for field in changes},
    }
    await db.commit()
    return row


@router.post("/performances")
async def performance(body: PerformanceBinding, db: DB, current_user: Admin) -> dict:
    try:
        await catalogue.write_lock(db)
        row = await catalogue.link_performance(
            db, body.credit_id, body.appearance_id, body.language
        )
        await db.commit()
        return {"id": row.id}
    except catalogue.IdentityConflict as e:
        await db.rollback()
        raise safe_error(e) from None


@router.delete("/performances/{performance_id}")
async def remove_performance(
    performance_id: Annotated[int, Path(gt=0)], db: DB, current_user: Admin
) -> dict:
    if not await catalogue.unlink_performance(db, performance_id):
        raise HTTPException(404, "Performance link not found")
    await db.commit()
    return {"deleted": True}


@router.post("/backfill")
async def backfill(
    db: DB,
    current_user: Admin,
    media_cursor: Annotated[int, Query(ge=0)] = 0,
    show_cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    return await catalogue.backfill_legacy(db, media_cursor, show_cursor, limit)


@router.post("/entities/{entity_id}/steam-prices/{appid}")
@limiter.limit("10/minute")
async def prices(
    request: Request,
    entity_id: int,
    appid: Annotated[str, Path(pattern=r"^[1-9][0-9]{0,18}$")],
    db: DB,
    current_user: Caller,
    country: Annotated[str|None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
) -> dict:
    entity = await db.get(CatalogueEntity, entity_id)
    if not entity:
        raise HTTPException(404, "Catalogue entity not found")
    await authorize_entity(db, entity, current_user)
    try:
        from core.game_offers import country as effective_country
        selected=await effective_country(db,current_user.id,country)
        return await catalogue.refresh_prices(
            db, await providers(db, current_user), entity_id, appid, selected['country']
        )
    except (ProviderError, catalogue.IdentityConflict) as e:
        raise safe_error(e) from None
