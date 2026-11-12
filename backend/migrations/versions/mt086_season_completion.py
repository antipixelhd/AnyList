"""Durable season completions and transactional reconciliation receipts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'mt086_season_completion'
down_revision = 'mt085_merge_sync_backport'
branch_labels = depends_on = None


def upgrade():
    op.create_table('series_seasons',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('media_id', sa.Integer, sa.ForeignKey('media.id', ondelete='CASCADE'), nullable=False),
        sa.Column('episode_order', sa.String(40), nullable=False),
        sa.Column('season_number', sa.Integer, nullable=False),
        sa.Column('membership_key', sa.String(64), nullable=False),
        sa.Column('episode_ids', postgresql.JSONB, nullable=False),
        sa.Column('metadata_payload', postgresql.JSONB, nullable=False),
        sa.UniqueConstraint('media_id', 'episode_order', 'season_number', 'membership_key'))
    op.create_index('ix_series_seasons_media_id', 'series_seasons', ['media_id'])
    op.create_table('season_tracking',
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('season_id', sa.Integer, sa.ForeignKey('series_seasons.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('completed', sa.Boolean, nullable=False),
        sa.Column('completion_date', sa.Date), sa.Column('date_source', sa.String(16)),
        sa.Column('date_manual', sa.Boolean, nullable=False),
        sa.Column('watched_episodes', sa.Integer, nullable=False),
        sa.Column('current', sa.Boolean, nullable=False),
        sa.Column('activity_suppressed', sa.Boolean, nullable=False),
        sa.Column('evidence', postgresql.JSONB, nullable=False))
    op.create_table('season_reconcile_jobs',
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('media_id', sa.Integer, sa.ForeignKey('media.id', ondelete='CASCADE'), primary_key=True))
    install_triggers()
    op.execute("""INSERT INTO season_reconcile_jobs SELECT e.user_id,e.media_id
        FROM tracked_entries e JOIN media m ON m.id=e.media_id WHERE m.media_type='series'
        ON CONFLICT DO NOTHING""")


def install_triggers():
    # Facts may be written by ORM, bulk imports or direct SQL. The receipt is
    # in the same transaction, so rollbacks cannot publish phantom milestones.
    op.execute("""CREATE OR REPLACE FUNCTION queue_season_completion() RETURNS trigger AS $$
    DECLARE r record; uid integer; mid integer; native integer;
    BEGIN
      IF TG_OP='DELETE' THEN r:=OLD; ELSE r:=NEW; END IF;
      IF TG_TABLE_NAME='tracked_entries' THEN
        IF TG_OP='DELETE' THEN
          DELETE FROM season_tracking WHERE user_id=r.user_id AND season_id IN
            (SELECT id FROM series_seasons WHERE media_id=r.media_id);
          DELETE FROM season_reconcile_jobs WHERE user_id=r.user_id AND media_id=r.media_id;
          RETURN NULL;
        END IF;
        uid:=r.user_id; mid:=r.media_id;
      ELSIF TG_TABLE_NAME='watch_events' THEN
        uid:=r.user_id;
        IF TG_OP='UPDATE' THEN
          INSERT INTO season_reconcile_jobs(user_id,media_id)
            SELECT e.user_id,e.media_id FROM media ep JOIN shows s ON s.id=ep.show_id
            JOIN media root ON root.media_type='series' AND
              ((s.tmdb_id IS NOT NULL AND root.tmdb_id=s.tmdb_id) OR
               (s.tvdb_id IS NOT NULL AND root.tvdb_id=s.tvdb_id))
            JOIN tracked_entries e ON e.media_id=root.id AND e.user_id=OLD.user_id
            WHERE ep.id=OLD.media_id ON CONFLICT DO NOTHING;
        END IF;
        SELECT root.id INTO mid FROM media ep JOIN shows s ON s.id=ep.show_id
          JOIN media root ON root.media_type='series' AND
            ((s.tmdb_id IS NOT NULL AND root.tmdb_id=s.tmdb_id) OR
             (s.tvdb_id IS NOT NULL AND root.tvdb_id=s.tvdb_id)) WHERE ep.id=r.media_id LIMIT 1;
      ELSIF TG_TABLE_NAME='user_show_episode_orders' THEN
        uid:=r.user_id;
        SELECT id INTO mid FROM media WHERE media_type='series' AND tmdb_id=r.series_tmdb_id LIMIT 1;
      ELSIF TG_TABLE_NAME='show_episode_positions' THEN
        native:=r.series_tmdb_id;
      ELSIF TG_TABLE_NAME='media' THEN
        IF r.media_type='series' THEN mid:=r.id;
        ELSIF r.media_type='episode' THEN
          SELECT root.id INTO mid FROM shows s JOIN media root ON root.media_type='series' AND
            ((s.tmdb_id IS NOT NULL AND root.tmdb_id=s.tmdb_id) OR
             (s.tvdb_id IS NOT NULL AND root.tvdb_id=s.tvdb_id)) WHERE s.id=r.show_id LIMIT 1;
        END IF;
      END IF;
      INSERT INTO season_reconcile_jobs(user_id,media_id)
        SELECT e.user_id,e.media_id FROM tracked_entries e JOIN media m ON m.id=e.media_id
        WHERE m.media_type='series' AND (mid=e.media_id OR native=m.tmdb_id)
          AND (uid IS NULL OR e.user_id=uid) ON CONFLICT DO NOTHING;
      RETURN NULL;
    END; $$ LANGUAGE plpgsql""")
    for table in ('watch_events', 'tracked_entries', 'user_show_episode_orders', 'show_episode_positions', 'media'):
        columns = {
            'watch_events': 'user_id,media_id,watched_at,completed,date_inferred,date_shared,provisional',
            'tracked_entries': 'user_id,media_id,status,progress,finish_date',
            'user_show_episode_orders': 'user_id,series_tmdb_id,episode_order',
            'media': 'tmdb_id,tvdb_id,media_type,show_id,season_number,episode_number,release_date,tmdb_data',
        }.get(table)
        events = f'INSERT OR UPDATE OF {columns} OR DELETE' if columns else 'INSERT OR UPDATE OR DELETE'
        op.execute(f'CREATE TRIGGER season_completion_dirty AFTER {events} ON {table} '
                   'FOR EACH ROW EXECUTE FUNCTION queue_season_completion()')


def downgrade():
    for table in ('watch_events', 'tracked_entries', 'user_show_episode_orders', 'show_episode_positions', 'media'):
        op.execute(f'DROP TRIGGER IF EXISTS season_completion_dirty ON {table}')
    op.execute('DROP FUNCTION IF EXISTS queue_season_completion()')
    for table in ('season_reconcile_jobs', 'season_tracking', 'series_seasons'):
        op.drop_table(table)
