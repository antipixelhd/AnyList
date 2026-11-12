"""Join main's vote eligibility and beta's Games migrations.

An existing main database at mt058 runs the Games branch. An existing beta
database at mt066 runs the vote migration. Neither reapplies its existing work.
"""

revision = 'mt067'
down_revision = ('mt058', 'mt066')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
