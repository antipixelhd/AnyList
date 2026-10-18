"""Restore the browse vote floor without applying it to relevance searches."""
from alembic import op
import sqlalchemy as sa

revision = 'mt056'
down_revision = 'mt055'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user_profiles', sa.Column('minimum_votes', sa.Integer(), nullable=False, server_default='250'))
    op.execute("""UPDATE user_settings SET preferences = preferences - 'require_release' - 'require_poster'
        WHERE preferences ?| ARRAY['require_release', 'require_poster']""")


def downgrade():
    op.drop_column('user_profiles', 'minimum_votes')
