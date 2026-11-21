"""Round recorded game playtime and first completions to tenths of an hour."""

from alembic import op

revision = 'mt102_game_time_tenths'
down_revision = 'mt101_independent_seasons'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
        UPDATE game_entries
        SET total_seconds = total_seconds - total_seconds % 360 +
                CASE WHEN total_seconds % 360 >= 180 THEN 360 ELSE 0 END,
            completion_seconds = completion_seconds - completion_seconds % 360 +
                CASE WHEN completion_seconds % 360 >= 180 THEN 360 ELSE 0 END,
            revision = revision + 1
        WHERE total_seconds % 360 <> 0 OR completion_seconds % 360 <> 0
    ''')


def downgrade():
    pass  # Rounding changes the recorded durations, with no schema change to undo.
