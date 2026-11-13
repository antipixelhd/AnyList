"""Refresh existing season cards through durable, bounded reconciliation."""
from alembic import op

revision = 'mt090_season_activity_context'
down_revision = 'mt089_link_series_start'
branch_labels = depends_on = None


def upgrade():
    op.execute("""INSERT INTO season_reconcile_jobs (user_id, media_id)
        SELECT e.user_id, e.media_id FROM tracked_entries e JOIN media m ON m.id=e.media_id
        WHERE m.media_type='series' ON CONFLICT DO NOTHING""")


def downgrade():
    pass  # Receipts and backward-compatible JSON payloads require no reversal.
