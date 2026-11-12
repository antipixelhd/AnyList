"""Private one-way Steam wishlist observations and managed planning entries."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt082_steam_wishlist'
down_revision = 'mt081_game_statistics_totals'
branch_labels = depends_on = None


def upgrade():
    op.add_column('steam_connections', sa.Column('sync_wishlist', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('steam_connections', sa.Column('wishlist', JSONB(), nullable=True))
    op.add_column('steam_connections', sa.Column('wishlist_state', JSONB(), nullable=False, server_default='{}'))


def downgrade():
    for name in ('wishlist_state', 'wishlist', 'sync_wishlist'):
        op.drop_column('steam_connections', name)
