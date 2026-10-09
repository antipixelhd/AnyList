"""Shared catalogue identities and metadata foundation (additive).

Revision ID: mt036
Revises: mt035
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "mt036"
down_revision = "mt035"
branch_labels = None
depends_on = None


def upgrade():
    # Additive tables; existing facts and canonical numbering remain untouched.
    op.create_table(
        "catalogue_entities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "field_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "protected_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(precision=3),
            server_default=sa.text("date_trunc('milliseconds', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(precision=3),
            server_default=sa.text("date_trunc('milliseconds', now())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('movie','series','game','book','book_series','game_collection','person','organization','character','edition','release')",
            name="ck_catalogue_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_catalogue_kind_name", "catalogue_entities", ["kind", "name"], unique=False
    )
    op.create_table(
        "catalogue_provider_budgets",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("next_request_at", postgresql.TIMESTAMP(precision=3), nullable=True),
        sa.Column("month", sa.String(length=7), nullable=True),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("provider"),
    )
    op.create_table(
        "catalogue_book_editions",
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=False),
        sa.Column("publisher_id", sa.Integer(), nullable=True),
        sa.Column("release_date", sa.String(length=20), nullable=True),
        sa.Column("language", sa.String(length=80), nullable=True),
        sa.Column("format", sa.String(length=100), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.CheckConstraint("entity_id <> work_id", name="ck_catalogue_edition_work"),
        sa.CheckConstraint(
            "pages IS NULL OR pages >= 0", name="ck_catalogue_edition_pages"
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["catalogue_entities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["publisher_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["work_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("entity_id"),
    )
    op.create_index(
        op.f("ix_catalogue_book_editions_work_id"),
        "catalogue_book_editions",
        ["work_id"],
        unique=False,
    )
    op.create_table(
        "catalogue_character_appearances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["character_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["work_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "work_id", name="uq_catalogue_appearance_work"),
        sa.UniqueConstraint(
            "work_id", "character_id", "provider", name="uq_catalogue_appearance"
        ),
    )
    op.create_index(
        "ix_catalogue_appearance_character",
        "catalogue_character_appearances",
        ["character_id", "work_id"],
        unique=False,
    )
    op.create_table(
        "catalogue_game_releases",
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=False),
        sa.Column("platform_namespace", sa.String(length=80), nullable=False),
        sa.Column("platform_id", sa.String(length=200), nullable=False),
        sa.Column("platform_name", sa.String(length=200), nullable=True),
        sa.Column("region", sa.String(length=80), nullable=True),
        sa.Column("release_date", sa.String(length=20), nullable=True),
        sa.CheckConstraint("entity_id <> work_id", name="ck_catalogue_release_work"),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["catalogue_entities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["work_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("entity_id"),
    )
    op.create_index(
        op.f("ix_catalogue_game_releases_work_id"),
        "catalogue_game_releases",
        ["work_id"],
        unique=False,
    )
    op.create_table(
        "catalogue_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("namespace", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(precision=3),
            server_default=sa.text("date_trunc('milliseconds', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "external_id", name="uq_catalogue_identity"),
    )
    op.create_index(
        "ix_catalogue_identity_entity",
        "catalogue_identities",
        ["entity_id"],
        unique=False,
    )
    op.create_table(
        "catalogue_metadata_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "coverage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("fetched_at", postgresql.TIMESTAMP(precision=3), nullable=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(precision=3), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("next_attempt_at", postgresql.TIMESTAMP(precision=3), nullable=True),
        sa.Column("lease_token", sa.String(length=40), nullable=True),
        sa.Column("lease_until", postgresql.TIMESTAMP(precision=3), nullable=True),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "kind", "external_id", name="uq_catalogue_snapshot"
        ),
    )
    op.create_index(
        "ix_catalogue_snapshot_due",
        "catalogue_metadata_snapshots",
        ["next_attempt_at"],
        unique=False,
    )
    op.create_table(
        "catalogue_relationships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.CheckConstraint(
            "relation IN ('dlc','expansion','standalone_expansion','remake','remaster','port','version','series_member','recommendation')",
            name="ck_catalogue_relationship_type",
        ),
        sa.CheckConstraint(
            "source_id <> target_id", name="ck_catalogue_relationship_distinct"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "target_id",
            "relation",
            "provider",
            name="uq_catalogue_relationship",
        ),
    )
    op.create_index(
        "ix_catalogue_relationship_target",
        "catalogue_relationships",
        ["target_id", "relation"],
        unique=False,
    )
    op.create_table(
        "catalogue_show_links",
        sa.Column("show_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["show_id"], ["shows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("show_id"),
    )
    op.create_index(
        op.f("ix_catalogue_show_links_entity_id"),
        "catalogue_show_links",
        ["entity_id"],
        unique=False,
    )
    op.create_table(
        "catalogue_steam_prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=False),
        sa.Column("steam_app_id", sa.String(length=20), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("current", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "historical_low", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("historical_low_at", sa.String(length=80), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column(
            "fetched_at",
            postgresql.TIMESTAMP(precision=3),
            server_default=sa.text("date_trunc('milliseconds', now())"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["work_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_id", "steam_app_id", "country", name="uq_catalogue_steam_price"
        ),
    )
    op.create_table(
        "catalogue_credits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=False),
        sa.Column("contributor_id", sa.Integer(), nullable=False),
        sa.Column("edition_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("role_label", sa.String(length=200), nullable=True),
        sa.Column("character_label", sa.String(length=500), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("source_key", sa.String(length=200), nullable=False),
        sa.Column(
            "scope", sa.String(length=80), server_default="title", nullable=False
        ),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["contributor_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["edition_id"], ["catalogue_book_editions.entity_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["work_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "work_id", name="uq_catalogue_credit_work"),
        sa.UniqueConstraint(
            "work_id", "provider", "source_key", name="uq_catalogue_credit_source"
        ),
    )
    op.create_index(
        "ix_catalogue_credit_contributor_role",
        "catalogue_credits",
        ["contributor_id", "role", "work_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_catalogue_credits_edition_id"),
        "catalogue_credits",
        ["edition_id"],
        unique=False,
    )
    op.create_table(
        "catalogue_legacy_links",
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["catalogue_entities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("media_id"),
    )
    op.create_index(
        op.f("ix_catalogue_legacy_links_entity_id"),
        "catalogue_legacy_links",
        ["entity_id"],
        unique=False,
    )
    op.create_table(
        "catalogue_character_performances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=False),
        sa.Column("credit_id", sa.Integer(), nullable=False),
        sa.Column("appearance_id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=80), server_default="", nullable=False),
        sa.ForeignKeyConstraint(
            ["appearance_id", "work_id"],
            [
                "catalogue_character_appearances.id",
                "catalogue_character_appearances.work_id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["credit_id", "work_id"],
            ["catalogue_credits.id", "catalogue_credits.work_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "credit_id", "appearance_id", "language", name="uq_catalogue_performance"
        ),
    )

    op.execute("""
