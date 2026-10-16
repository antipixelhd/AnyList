"""Movie runtime and category result filters."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt047'
down_revision = 'mt046'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user_profiles', sa.Column('minimum_movie_runtime', sa.Integer(), nullable=False, server_default='30'))
    op.add_column('user_profiles', sa.Column('excluded_categories', JSONB(), nullable=False, server_default='[]'))


def downgrade():
    op.drop_column('user_profiles', 'excluded_categories')
    op.drop_column('user_profiles', 'minimum_movie_runtime')
