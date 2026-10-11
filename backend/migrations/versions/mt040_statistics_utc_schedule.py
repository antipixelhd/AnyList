"""Make statistics defaults and trigger schedules independent of DB timezone."""
from alembic import op

revision = "mt040"
down_revision = "mt039"
branch_labels = None
depends_on = None


def upgrade():
    clock = "date_trunc('milliseconds', now() AT TIME ZONE 'UTC')"
    op.execute(f"ALTER TABLE user_stats_snapshots ALTER COLUMN computed_at SET DEFAULT {clock}")
    op.execute(f"ALTER TABLE user_stats_state ALTER COLUMN next_due_at SET DEFAULT {clock}")
    op.execute("""
        CREATE OR REPLACE FUNCTION stats_mark_user(target integer, purge boolean) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
          INSERT INTO user_stats_state (user_id, source_revision, dirty, next_due_at)
            SELECT id, 1, true, now() AT TIME ZONE 'UTC' FROM users WHERE id = target
          ON CONFLICT (user_id) DO UPDATE SET
            source_revision = user_stats_state.source_revision + 1, dirty = true,
            active_snapshot_id = CASE WHEN purge THEN NULL ELSE user_stats_state.active_snapshot_id END,
            next_due_at = CASE WHEN purge THEN now() AT TIME ZONE 'UTC' ELSE user_stats_state.next_due_at END;
          IF purge THEN DELETE FROM user_stats_snapshots WHERE user_id = target; END IF;
        END $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION stats_visibility_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR (TG_OP = 'UPDATE' AND OLD.show_anime IS DISTINCT FROM NEW.show_anime) THEN
            UPDATE user_stats_state SET active_snapshot_id = NULL, dirty = true, next_due_at = now() AT TIME ZONE 'UTC';
            DELETE FROM user_stats_snapshots;
          END IF;
          UPDATE stats_metadata_revision SET revision = revision + 1 WHERE id = 1;
          RETURN NULL;
        END $$;
    """)
    # Only unclaimed users without a published generation need initial repair.
    op.execute(f"UPDATE user_stats_state SET next_due_at = {clock} WHERE active_snapshot_id IS NULL AND lease_token IS NULL")


def downgrade():
    raise RuntimeError("Statistics UTC scheduling is a correctness fix and must be retained")
