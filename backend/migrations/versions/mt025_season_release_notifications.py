"""Persist season release observations, notices, and inbox preferences.

Revision ID: mt025
Revises: mt024
"""
from alembic import op
import sqlalchemy as sa


revision = "mt025"
down_revision = "mt024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tracking_preferences",
        sa.Column("new_season_release_dates", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "tracking_preferences",
        sa.Column("new_season_releases", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column("tracking_reviews", sa.Column("season_event_key", sa.String(length=255), nullable=True))
    op.create_index(
        "uq_tracking_review_season_event",
        "tracking_reviews",
        ["user_id", "season_event_key"],
        unique=True,
    )
    op.create_table(
        "tracking_season_release_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("season_number", sa.Integer(), nullable=False),
        sa.Column("release_date", sa.String(length=20), nullable=True),
        sa.Column("seen_upcoming", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("release_processed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "media_id", "season_number", name="uq_tracking_season_release_observation"),
    )
    op.create_index(
        "ix_tracking_season_release_observations_user_id",
        "tracking_season_release_observations",
        ["user_id"],
    )
    op.create_index(
        "ix_tracking_season_release_observations_media_id",
        "tracking_season_release_observations",
        ["media_id"],
    )


def downgrade():
    op.drop_index("ix_tracking_season_release_observations_media_id", table_name="tracking_season_release_observations")
    op.drop_index("ix_tracking_season_release_observations_user_id", table_name="tracking_season_release_observations")
    op.drop_table("tracking_season_release_observations")
    op.drop_index("uq_tracking_review_season_event", table_name="tracking_reviews")
    op.drop_column("tracking_reviews", "season_event_key")
    op.drop_column("tracking_preferences", "new_season_releases")
    op.drop_column("tracking_preferences", "new_season_release_dates")
