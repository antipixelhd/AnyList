"""Durable manual matches for provider imports."""
from alembic import op
import sqlalchemy as sa

revision = "mt008"
down_revision = "mt007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tracking_provider_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("external_key", sa.String(length=255), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "provider", "external_key", name="uq_tracking_provider_match"),
    )
    op.create_index("ix_tracking_provider_matches_user_id", "tracking_provider_matches", ["user_id"])


def downgrade():
    op.drop_index("ix_tracking_provider_matches_user_id", table_name="tracking_provider_matches")
    op.drop_table("tracking_provider_matches")
