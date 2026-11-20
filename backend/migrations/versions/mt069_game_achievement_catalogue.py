"""Retain withdrawn achievement definitions and exact historical evidence."""
from alembic import op
import sqlalchemy as sa

revision='mt069_game_achievements'
down_revision='mt068'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('game_achievements',sa.Column('active',sa.Boolean(),nullable=False,server_default=sa.true()))
    op.execute('''CREATE FUNCTION game_stats_achievement_catalogue_changed() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE works integer[];
      BEGIN
        SELECT array_agg(DISTINCT n.work_id) INTO works FROM new_rows n JOIN old_rows o USING(id)
          WHERE n.active IS DISTINCT FROM o.active;
        IF works IS NOT NULL THEN PERFORM game_stats_mark_metadata(works); END IF;
        RETURN NULL;
      END $$''')
    op.execute('''CREATE TRIGGER game_stats_achievement_catalogue_changed AFTER UPDATE ON game_achievements
      REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT
      EXECUTE FUNCTION game_stats_achievement_catalogue_changed()''')


def downgrade():
    op.execute('DROP TRIGGER game_stats_achievement_catalogue_changed ON game_achievements')
    op.execute('DROP FUNCTION game_stats_achievement_catalogue_changed()')
    op.drop_column('game_achievements','active')
