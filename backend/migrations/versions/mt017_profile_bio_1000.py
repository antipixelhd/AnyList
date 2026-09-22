"""Allow longer Markdown profile bios.

Revision ID: mt017
Revises: mt016
"""
from alembic import op
import sqlalchemy as sa

revision = "mt017"
down_revision = "mt016"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("user_profiles", "bio", existing_type=sa.String(280), type_=sa.String(1000), existing_nullable=True)


def downgrade():
    # A longer saved bio must be shortened before rolling back this migration.
    op.alter_column("user_profiles", "bio", existing_type=sa.String(1000), type_=sa.String(280), existing_nullable=True)
