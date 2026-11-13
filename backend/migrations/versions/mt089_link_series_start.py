"""Make the first tracked season authoritative for the series start date."""
from alembic import op
import sqlalchemy as sa

revision = 'mt089_link_series_start'
down_revision = 'mt088_series_editor'
branch_labels = depends_on = None


def tracking_trigger(include_start):
    op.execute('DROP TRIGGER season_completion_dirty ON tracked_entries')
    columns = 'user_id,media_id,status,progress,finish_date'
    if include_start:
        columns += ',start_date'
    op.execute(f'''CREATE TRIGGER season_completion_dirty
        AFTER INSERT OR UPDATE OF {columns} OR DELETE ON tracked_entries
        FOR EACH ROW EXECUTE FUNCTION queue_season_completion()''')


def upgrade():
    op.add_column('tracked_entries', sa.Column('start_date_source', sa.String(16)))
    tracking_trigger(True)
    op.execute("""INSERT INTO season_reconcile_jobs (user_id, media_id)
        SELECT e.user_id, e.media_id FROM tracked_entries e JOIN media m ON m.id=e.media_id
        WHERE m.media_type='series' ON CONFLICT DO NOTHING""")


def downgrade():
    tracking_trigger(False)
    op.drop_column('tracked_entries', 'start_date_source')
