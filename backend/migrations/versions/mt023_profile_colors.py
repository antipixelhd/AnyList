"""Store each member's profile accent and private site-wide preference.

Revision ID: mt023
Revises: mt022
"""
from alembic import op
import sqlalchemy as sa


revision = "mt023"
down_revision = "mt022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_profiles",
        sa.Column("profile_color", sa.String(7), nullable=False, server_default="#3db4f2"),
    )
    op.add_column(
        "user_profiles",
        sa.Column("apply_site_wide", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("user_profiles", "apply_site_wide")
    op.drop_column("user_profiles", "profile_color")
