"""Shared catalogue identities, separate from personal tracking facts.

One identity root makes every external mapping and graph edge a real FK. Kind
discriminators distinguish people, organizations, characters and works; editions
and releases have additional relational data and never become tracked works.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

KINDS = (
    "movie",
    "series",
    "game",
    "book",
    "book_series",
    "game_collection",
    "person",
    "organization",
    "character",
    "edition",
    "release",
)


class CatalogueEntity(Base):
    __tablename__ = "catalogue_entities"
    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ",".join(repr(k) for k in KINDS) + ")",
            name="ck_catalogue_kind",
        ),
        Index("ix_catalogue_kind_name", "kind", "name"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    # Normalized descriptive fields only. Provider responses live in snapshots.
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    field_sources: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    protected_fields: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CatalogueIdentity(Base):
    __tablename__ = "catalogue_identities"
    __table_args__ = (
        UniqueConstraint("namespace", "external_id", name="uq_catalogue_identity"),
        UniqueConstraint(
            "entity_id",
            "namespace",
            "external_id",
            name="uq_catalogue_identity_reference",
        ),
        Index("ix_catalogue_identity_entity", "entity_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"), nullable=False
    )
    namespace: Mapped[str] = mapped_column(String(80), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class CatalogueLegacyLink(Base):
    __tablename__ = "catalogue_legacy_links"
    media_id: Mapped[int] = mapped_column(
        ForeignKey("media.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class CatalogueShowLink(Base):
    __tablename__ = "catalogue_show_links"
    show_id: Mapped[int] = mapped_column(
        ForeignKey("shows.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class BookEdition(Base):
    __tablename__ = "catalogue_book_editions"
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="CASCADE"), primary_key=True
    )
    work_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    publisher_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT")
    )
    release_date: Mapped[str | None] = mapped_column(String(20))
    language: Mapped[str | None] = mapped_column(String(80))
    format: Mapped[str | None] = mapped_column(String(100))
    pages: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("entity_id", "work_id", name="uq_catalogue_edition_work"),
        CheckConstraint(
            "pages IS NULL OR pages >= 0", name="ck_catalogue_edition_pages"
        ),
        CheckConstraint("entity_id <> work_id", name="ck_catalogue_edition_work"),
    )


class GameRelease(Base):
    __tablename__ = "catalogue_game_releases"
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="CASCADE"), primary_key=True
    )
    work_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    platform_namespace: Mapped[str] = mapped_column(String(80), nullable=False)
    platform_id: Mapped[str] = mapped_column(String(200), nullable=False)
    platform_name: Mapped[str | None] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(80))
    release_date: Mapped[str | None] = mapped_column(String(20))
    __table_args__ = (
        CheckConstraint("entity_id <> work_id", name="ck_catalogue_release_work"),
    )


class ScreenCharacter(Base):
    """An actor's named screen role; not a global fictional-character identity."""
    __tablename__ = "catalogue_screen_characters"
    __table_args__ = (
        UniqueConstraint("actor_id", "role_key", name="uq_screen_character_actor_role"),
        UniqueConstraint("id", "actor_id", name="uq_screen_character_actor"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("catalogue_entities.id", ondelete="RESTRICT"), nullable=False)
    role_key: Mapped[str] = mapped_column(String(500), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


class CatalogueCredit(Base):
    __tablename__ = "catalogue_credits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["screen_character_id", "contributor_id"],
            ["catalogue_screen_characters.id", "catalogue_screen_characters.actor_id"],
            name="fk_credit_screen_character_actor", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["edition_id", "work_id"],
            ["catalogue_book_editions.entity_id", "catalogue_book_editions.work_id"],
            ondelete="RESTRICT",
            name="fk_catalogue_credit_edition_work",
        ),
        UniqueConstraint(
            "work_id", "provider", "source_key", name="uq_catalogue_credit_source"
        ),
        UniqueConstraint("id", "work_id", name="uq_catalogue_credit_work"),
        Index(
            "ix_catalogue_credit_contributor_role", "contributor_id", "role", "work_id"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"), nullable=False
    )
    contributor_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"), nullable=False
    )
    edition_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalogue_book_editions.entity_id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    role_label: Mapped[str | None] = mapped_column(String(200))
    character_label: Mapped[str | None] = mapped_column(String(500))
    character_image_url: Mapped[str | None] = mapped_column(String(2048))
    screen_character_id: Mapped[int | None] = mapped_column(Integer, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    source_key: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[str] = mapped_column(
        String(80), nullable=False, default="title", server_default="title"
    )
    position: Mapped[int | None] = mapped_column(Integer)


class CharacterAppearance(Base):
    __tablename__ = "catalogue_character_appearances"
    __table_args__ = (
        UniqueConstraint(
            "work_id", "character_id", "provider", name="uq_catalogue_appearance"
        ),
        UniqueConstraint("id", "work_id", name="uq_catalogue_appearance_work"),
        Index("ix_catalogue_appearance_character", "character_id", "work_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


class CharacterPerformance(Base):
    __tablename__ = "catalogue_character_performances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["credit_id", "work_id"],
            ["catalogue_credits.id", "catalogue_credits.work_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["appearance_id", "work_id"],
            [
                "catalogue_character_appearances.id",
                "catalogue_character_appearances.work_id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "credit_id", "appearance_id", "language", name="uq_catalogue_performance"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_id: Mapped[int] = mapped_column(Integer, nullable=False)
    appearance_id: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(
        String(80), nullable=False, default="", server_default=""
    )


class CatalogueRelationship(Base):
    __tablename__ = "catalogue_relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "target_id",
            "relation",
            "provider",
            name="uq_catalogue_relationship",
        ),
        CheckConstraint(
            "source_id <> target_id", name="ck_catalogue_relationship_distinct"
        ),
        CheckConstraint(
            "relation IN ('dlc','expansion','standalone_expansion','remake','remaster','port','version','series_member','recommendation')",
            name="ck_catalogue_relationship_type",
        ),
        Index("ix_catalogue_relationship_target", "target_id", "relation"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"), nullable=False
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


class MetadataSnapshot(Base):
    __tablename__ = "catalogue_metadata_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "provider", "kind", "external_id", name="uq_catalogue_snapshot"
        ),
        Index("ix_catalogue_snapshot_due", "next_attempt_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT")
    )
    payload: Mapped[dict | None] = mapped_column(JSONB)
    coverage: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    lease_token: Mapped[str | None] = mapped_column(String(40))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)


class MetadataProviderBudget(Base):
    __tablename__ = "catalogue_provider_budgets"
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    next_request_at: Mapped[datetime | None] = mapped_column(DateTime)
    month: Mapped[str | None] = mapped_column(String(7))
    day: Mapped[str | None] = mapped_column(String(10))
    daily_request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class SteamPriceSnapshot(Base):
    __tablename__ = "catalogue_steam_prices"
    __table_args__ = (
        CheckConstraint(
            "identity_namespace = 'steam.app'", name="ck_catalogue_price_namespace"
        ),
        ForeignKeyConstraint(
            ["work_id", "identity_namespace", "steam_app_id"],
            [
                "catalogue_identities.entity_id",
                "catalogue_identities.namespace",
                "catalogue_identities.external_id",
            ],
            ondelete="RESTRICT",
            name="fk_catalogue_price_steam_identity",
        ),
        UniqueConstraint(
            "work_id", "steam_app_id", "country", name="uq_catalogue_steam_price"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_entities.id", ondelete="RESTRICT"), nullable=False
    )
    steam_app_id: Mapped[str] = mapped_column(String(20), nullable=False)
    identity_namespace: Mapped[str] = mapped_column(
        String(80), nullable=False, default="steam.app", server_default="steam.app"
    )
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    # Money objects retain currency and integer minor units from ITAD.
    current: Mapped[dict | None] = mapped_column(JSONB)
    historical_low: Mapped[dict | None] = mapped_column(JSONB)
    historical_low_at: Mapped[str | None] = mapped_column(String(80))
    url: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
