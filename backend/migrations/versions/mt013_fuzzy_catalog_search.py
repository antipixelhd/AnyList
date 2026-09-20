"""Add trigram support for typo-tolerant catalog search.

Revision ID: mt013
Revises: mt012
"""
from alembic import op

revision = "mt013"
down_revision = "mt012"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX ix_media_title_trgm ON media USING gin (title gin_trgm_ops)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_media_title_trgm")
    # Keep the shared extension: other indexes or applications may use it.
