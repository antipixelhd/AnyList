"""Independent movie/series tracking, activity and deletion markers."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "mt001"
down_revision = "condhist391"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tracked_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="planning"),
        sa.Column("rating_mode", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("manual_score", sa.Float()),
        sa.Column("season_scores", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("favorite", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text()),
        sa.Column("start_date", sa.Date()),
        sa.Column("finish_date", sa.Date()),
        sa.Column("rewatch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "media_id", name="uq_tracked_user_media"),
    )
    op.create_table(
        "tracking_activity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "tracking_deletions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("pending_connections", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("user_id", "media_id", name="uq_tracking_deletion"),
    )
    for table in ("tracked_entries", "tracking_activity", "tracking_deletions"):
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])
    for table in ("tracked_entries", "tracking_activity"):
        op.create_index(f"ix_{table}_media_id", table, ["media_id"])


def downgrade():
    for table in ("tracking_deletions", "tracking_activity", "tracked_entries"):
        op.drop_table(table)
