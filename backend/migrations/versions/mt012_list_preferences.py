"""Add default list sorting.

Revision ID: mt012
Revises: mt011
"""
from alembic import op
import sqlalchemy as sa

revision = "mt012"
down_revision = "mt011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tracking_preferences", sa.Column("default_sort", sa.String(length=16), nullable=False, server_default="title"))


def downgrade():
    op.drop_column("tracking_preferences", "default_sort")
