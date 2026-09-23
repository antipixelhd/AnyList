"""Add durable receipts for AnyList tracking API writes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "mt020"
down_revision = "mt019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tracking_delivery_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("detail", sa.String(240)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tracking_delivery_jobs_user_id", "tracking_delivery_jobs", ["user_id"])
    op.create_index("ix_tracking_delivery_jobs_media_id", "tracking_delivery_jobs", ["media_id"])


def downgrade():
    op.drop_table("tracking_delivery_jobs")
