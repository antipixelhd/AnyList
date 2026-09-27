"""Durable per-connection watched state delivery for Nuvio and Stremio."""
from alembic import op
import sqlalchemy as sa


revision = "mt026"
down_revision = "mt025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tracking_watch_intents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("media_server_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("desired_watched", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(200)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "media_id", name="uq_tracking_watch_intent_connection_media"),
    )
    op.create_index("ix_tracking_watch_intents_user_id", "tracking_watch_intents", ["user_id"])
    op.create_index("ix_tracking_watch_intents_connection_id", "tracking_watch_intents", ["connection_id"])


def downgrade():
    op.drop_table("tracking_watch_intents")
