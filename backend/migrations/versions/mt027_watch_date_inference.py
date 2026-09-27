"""Track inferred watch dates and backfill legacy unknown dates."""

from alembic import op
import sqlalchemy as sa


revision = "mt027"
down_revision = "mt026"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "watch_events",
        sa.Column("date_inferred", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Existing provisional events already represent estimates. Preserve that
    # fact independently from their webhook reconciliation marker.
    op.execute(sa.text(
        "UPDATE watch_events SET date_inferred = TRUE WHERE provisional = TRUE"
    ))
    # Give completed legacy rows without dates a useful, explicitly inferred
    # value. Prefer the tracked movie/show finish date; otherwise the only
    # available timestamp is when AnyList first recorded the event.
    op.execute(sa.text("""
        UPDATE watch_events AS event
        SET watched_at = COALESCE(
            (
                SELECT CAST(entry.finish_date AS timestamp)
                FROM tracked_entries AS entry
                WHERE entry.user_id = event.user_id
                  AND entry.media_id = event.media_id
                  AND entry.finish_date IS NOT NULL
                ORDER BY entry.id DESC
                LIMIT 1
            ),
            (
                SELECT CAST(entry.finish_date AS timestamp)
                FROM media AS episode
                JOIN shows AS show ON show.id = episode.show_id
                JOIN media AS series
                  ON series.media_type = 'series'
                 AND ((show.tmdb_id IS NOT NULL AND series.tmdb_id = show.tmdb_id)
                   OR (show.tvdb_id IS NOT NULL AND series.tvdb_id = show.tvdb_id))
                JOIN tracked_entries AS entry
                  ON entry.user_id = event.user_id
                 AND entry.media_id = series.id
                WHERE episode.id = event.media_id
                  AND entry.finish_date IS NOT NULL
                ORDER BY entry.id DESC
                LIMIT 1
            ),
            event.created_at
        ),
        date_inferred = TRUE
        WHERE event.completed = TRUE AND event.watched_at IS NULL
    """))


def downgrade():
    op.drop_column("watch_events", "date_inferred")
