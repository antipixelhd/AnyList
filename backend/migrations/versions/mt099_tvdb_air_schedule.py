"""Cache the TVDB airtime for a tracked series."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'mt099_tvdb_air_schedule'
down_revision = 'mt098_screen_actor_appearances'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('media', sa.Column('tvdb_air_schedule', postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column('media', 'tvdb_air_schedule')
