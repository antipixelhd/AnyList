"""Store independent season statuses and the latest playback target."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt101_independent_seasons'
down_revision = 'mt100_manual_season_completion'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tracked_entries', sa.Column('season_list_statuses', JSONB(), nullable=True))
    op.add_column('tracked_entries', sa.Column('latest_episode_activity', JSONB(), nullable=True))


def downgrade():
    op.drop_column('tracked_entries', 'latest_episode_activity')
    op.drop_column('tracked_entries', 'season_list_statuses')
