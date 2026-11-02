"""Nonblocking Steam progress and the post-import new-game boundary."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'mt075_steam_progress'
down_revision = 'mt074_steam_connections'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('steam_connections', sa.Column('auto_add_games', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('steam_connections', sa.Column('timezone', sa.String(80), nullable=False, server_default='UTC'))
    op.add_column('steam_connections', sa.Column('known_apps', postgresql.JSONB(), nullable=False, server_default='[]'))
    op.create_table('steam_player_games',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('steam_id', sa.String(20), primary_key=True),
        sa.Column('app_id', sa.String(20), primary_key=True),
        sa.Column('achievement_count', sa.Integer()), sa.Column('achievement_total', sa.Integer()),
        sa.Column('observed_playtime', sa.Integer()), sa.Column('observed_last_played', sa.BigInteger()), sa.Column('fetched_at', sa.DateTime()),
        sa.Column('retry_at', sa.DateTime()), sa.Column('lease_token', sa.String(40)), sa.Column('lease_until', sa.DateTime()))


def downgrade():
    op.drop_table('steam_player_games')
    for name in ('known_apps', 'timezone', 'auto_add_games'):
        op.drop_column('steam_connections', name)
