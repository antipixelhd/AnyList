"""Keep favorites independently of list status, preserving existing hearts."""
from alembic import op
import sqlalchemy as sa

revision = 'mt044'
down_revision = 'mt043'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('media_favorites',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('media_id', sa.Integer(), sa.ForeignKey('media.id', ondelete='CASCADE'), primary_key=True))
    op.execute('INSERT INTO media_favorites (user_id, media_id) SELECT user_id, media_id FROM tracked_entries WHERE favorite IS TRUE')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM media_favorites f LEFT JOIN tracked_entries e ON e.user_id=f.user_id AND e.media_id=f.media_id WHERE e.favorite IS NOT TRUE)')):
        raise RuntimeError('Independent favorites must be preserved before downgrading')
    op.drop_table('media_favorites')
