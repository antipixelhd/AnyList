"""Durable streaming playback actions with independent acknowledgments."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt003'
down_revision = 'mt002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('tracking_stream_actions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('media_server_connections.id', ondelete='CASCADE'), nullable=False),
        sa.Column('media_id', sa.Integer(), sa.ForeignKey('media.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action', sa.String(24), nullable=False),
        sa.Column('payload', JSONB(), nullable=False, server_default='{}'),
        sa.Column('state', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.String(200)),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_index('ix_tracking_stream_actions_user_id', 'tracking_stream_actions', ['user_id'])
    op.create_index('ix_tracking_stream_actions_connection_id', 'tracking_stream_actions', ['connection_id'])


def downgrade():
    op.drop_table('tracking_stream_actions')
