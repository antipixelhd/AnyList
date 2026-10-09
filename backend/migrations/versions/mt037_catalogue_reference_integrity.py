"""Keep catalogue references valid when their parent records change.

Revision ID: mt037
Revises: mt036
"""

from alembic import op
import sqlalchemy as sa

revision = "mt037"
down_revision = "mt036"
branch_labels = None
depends_on = None


def upgrade():
    # Additive reference constraints validate retained rows; never discard data.
    op.create_unique_constraint(
        "uq_catalogue_identity_reference",
        "catalogue_identities",
        ["entity_id", "namespace", "external_id"],
    )
    op.add_column(
        "catalogue_steam_prices",
        sa.Column(
            "identity_namespace",
            sa.String(80),
            nullable=False,
            server_default="steam.app",
        ),
    )
    op.create_check_constraint(
        "ck_catalogue_price_namespace",
        "catalogue_steam_prices",
        "identity_namespace = 'steam.app'",
    )
    op.create_foreign_key(
        "fk_catalogue_price_steam_identity",
        "catalogue_steam_prices",
        "catalogue_identities",
        ["work_id", "identity_namespace", "steam_app_id"],
        ["entity_id", "namespace", "external_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_catalogue_edition_work",
        "catalogue_book_editions",
        ["entity_id", "work_id"],
    )
    op.create_foreign_key(
        "fk_catalogue_credit_edition_work",
        "catalogue_credits",
        "catalogue_book_editions",
        ["edition_id", "work_id"],
        ["entity_id", "work_id"],
        ondelete="RESTRICT",
    )
    op.execute("""
        CREATE FUNCTION catalogue_lock_performer_credit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- SHARE conflicts with a concurrent role/contributor update. The
            -- parent guard below protects existing links in the other direction.
            PERFORM 1 FROM catalogue_credits c
                JOIN catalogue_entities e ON e.id=c.contributor_id
                WHERE c.id=NEW.credit_id AND c.work_id=NEW.work_id
                    AND e.kind='person' AND c.role IN ('actor','voice_actor','narrator')
                FOR SHARE OF c;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Invalid performer references' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION catalogue_guard_performer_credit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS(SELECT 1 FROM catalogue_character_performances WHERE credit_id=OLD.id)
               AND (NEW.role NOT IN ('actor','voice_actor','narrator')
                    OR NOT catalogue_require_kind(NEW.contributor_id,ARRAY['person'])) THEN
                RAISE EXCEPTION 'Linked performer credit requires review before changing role or contributor'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER catalogue_lock_performer_credit
        BEFORE INSERT OR UPDATE ON catalogue_character_performances
        FOR EACH ROW EXECUTE FUNCTION catalogue_lock_performer_credit()
    """)
    op.execute("""
        CREATE TRIGGER catalogue_guard_performer_credit
        BEFORE UPDATE OF role, contributor_id ON catalogue_credits
        FOR EACH ROW EXECUTE FUNCTION catalogue_guard_performer_credit()
    """)
    # Refuse inconsistent pre-existing performer links rather than deleting them.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(
                SELECT 1 FROM catalogue_character_performances p
                JOIN catalogue_credits c ON c.id=p.credit_id
                JOIN catalogue_entities e ON e.id=c.contributor_id
                WHERE e.kind <> 'person' OR c.role NOT IN ('actor','voice_actor','narrator')
            ) THEN
                RAISE EXCEPTION 'Existing performer links require review' USING ERRCODE='23514';
            END IF;
        END $$
    """)


def downgrade():
    raise RuntimeError("Catalogue downgrade requires a reviewed data-retention plan")
