"""Store explicit season completion independently of catalogue membership."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt100_manual_season_completion'
down_revision = 'mt099_tvdb_air_schedule'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tracked_entries', sa.Column('manual_season_completions', JSONB(), nullable=True))


def downgrade():
    op.drop_column('tracked_entries', 'manual_season_completions')
