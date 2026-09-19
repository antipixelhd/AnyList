"""Track the origin and time of the latest title-status decision.

Revision ID: mt009
Revises: mt008
"""

from alembic import op
import sqlalchemy as sa


revision = "mt009"
down_revision = "mt008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracked_entries", sa.Column("status_source", sa.String(length=64), nullable=True))
    op.add_column("tracked_entries", sa.Column("status_changed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("tracked_entries", "status_changed_at")
    op.drop_column("tracked_entries", "status_source")
