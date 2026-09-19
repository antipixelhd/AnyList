"""Notification lifecycle, combined lists, provider ignores, and anime visibility."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "mt007"
down_revision = "mt006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tracking_preferences", sa.Column("combine_lists", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("tracking_preferences", sa.Column("low_priority_notifications", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("tracking_preferences", sa.Column("low_priority_retention_days", sa.Integer(), nullable=False, server_default="7"))
    op.add_column("tracking_reviews", sa.Column("priority", sa.String(length=16), nullable=False, server_default="low"))
    op.add_column("tracking_reviews", sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"))
    op.add_column("tracking_reviews", sa.Column("seen_at", sa.DateTime(), nullable=True))
    op.add_column("tracking_reviews", sa.Column("dismissed_at", sa.DateTime(), nullable=True))
    op.add_column("global_settings", sa.Column("show_anime", sa.Boolean(), nullable=False, server_default="false"))
    op.create_table(
        "tracking_provider_ignores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("external_key", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "provider", "external_key", name="uq_tracking_provider_ignore"),
    )
    op.create_index("ix_tracking_provider_ignores_user_id", "tracking_provider_ignores", ["user_id"])


def downgrade():
    op.drop_index("ix_tracking_provider_ignores_user_id", table_name="tracking_provider_ignores")
    op.drop_table("tracking_provider_ignores")
    op.drop_column("global_settings", "show_anime")
    for column in ("dismissed_at", "seen_at", "payload", "priority"):
        op.drop_column("tracking_reviews", column)
    for column in ("low_priority_retention_days", "low_priority_notifications", "combine_lists"):
        op.drop_column("tracking_preferences", column)
