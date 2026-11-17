"""Keep transactional statistics invalidation without one global write row."""
from alembic import op

revision = 'mt095_metadata_revision_shards'
down_revision = 'mt094_search_demand'
branch_labels = depends_on = None


def upgrade():
    # Preserve the aggregate revision of existing snapshots. A transaction always
    # writes the same shard; SUM observes committed changes at the reader's cutoff,
    # including rollback and commits that finish out of transaction-ID order.
    op.execute('INSERT INTO stats_metadata_revision (id, revision) SELECT id, 0 FROM generate_series(2, 64) id')
    op.execute("""
        CREATE OR REPLACE FUNCTION stats_bump_metadata_revision() RETURNS void LANGUAGE sql AS $$
          UPDATE stats_metadata_revision SET revision = revision + 1
          WHERE id = 1 + (txid_current() % 64)::integer;
        $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION stats_metadata_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM stats_bump_metadata_revision();
          RETURN NULL;
        END $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION stats_visibility_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR (TG_OP = 'UPDATE' AND OLD.show_anime IS DISTINCT FROM NEW.show_anime) THEN
            UPDATE user_stats_state SET active_snapshot_id = NULL, dirty = true, next_due_at = now() AT TIME ZONE 'UTC';
            DELETE FROM user_stats_snapshots;
          END IF;
          PERFORM stats_bump_metadata_revision();
          RETURN NULL;
        END $$;
    """)


def downgrade():
    # Retain the aggregate cutoff if the application is rolled back as well.
    op.execute('LOCK TABLE stats_metadata_revision IN ACCESS EXCLUSIVE MODE')
    op.execute('UPDATE stats_metadata_revision SET revision = (SELECT sum(revision) FROM stats_metadata_revision) WHERE id = 1')
    op.execute('DELETE FROM stats_metadata_revision WHERE id <> 1')
    op.execute("""
        CREATE OR REPLACE FUNCTION stats_bump_metadata_revision() RETURNS void LANGUAGE sql AS $$
          UPDATE stats_metadata_revision SET revision = revision + 1 WHERE id = 1;
        $$;
    """)