CREATE FUNCTION catalogue_require_kind(target integer, expected text[]) RETURNS boolean
LANGUAGE sql STABLE AS $$ SELECT EXISTS(SELECT 1 FROM catalogue_entities WHERE id=target AND kind=ANY(expected)) $$
    """)

    op.execute("""
CREATE FUNCTION catalogue_validate_references() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE expected text[];
BEGIN
    IF TG_TABLE_NAME='catalogue_entities' THEN
        IF NEW.kind<>OLD.kind THEN RAISE EXCEPTION 'Canonical entity kind is immutable' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_identities' THEN
        expected := CASE NEW.namespace
            WHEN 'tmdb.movie' THEN ARRAY['movie']::text[]
            WHEN 'tmdb.series' THEN ARRAY['series']::text[]
            WHEN 'tvdb.series' THEN ARRAY['series']::text[]
            WHEN 'imdb.title' THEN ARRAY['movie','series']::text[]
            WHEN 'tmdb.person' THEN ARRAY['person']::text[]
            WHEN 'tvdb.person' THEN ARRAY['person']::text[]
            WHEN 'tmdb.company' THEN ARRAY['organization']::text[]
            WHEN 'tmdb.network' THEN ARRAY['organization']::text[]
            WHEN 'tvdb.company' THEN ARRAY['organization']::text[]
            WHEN 'igdb.game' THEN ARRAY['game']::text[]
            WHEN 'igdb.character' THEN ARRAY['character']::text[]
            WHEN 'igdb.company' THEN ARRAY['organization']::text[]
            WHEN 'igdb.collection' THEN ARRAY['game_collection']::text[]
            WHEN 'igdb.release' THEN ARRAY['release']::text[]
            WHEN 'igdb.game_version' THEN ARRAY['release']::text[]
            WHEN 'rawg.game' THEN ARRAY['game']::text[]
            WHEN 'rawg.developer' THEN ARRAY['organization']::text[]
            WHEN 'rawg.publisher' THEN ARRAY['organization']::text[]
            WHEN 'rawg.release' THEN ARRAY['release']::text[]
            WHEN 'hardcover.book' THEN ARRAY['book']::text[]
            WHEN 'hardcover.author' THEN ARRAY['person']::text[]
            WHEN 'hardcover.publisher' THEN ARRAY['organization']::text[]
            WHEN 'hardcover.character' THEN ARRAY['character']::text[]
            WHEN 'hardcover.edition' THEN ARRAY['edition']::text[]
            WHEN 'hardcover.series' THEN ARRAY['book_series']::text[]
            WHEN 'isbn.10' THEN ARRAY['edition']::text[]
            WHEN 'isbn.13' THEN ARRAY['edition']::text[]
            WHEN 'steam.app' THEN ARRAY['game']::text[]
            WHEN 'itad.game' THEN ARRAY['game']::text[]
            ELSE ARRAY[]::text[] END;
        IF NOT catalogue_require_kind(NEW.entity_id,expected) THEN RAISE EXCEPTION 'Identity namespace/kind mismatch' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_book_editions' THEN
        IF NOT catalogue_require_kind(NEW.entity_id,ARRAY['edition']) OR NOT catalogue_require_kind(NEW.work_id,ARRAY['book'])
           OR (NEW.publisher_id IS NOT NULL AND NOT catalogue_require_kind(NEW.publisher_id,ARRAY['organization']))
        THEN RAISE EXCEPTION 'Invalid book edition references' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_game_releases' THEN
        IF NOT catalogue_require_kind(NEW.entity_id,ARRAY['release']) OR NOT catalogue_require_kind(NEW.work_id,ARRAY['game'])
        THEN RAISE EXCEPTION 'Invalid game release references' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_credits' THEN
        IF NOT catalogue_require_kind(NEW.work_id,ARRAY['movie','series','game','book']) OR NOT catalogue_require_kind(NEW.contributor_id,ARRAY['person','organization'])
           OR (NEW.edition_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM catalogue_book_editions WHERE entity_id=NEW.edition_id AND work_id=NEW.work_id))
        THEN RAISE EXCEPTION 'Invalid contributor references' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_character_appearances' THEN
        IF NOT catalogue_require_kind(NEW.work_id,ARRAY['movie','series','game','book']) OR NOT catalogue_require_kind(NEW.character_id,ARRAY['character'])
        THEN RAISE EXCEPTION 'Invalid fictional character references' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_character_performances' THEN
        IF NOT EXISTS(SELECT 1 FROM catalogue_credits c JOIN catalogue_entities e ON e.id=c.contributor_id
                      WHERE c.id=NEW.credit_id AND c.work_id=NEW.work_id AND e.kind='person' AND c.role IN ('actor','voice_actor','narrator'))
        THEN RAISE EXCEPTION 'Invalid performer references' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_relationships' THEN
        IF NEW.relation='series_member' THEN
            IF NOT ((catalogue_require_kind(NEW.source_id,ARRAY['book']) AND catalogue_require_kind(NEW.target_id,ARRAY['book_series']))
                OR (catalogue_require_kind(NEW.source_id,ARRAY['game']) AND catalogue_require_kind(NEW.target_id,ARRAY['game_collection'])))
            THEN RAISE EXCEPTION 'Invalid series membership' USING ERRCODE='23514'; END IF;
        ELSIF NEW.relation='recommendation' THEN
            IF NOT EXISTS(SELECT 1 FROM catalogue_entities s JOIN catalogue_entities t ON s.kind=t.kind
                          WHERE s.id=NEW.source_id AND t.id=NEW.target_id AND s.kind IN ('movie','series','game','book'))
            THEN RAISE EXCEPTION 'Invalid recommendation references' USING ERRCODE='23514'; END IF;
        ELSIF NOT catalogue_require_kind(NEW.source_id,ARRAY['game']) OR NOT catalogue_require_kind(NEW.target_id,ARRAY['game'])
        THEN RAISE EXCEPTION 'Game relationship requires game works' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_show_links' THEN
        IF NOT catalogue_require_kind(NEW.entity_id,ARRAY['series']) THEN RAISE EXCEPTION 'Show bridge requires a series' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_legacy_links' THEN
        IF NOT EXISTS(SELECT 1 FROM media m JOIN catalogue_entities e ON e.kind=m.media_type::text WHERE m.id=NEW.media_id AND e.id=NEW.entity_id)
        THEN RAISE EXCEPTION 'Legacy bridge kind mismatch' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_steam_prices' THEN
        IF NOT catalogue_require_kind(NEW.work_id,ARRAY['game']) OR NOT EXISTS(SELECT 1 FROM catalogue_identities WHERE namespace='steam.app' AND external_id=NEW.steam_app_id AND entity_id=NEW.work_id)
        THEN RAISE EXCEPTION 'Price requires a verified Steam game identity' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$
    """)
    op.execute(
        "CREATE TRIGGER validate_catalogue_refs BEFORE INSERT OR UPDATE ON catalogue_identities FOR EACH ROW EXECUTE FUNCTION catalogue_validate_references()"
    )
    op.execute(
        "CREATE TRIGGER validate_catalogue_refs BEFORE INSERT OR UPDATE ON catalogue_book_editions FOR EACH ROW EXECUTE FUNCTION catalogue_validate_references()"
    )
    op.execute(
        "CREATE TRIGGER validate_catalogue_refs BEFORE INSERT OR UPDATE ON catalogue_game_releases FOR EACH ROW EXECUTE FUNCTION catalogue_validate_references()"
    )
    op.execute(
        "CREATE TRIGGER validate_catalogue_refs BEFORE INSERT OR UPDATE ON catalogue_credits FOR EACH ROW EXECUTE FUNCTION catalogue_validate_references()"
    )
    op.execute(
        "CREATE TRIGGER validate_catalogue_refs BEFORE INSERT OR UPDATE ON catalogue_character_appearances FOR EACH ROW EXECUTE FUNCTION catalogue_validate_references()"
    )
    op.execute(
        "CREATE TRIGGER validate_catalogue_refs BEFORE INSERT OR UPDATE ON catalogue_character_performances FOR EACH ROW EXECUTE FUNCTION catalogue_validate_references()"
    )
    op.execute(
        "CREATE TRIGGER validate_catalogue_refs BEFORE INSERT OR UPDATE ON catalogue_relationships FOR EACH ROW EXECUTE FUNCTION catalogue_validate_references()"
    )
    op.execute(
        "CREATE TRIGGER validate_catalogue_refs BEFORE INSERT OR UPDATE ON catalogue_show_links FOR EACH ROW EXECUTE FUNCTION catalogue_validate_references()"
    )
    op.execute(
        "CREATE TRIGGER validate_catalogue_refs BEFORE INSERT OR UPDATE ON catalogue_legacy_links FOR EACH ROW EXECUTE FUNCTION catalogue_validate_references()"
    )
    op.execute(
        "CREATE TRIGGER validate_catalogue_refs BEFORE INSERT OR UPDATE ON catalogue_steam_prices FOR EACH ROW EXECUTE FUNCTION catalogue_validate_references()"
    )
    op.execute(
        "CREATE TRIGGER catalogue_kind_immutable BEFORE UPDATE OF kind ON catalogue_entities FOR EACH ROW EXECUTE FUNCTION catalogue_validate_references()"
    )


def downgrade():
    raise RuntimeError("Catalogue downgrade requires a reviewed data-retention plan")
