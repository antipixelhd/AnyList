"""Restore the 250-vote default alongside alternative popularity eligibility."""
from alembic import op
import sqlalchemy as sa

revision = 'mt058'
down_revision = 'mt057'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('user_profiles', 'minimum_votes', server_default='250', existing_type=sa.Integer())
    # mt057 disabled this default for every account. Restore it without replacing
    # customized positive thresholds; zero remains available after this migration.
    op.execute('UPDATE user_profiles SET minimum_votes = 250 WHERE minimum_votes = 0')


def downgrade():
    op.alter_column('user_profiles', 'minimum_votes', server_default='0', existing_type=sa.Integer())
