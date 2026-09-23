"""Remember initially imported Completed entries for rating activity grace.

Revision ID: mt021
Revises: mt020
"""
from alembic import op
import sqlalchemy as sa

revision = "mt021"
down_revision = "mt020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tracked_entries", sa.Column("initial_import_completed_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("tracked_entries", "initial_import_completed_at")
