"""Persist actor-scoped characters and protect character/performer references."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "mt042"
down_revision = "mt041"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("catalogue_screen_characters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("catalogue_entities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role_key", sa.String(500), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("actor_id", "role_key", name="uq_screen_character_actor_role"),
        sa.UniqueConstraint("id", "actor_id", name="uq_screen_character_actor"))
    op.add_column("catalogue_credits", sa.Column("screen_character_id", sa.Integer()))
    op.create_index("ix_catalogue_credits_screen_character_id", "catalogue_credits", ["screen_character_id"])
    op.create_foreign_key("fk_credit_screen_character_actor", "catalogue_credits", "catalogue_screen_characters",
        ["screen_character_id", "contributor_id"], ["id", "actor_id"], ondelete="RESTRICT")
    op.execute("""CREATE FUNCTION screen_character_validate() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_TABLE_NAME = 'catalogue_screen_characters' THEN
          IF NOT EXISTS(SELECT 1 FROM catalogue_entities WHERE id=NEW.actor_id AND kind='person') THEN
            RAISE EXCEPTION 'Screen character requires a person' USING ERRCODE='23514';
          END IF;
        ELSE
          -- A role link is derived. Direct corrections to a credit detach its
          -- old mapping; explicitly supplied inconsistent links still fail.
          IF TG_OP = 'UPDATE' THEN
            IF NEW.screen_character_id IS NOT DISTINCT FROM OLD.screen_character_id
               AND (NEW.role, NEW.contributor_id, NEW.work_id) IS DISTINCT FROM (OLD.role, OLD.contributor_id, OLD.work_id) THEN
              NEW.screen_character_id := NULL;
            END IF;
          END IF;
          IF NEW.screen_character_id IS NOT NULL THEN
            IF NEW.role <> 'actor' OR NOT EXISTS(SELECT 1 FROM catalogue_entities WHERE id=NEW.work_id AND kind IN ('movie','series')) THEN
              RAISE EXCEPTION 'Screen character requires a screen acting credit' USING ERRCODE='23514';
            END IF;
          END IF;
        END IF;
        RETURN NEW;
      END $$;""")
    for table in ('catalogue_screen_characters', 'catalogue_credits'):
        op.execute(f"CREATE TRIGGER screen_character_validate BEFORE INSERT OR UPDATE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION screen_character_validate()")
    op.execute("""DO $$ BEGIN
        IF to_regprocedure('stats_metadata_changed()') IS NOT NULL THEN
          CREATE TRIGGER stats_metadata_changed AFTER INSERT OR UPDATE OR DELETE ON catalogue_screen_characters
            FOR EACH STATEMENT EXECUTE FUNCTION stats_metadata_changed();
        END IF;
      END $$;""")


def downgrade():
    raise RuntimeError("Screen character mappings require reviewed data retention before downgrade")
