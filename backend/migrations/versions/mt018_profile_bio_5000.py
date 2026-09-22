"""Allow profile bios up to 5,000 Markdown characters.

Revision ID: mt018
Revises: mt017
"""
from alembic import op
import sqlalchemy as sa


revision = "mt018"
down_revision = "mt017"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "user_profiles",
        "bio",
        existing_type=sa.String(1000),
        type_=sa.String(5000),
        existing_nullable=True,
    )


def downgrade():
    # Longer bios must be shortened before downgrading this column.
    op.alter_column(
        "user_profiles",
        "bio",
        existing_type=sa.String(5000),
        type_=sa.String(1000),
        existing_nullable=True,
    )
