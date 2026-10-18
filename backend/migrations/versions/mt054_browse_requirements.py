"""Replace discovery vote thresholds and ranking switches with browse requirements."""
from alembic import op
import sqlalchemy as sa

revision = 'mt054'
down_revision = 'mt053'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column('user_profiles', 'minimum_votes')
    op.execute("""UPDATE user_settings SET preferences = preferences
        - 'prefer_streaming_series' - 'prefer_theatrical_movies'
        WHERE preferences ?| ARRAY['prefer_streaming_series', 'prefer_theatrical_movies']""")


def downgrade():
    op.add_column('user_profiles', sa.Column('minimum_votes', sa.Integer(), nullable=False, server_default='250'))
    op.execute("""UPDATE user_settings SET preferences = preferences
        - 'require_release' - 'require_poster'
        WHERE preferences ?| ARRAY['require_release', 'require_poster']""")
