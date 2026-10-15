"""Hide Specials season cards by default without removing season data."""
from alembic import op
import sqlalchemy as sa

revision = 'mt051'
down_revision = 'mt050'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tracking_preferences', sa.Column(
        'remove_specials_seasons', sa.Boolean(), nullable=False, server_default=sa.true(),
    ))


def downgrade():
    op.drop_column('tracking_preferences', 'remove_specials_seasons')
