"""Persist resumable Netflix viewing-history import drafts.

Revision ID: mt024
Revises: mt023
Create Date: 2026-09-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "mt024"
down_revision = "mt023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netflix_import_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="preparing", nullable=False),
        sa.Column("phase", sa.String(length=16), server_default="parse", nullable=False),
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_csv", sa.LargeBinary(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_netflix_import_user_idempotency"),
    )
    op.create_index("ix_netflix_import_sessions_user_id", "netflix_import_sessions", ["user_id"])
    op.create_index("ix_netflix_import_sessions_expires_at", "netflix_import_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_netflix_import_sessions_expires_at", table_name="netflix_import_sessions")
    op.drop_index("ix_netflix_import_sessions_user_id", table_name="netflix_import_sessions")
    op.drop_table("netflix_import_sessions")
