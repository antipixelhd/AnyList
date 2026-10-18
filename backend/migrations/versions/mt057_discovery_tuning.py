"""Disable vote gating during discovery evaluation; keep the optional control."""
from alembic import op
import sqlalchemy as sa
revision = 'mt057'
down_revision = 'mt056'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('user_profiles', 'minimum_votes', server_default='0', existing_type=sa.Integer())
    op.execute('UPDATE user_profiles SET minimum_votes = 0')


def downgrade():
    op.alter_column('user_profiles', 'minimum_votes', server_default='250', existing_type=sa.Integer())
