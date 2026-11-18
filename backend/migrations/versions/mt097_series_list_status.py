"""Persist a viewer's decision to move the whole series."""
from alembic import op
import sqlalchemy as sa

revision = 'mt097_series_list_status'
down_revision = 'mt096_browse_ranking_indexes'
branch_labels = depends_on = None


def upgrade():
    op.add_column('tracked_entries', sa.Column('series_list_status', sa.String(16)))


def downgrade():
    op.drop_column('tracked_entries', 'series_list_status')
