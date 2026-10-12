"""Account-wide discovery vote threshold."""
from alembic import op
import sqlalchemy as sa

revision = 'mt046'
down_revision = 'mt045'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user_profiles', sa.Column('minimum_votes', sa.Integer(), nullable=False, server_default='250'))


def downgrade():
    op.drop_column('user_profiles', 'minimum_votes')
