"""Require cloud tracker reconciliation before outbound writes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt005'
down_revision = 'mt004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tracking_reviews', sa.Column('provider', sa.String(24), nullable=True))
    op.create_table(
        'tracking_cloud_baselines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', sa.String(24), nullable=False),
        sa.Column('snapshot', JSONB(), nullable=False, server_default='{}'),
        sa.Column('approved', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('observed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'provider', name='uq_tracking_cloud_user_provider'),
    )
    op.create_index('ix_tracking_cloud_baselines_user_id', 'tracking_cloud_baselines', ['user_id'])


def downgrade():
    op.drop_table('tracking_cloud_baselines')
    op.drop_column('tracking_reviews', 'provider')
