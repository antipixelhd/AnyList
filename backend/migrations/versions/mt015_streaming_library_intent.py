"""Record explicit streaming-library intent and per-connection delivery.

Revision ID: mt015
Revises: mt014
"""
from alembic import op
import sqlalchemy as sa

revision = "mt015"
down_revision = "mt014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "streaming_library_intents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("desired", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "media_id", name="uq_streaming_library_intent"),
    )
    op.create_index("ix_streaming_library_intents_user_id", "streaming_library_intents", ["user_id"])
    op.create_index("ix_streaming_library_intents_media_id", "streaming_library_intents", ["media_id"])
    op.create_table(
        "streaming_library_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("intent_id", sa.Integer(), sa.ForeignKey("streaming_library_intents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("media_server_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("desired", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(200)),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("intent_id", "connection_id", name="uq_streaming_library_delivery"),
    )
    op.create_index("ix_streaming_library_deliveries_intent_id", "streaming_library_deliveries", ["intent_id"])
    op.create_index("ix_streaming_library_deliveries_connection_id", "streaming_library_deliveries", ["connection_id"])


def downgrade():
    op.drop_table("streaming_library_deliveries")
    op.drop_table("streaming_library_intents")
