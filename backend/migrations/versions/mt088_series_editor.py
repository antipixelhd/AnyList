"""Season start dates and the release boundary of a series completion."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'mt088_series_editor'
down_revision = 'mt087_merge_game_categories'
branch_labels = depends_on = None


def upgrade():
    op.add_column('season_tracking', sa.Column('start_date', sa.Date))
    op.add_column('season_tracking', sa.Column('start_source', sa.String(16)))
    op.add_column('season_tracking', sa.Column('start_manual', sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column('season_tracking', sa.Column('start_evidence', postgresql.JSONB, nullable=False, server_default='{}'))
    op.add_column('season_tracking', sa.Column('series_finish_hint', sa.Date))
    op.add_column('season_tracking', sa.Column('series_finish_source', sa.String(16)))
    op.add_column('tracked_entries', sa.Column('finish_date_source', sa.String(16)))
    op.add_column('tracked_entries', sa.Column('season_release_check_on', sa.Date))
    op.create_index('ix_tracking_season_release_check', 'tracked_entries', ['season_release_check_on'],
                    postgresql_where=sa.text('season_release_check_on IS NOT NULL'))
    op.execute("""INSERT INTO season_reconcile_jobs (user_id, media_id)
        SELECT e.user_id, e.media_id FROM tracked_entries e JOIN media m ON m.id=e.media_id
        WHERE m.media_type='series' ON CONFLICT DO NOTHING""")


def downgrade():
    op.drop_index('ix_tracking_season_release_check', table_name='tracked_entries')
    for column in ('season_release_check_on', 'finish_date_source'):
        op.drop_column('tracked_entries', column)
    for column in ('series_finish_source', 'series_finish_hint', 'start_evidence', 'start_manual', 'start_source', 'start_date'):
        op.drop_column('season_tracking', column)
