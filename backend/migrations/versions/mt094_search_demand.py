"""Keep active search demand separate from shared verification jobs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'mt094_search_demand'
down_revision = 'mt093_playable_bundles'
branch_labels = depends_on = None


def upgrade():
    op.create_table('search_demand',
        sa.Column('subscriber', sa.String(64), primary_key=True),
        sa.Column('owner', sa.String(80), nullable=False),
        sa.Column('generation', sa.BigInteger(), nullable=False),
        sa.Column('query', sa.String(64), nullable=False),
        sa.Column('targets', postgresql.JSONB(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False))
    op.create_index('ix_search_demand_expiry', 'search_demand', ['expires_at'])
    op.create_index('ix_search_demand_targets', 'search_demand', ['targets'], postgresql_using='gin')


def downgrade():
    op.drop_table('search_demand')
