"""Align Games approval dates with the shared millisecond timestamp contract.

Existing sub-millisecond digits are truncated, consistently with mt030 and ORM
bindings. Downgrading widens the column but cannot restore discarded digits.
"""
from alembic import op

revision = 'mt068'
down_revision = 'mt067'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE users ALTER COLUMN games_approved_at TYPE timestamp(3) "
        "without time zone USING date_trunc('milliseconds', games_approved_at)")


def downgrade():
    op.execute('ALTER TABLE users ALTER COLUMN games_approved_at TYPE timestamp(6) without time zone')
