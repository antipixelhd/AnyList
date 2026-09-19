"""Durable outbound resets for cloud tracking providers."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "mt010"
down_revision = "mt009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tracking_cloud_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tracking_cloud_actions_user_id", "tracking_cloud_actions", ["user_id"])
    op.create_index("ix_tracking_cloud_actions_provider", "tracking_cloud_actions", ["provider"])


def downgrade():
    op.drop_index("ix_tracking_cloud_actions_provider", table_name="tracking_cloud_actions")
    op.drop_index("ix_tracking_cloud_actions_user_id", table_name="tracking_cloud_actions")
    op.drop_table("tracking_cloud_actions")
