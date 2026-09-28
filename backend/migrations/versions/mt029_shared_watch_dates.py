"""Distinguish title-wide watch dates from episode-specific evidence."""
from alembic import op
import sqlalchemy as sa

revision = 'mt029'
down_revision = 'mt028'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('watch_events', sa.Column('date_shared', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('watch_events', 'date_shared')
