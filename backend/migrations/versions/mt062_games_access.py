"""Owner/admin approved Games access; preferences never grant access."""
from alembic import op
import sqlalchemy as sa

revision = 'mt062'
down_revision = 'mt061'
branch_labels = depends_on = None


def upgrade():
    op.add_column('users', sa.Column('games_access', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('games_approved_by', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('games_approved_at', sa.DateTime(), nullable=True))
    op.create_foreign_key('fk_users_games_approver', 'users', 'users', ['games_approved_by'], ['id'], ondelete='SET NULL')


def downgrade():
    op.drop_constraint('fk_users_games_approver', 'users', type_='foreignkey')
    for name in ('games_approved_at', 'games_approved_by', 'games_access'):
        op.drop_column('users', name)
