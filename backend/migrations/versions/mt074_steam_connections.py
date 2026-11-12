"""Steam imports and sync reuse connection identities and account sync jobs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'mt074_steam_connections'
down_revision = 'mt073_game_episode_credits'
branch_labels = None
depends_on = None


def upgrade():
    # Commit the enum before it can be used by jobs in a later transaction.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE collectionsource ADD VALUE IF NOT EXISTS 'steam'")
    op.drop_constraint('ck_msc_type', 'media_server_connections', type_='check')
    op.create_check_constraint('ck_msc_type', 'media_server_connections',
        "type IN ('plex','jellyfin','emby','nuvio','stremio','arvio','steam')")
    op.create_table('steam_connections',
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('media_server_connections.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('steam_id', sa.String(20), nullable=False),
        sa.Column('sync_achievements', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('sync_playtime', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('imported', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('library', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('selection', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('checkpoints', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('draft', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('generation', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('fetched_at', sa.DateTime()),
        sa.UniqueConstraint('user_id', 'steam_id', name='uq_steam_owner_profile'))
    op.create_index('ix_steam_connections_user_id', 'steam_connections', ['user_id'])


def downgrade():
    op.drop_table('steam_connections')
    op.drop_constraint('ck_msc_type', 'media_server_connections', type_='check')
    op.create_check_constraint('ck_msc_type', 'media_server_connections',
        "type IN ('plex','jellyfin','emby','nuvio','stremio','arvio')")
    # PostgreSQL enum members cannot be removed safely while historical jobs exist.
