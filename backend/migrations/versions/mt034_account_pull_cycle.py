"""Account-wide pull schedule and single active cycle.

Revision ID: mt034
Revises: mt033
"""
from alembic import op
import sqlalchemy as sa

revision = "mt034"
down_revision = "mt033"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_settings", sa.Column("pull_sync_interval", sa.Float(), nullable=True))
    # Preserve the fastest existing cadence, without enabling disabled accounts.
    op.execute("""
        UPDATE user_settings AS s SET pull_sync_interval = (
            SELECT min(interval) FROM (
                SELECT s.trakt_auto_sync_interval AS interval
                UNION ALL SELECT s.simkl_auto_sync_interval
                UNION ALL SELECT s.mdblist_auto_sync_interval
                UNION ALL SELECT c.auto_sync_interval FROM media_server_connections c
                    WHERE c.user_id = s.user_id
            ) intervals WHERE interval > 0
        )
    """)
    op.create_index("uq_sync_jobs_active_account_cycle", "sync_jobs", ["user_id"], unique=True,
                    postgresql_where=sa.text("job_type = 'pull_cycle' AND status IN ('pending', 'running')"))


def downgrade():
    op.drop_index("uq_sync_jobs_active_account_cycle", table_name="sync_jobs")
    op.drop_column("user_settings", "pull_sync_interval")
