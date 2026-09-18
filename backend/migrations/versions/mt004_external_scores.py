"""Cache catalog scores from IMDb and Rotten Tomatoes."""
from alembic import op
import sqlalchemy as sa

revision = 'mt004'
down_revision = 'mt003'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('global_settings', sa.Column('mdblist_api_key', sa.String(255), nullable=True))
    op.add_column('media', sa.Column('imdb_rating', sa.Float(), nullable=True))
    op.add_column('media', sa.Column('rt_critic_score', sa.Float(), nullable=True))
    op.add_column('media', sa.Column('rt_audience_score', sa.Float(), nullable=True))
    op.add_column('media', sa.Column('external_scores_updated_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('media', 'external_scores_updated_at')
    op.drop_column('media', 'rt_audience_score')
    op.drop_column('media', 'rt_critic_score')
    op.drop_column('media', 'imdb_rating')
    op.drop_column('global_settings', 'mdblist_api_key')
