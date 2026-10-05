"""Gate first media-server imports until library selection is saved.

Revision ID: mt035
Revises: mt034
"""
from alembic import op
import sqlalchemy as sa

revision = "mt035"
down_revision = "mt034"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media_server_connections", sa.Column(
        "libraries_confirmed", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column("media_server_connections", "libraries_confirmed")
