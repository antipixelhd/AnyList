"""Align stored movie progress with its current status.

Revision ID: mt016
Revises: mt015
"""
from alembic import op
import sqlalchemy as sa

revision = "mt016"
down_revision = "mt015"
branch_labels = None
depends_on = None


def upgrade():
    # Viewing history remains intact; this repairs the current 0/1 list value.
    op.execute(sa.text("""
        UPDATE tracked_entries AS entry
        SET progress = CASE WHEN entry.status = 'completed' THEN 1 ELSE 0 END
        FROM media AS title
        WHERE title.id = entry.media_id
          AND title.media_type = 'movie'
          AND entry.progress IS DISTINCT FROM
              CASE WHEN entry.status = 'completed' THEN 1 ELSE 0 END
    """))


def downgrade():
    # Previous inconsistent progress values cannot be reconstructed.
    pass
