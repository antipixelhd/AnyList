"""Add mergeable daily activity details.

Revision ID: mt014
Revises: mt013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "mt014"
down_revision = "mt013"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("tracking_activity", sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))

def downgrade():
    op.drop_column("tracking_activity", "payload")
