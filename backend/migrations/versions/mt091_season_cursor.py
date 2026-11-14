"""Remember explicit season boundaries independently of cumulative progress."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt091_season_cursor'
down_revision = 'mt090_season_activity_context'
branch_labels = depends_on = None


def upgrade():
    op.add_column('tracked_entries', sa.Column('season_cursor', JSONB(), nullable=True))


def downgrade():
    op.drop_column('tracked_entries', 'season_cursor')
