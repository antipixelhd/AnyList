"""Independent game favorite ordering, without legacy Media-id collisions."""
from alembic import op

revision = 'mt059'
down_revision = 'mt058_games'
branch_labels = None
depends_on = None


def upgrade():
    # Nullable for compatibility during rolling upgrades; new writes assign a
    # position under the existing per-owner lock. NULLs remain deterministically
    # ordered by work id until normalized by an ordering write.
    op.execute('ALTER TABLE game_favorites ADD COLUMN position integer')
    op.execute('''WITH ranked AS (
        SELECT user_id,work_id,row_number() OVER(PARTITION BY user_id ORDER BY work_id)-1 AS position
        FROM game_favorites)
        UPDATE game_favorites f SET position=r.position FROM ranked r
        WHERE f.user_id=r.user_id AND f.work_id=r.work_id''')
    op.execute('ALTER TABLE game_favorites ADD CONSTRAINT ck_game_favorite_position CHECK(position IS NULL OR position>=0)')
    op.execute('ALTER TABLE game_favorites ADD CONSTRAINT uq_game_favorite_position UNIQUE(user_id,position) DEFERRABLE INITIALLY DEFERRED')


def downgrade():
    op.execute('ALTER TABLE game_favorites DROP CONSTRAINT uq_game_favorite_position')
    op.execute('ALTER TABLE game_favorites DROP CONSTRAINT ck_game_favorite_position')
    op.execute('ALTER TABLE game_favorites DROP COLUMN position')
