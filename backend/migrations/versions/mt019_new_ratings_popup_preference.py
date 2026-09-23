"""Add the new ratings popup preference.

Revision ID: mt019
Revises: mt018
"""
from alembic import op
import sqlalchemy as sa


revision = "mt019"
down_revision = "mt018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tracking_preferences",
        sa.Column("show_new_ratings_popup", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade():
    op.drop_column("tracking_preferences", "show_new_ratings_popup")
