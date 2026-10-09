"""Durable overview generations, scheduling and transactional invalidation."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "mt039"
down_revision = "mt038"
branch_labels = None
depends_on = None


def upgrade():
    # Freeze the schema here; migrations must not depend on future model imports.
    timestamp = postgresql.TIMESTAMP(precision=3)
    clock = sa.text("date_trunc('milliseconds', now())")
    op.create_table("stats_metadata_revision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"))
    op.create_table("user_stats_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_revision", sa.BigInteger(), nullable=False),
        sa.Column("metadata_revision", sa.BigInteger(), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("computed_at", timestamp, nullable=False, server_default=clock),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("id", "user_id", name="uq_stats_snapshot_owner"))
    op.create_index("ix_user_stats_snapshots_user_id", "user_stats_snapshots", ["user_id"])
    op.create_table("user_stats_state",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("dirty", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("next_due_at", timestamp, nullable=False, server_default=clock),
        sa.Column("active_snapshot_id", sa.Integer()),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", timestamp),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(32)),
        sa.Column("last_attempt_at", timestamp),
        sa.ForeignKeyConstraint(["active_snapshot_id", "user_id"], ["user_stats_snapshots.id", "user_stats_snapshots.user_id"], name="fk_stats_active_owner", deferrable=True, initially="DEFERRED"))
    op.create_index("ix_user_stats_state_next_due_at", "user_stats_state", ["next_due_at"])
    op.execute("INSERT INTO stats_metadata_revision (id, revision) VALUES (1, 0)")
    op.execute("INSERT INTO user_stats_state (user_id) SELECT id FROM users")
    op.execute("""
        CREATE FUNCTION stats_mark_user(target integer, purge boolean) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
          -- Cascading user deletion must not resurrect state for a deleted owner.
          INSERT INTO user_stats_state (user_id, source_revision, dirty, next_due_at)
            SELECT id, 1, true, now() FROM users WHERE id = target
          ON CONFLICT (user_id) DO UPDATE SET
            source_revision = user_stats_state.source_revision + 1, dirty = true,
            active_snapshot_id = CASE WHEN purge THEN NULL ELSE user_stats_state.active_snapshot_id END,
            next_due_at = CASE WHEN purge THEN now() ELSE user_stats_state.next_due_at END;
          IF purge THEN
            DELETE FROM user_stats_snapshots WHERE user_id = target;
          END IF;
        END $$;
    """)
    op.execute("""
        CREATE FUNCTION stats_user_fact_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE purge boolean := false;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            PERFORM stats_mark_user(OLD.user_id, true);
            RETURN OLD;
          ELSIF TG_OP = 'UPDATE' THEN
            IF OLD.user_id <> NEW.user_id OR OLD.media_id <> NEW.media_id THEN
              PERFORM stats_mark_user(OLD.user_id, true);
            END IF;
            IF TG_TABLE_NAME = 'watch_events' THEN
              purge := OLD.completed AND (NOT NEW.completed OR NEW.play_count < OLD.play_count
                OR (OLD.watched_at IS NOT NULL AND NEW.watched_at IS NULL));
            END IF;
            IF to_jsonb(OLD) - 'updated_at' = to_jsonb(NEW) - 'updated_at' THEN RETURN NEW; END IF;
          END IF;
          PERFORM stats_mark_user(NEW.user_id, purge);
          RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE FUNCTION stats_metadata_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          UPDATE stats_metadata_revision SET revision = revision + 1 WHERE id = 1;
          RETURN NULL;
        END $$;
    """)
    op.execute("""
        CREATE FUNCTION stats_visibility_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR (TG_OP = 'UPDATE' AND OLD.show_anime IS DISTINCT FROM NEW.show_anime) THEN
            UPDATE user_stats_state SET active_snapshot_id = NULL, dirty = true, next_due_at = now();
            DELETE FROM user_stats_snapshots;
          END IF;
          UPDATE stats_metadata_revision SET revision = revision + 1 WHERE id = 1;
          RETURN NULL;
        END $$;
    """)
    op.execute("""
        CREATE FUNCTION stats_new_user() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          INSERT INTO user_stats_state (user_id) VALUES (NEW.id) ON CONFLICT DO NOTHING;
          RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER stats_new_user AFTER INSERT ON users
          FOR EACH ROW EXECUTE FUNCTION stats_new_user();
    """)
    for table in ("tracked_entries", "watch_events"):
        op.execute(f"CREATE TRIGGER stats_user_fact_changed AFTER INSERT OR UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION stats_user_fact_changed()")
    for table in ("media", "shows", "catalogue_entities", "catalogue_identities", "catalogue_legacy_links", "catalogue_show_links"):
        op.execute(f"CREATE TRIGGER stats_metadata_changed AFTER INSERT OR UPDATE OR DELETE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION stats_metadata_changed()")
    op.execute("CREATE TRIGGER stats_visibility_changed AFTER INSERT OR UPDATE OR DELETE ON global_settings "
               "FOR EACH ROW EXECUTE FUNCTION stats_visibility_changed()")


def downgrade():
    raise RuntimeError("Statistics retention must be reviewed before removing published snapshots")
