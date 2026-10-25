"""Catalogue-rooted games and parent-dependent expansion tracking."""
from alembic import op

# Main deployed mt058 for vote eligibility while beta had already advanced to
# mt066. Distinguish the Games branch; mt059+ retain their deployed identities.
revision = 'mt058_games'
down_revision = 'mt057'
branch_labels = None
depends_on = None


def upgrade():
    for column in ('show_movies_series', 'show_games', 'show_game_achievements'):
        op.execute(f'ALTER TABLE tracking_preferences ADD COLUMN {column} boolean NOT NULL DEFAULT true')
    op.execute("""CREATE TABLE game_entries (
        id serial PRIMARY KEY,
        user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        work_id integer NOT NULL REFERENCES catalogue_entities(id) ON DELETE RESTRICT,
        status varchar(16) NOT NULL DEFAULT 'planning', score double precision, notes text,
        start_date date, finish_date date, total_seconds bigint, completion_seconds bigint,
        completion_observed_at timestamp(3), completion_source varchar(32), replay_count integer,
        achievement_mode varchar(16), achievement_count integer, achievement_percentage double precision,
        revision integer NOT NULL DEFAULT 1,
        updated_at timestamp(3) NOT NULL DEFAULT date_trunc('milliseconds', now()),
        CONSTRAINT uq_game_entry_owner_work UNIQUE(user_id,work_id),
        CONSTRAINT uq_game_entry_reference UNIQUE(id,user_id,work_id),
        CONSTRAINT ck_game_entry_status CHECK(status IN ('planning','playing','paused','completed','dropped')),
        CONSTRAINT ck_game_entry_score CHECK(score IS NULL OR (score>=0.5 AND score<=10)),
        CONSTRAINT ck_game_entry_total CHECK(total_seconds IS NULL OR total_seconds>=0),
        CONSTRAINT ck_game_entry_completion CHECK(completion_seconds IS NULL OR completion_seconds>=0),
        CONSTRAINT ck_game_entry_replay CHECK(replay_count IS NULL OR replay_count>=0),
        CONSTRAINT ck_game_entry_achievement_mode CHECK(achievement_mode IS NULL OR achievement_mode IN ('percentage','count','targeted')),
        CONSTRAINT ck_game_entry_achievement_count CHECK(achievement_count IS NULL OR achievement_count>=0),
        CONSTRAINT ck_game_entry_achievement_percent CHECK(achievement_percentage IS NULL OR (achievement_percentage>=0 AND achievement_percentage<=100)),
        CONSTRAINT ck_game_entry_revision CHECK(revision>=1))""")
    op.execute("""CREATE TABLE game_favorites (
        user_id integer REFERENCES users(id) ON DELETE CASCADE,
        work_id integer REFERENCES catalogue_entities(id) ON DELETE RESTRICT,
        PRIMARY KEY(user_id,work_id))""")
    op.execute("""CREATE TABLE game_child_states (
        id serial PRIMARY KEY,
        user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        work_id integer NOT NULL REFERENCES catalogue_entities(id) ON DELETE RESTRICT,
        base_work_id integer NOT NULL REFERENCES catalogue_entities(id) ON DELETE RESTRICT,
        base_entry_id integer REFERENCES game_entries(id) ON DELETE SET NULL,
        status varchar(16) NOT NULL DEFAULT 'planning', score double precision,
        start_date date, finish_date date, revision integer NOT NULL DEFAULT 1,
        updated_at timestamp(3) NOT NULL DEFAULT date_trunc('milliseconds', now()),
        CONSTRAINT uq_game_child_owner_work UNIQUE(user_id,work_id),
        CONSTRAINT fk_game_child_owner_base FOREIGN KEY(base_entry_id,user_id,base_work_id)
          REFERENCES game_entries(id,user_id,work_id) DEFERRABLE INITIALLY DEFERRED,
        CONSTRAINT ck_game_child_distinct CHECK(work_id<>base_work_id),
        CONSTRAINT ck_game_child_status CHECK(status IN ('planning','playing','paused','completed','dropped')),
        CONSTRAINT ck_game_child_score CHECK(score IS NULL OR (score>=0.5 AND score<=10)),
        CONSTRAINT ck_game_child_revision CHECK(revision>=1))""")
    op.execute("""CREATE TABLE game_achievements (
        id serial PRIMARY KEY, work_id integer NOT NULL,
        namespace varchar(80) NOT NULL DEFAULT 'steam.app', app_id varchar(200) NOT NULL,
        api_name varchar(200) NOT NULL, name varchar(500) NOT NULL, description text,
        icon_url text, locked_icon_url text, hidden boolean NOT NULL DEFAULT false,
        global_percentage double precision,
        fetched_at timestamp(3) NOT NULL DEFAULT date_trunc('milliseconds', now()),
        CONSTRAINT uq_game_achievement_identity UNIQUE(app_id,api_name),
        CONSTRAINT uq_game_achievement_work UNIQUE(id,work_id),
        CONSTRAINT fk_game_achievement_steam FOREIGN KEY(work_id,namespace,app_id)
          REFERENCES catalogue_identities(entity_id,namespace,external_id) ON DELETE RESTRICT,
        CONSTRAINT ck_game_achievement_namespace CHECK(namespace='steam.app'),
        CONSTRAINT ck_game_achievement_percent CHECK(global_percentage IS NULL OR (global_percentage>=0 AND global_percentage<=100)))""")
    op.execute("""CREATE TABLE game_achievement_unlocks (
        user_id integer NOT NULL, achievement_id integer NOT NULL, work_id integer NOT NULL,
        source varchar(32) NOT NULL DEFAULT 'manual',
        observed_at timestamp(3) NOT NULL DEFAULT date_trunc('milliseconds', now()),
        PRIMARY KEY(user_id,achievement_id),
        CONSTRAINT fk_game_unlock_work FOREIGN KEY(achievement_id,work_id)
          REFERENCES game_achievements(id,work_id) ON DELETE CASCADE,
        CONSTRAINT fk_game_unlock_owner FOREIGN KEY(user_id,work_id)
          REFERENCES game_entries(user_id,work_id) ON DELETE CASCADE)""")
    op.execute("""CREATE TABLE game_activity (
        id serial PRIMARY KEY,
        user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        work_id integer NOT NULL REFERENCES catalogue_entities(id) ON DELETE RESTRICT,
        status varchar(16) NOT NULL, score double precision, payload jsonb NOT NULL DEFAULT '{}',
        created_at timestamp(3) NOT NULL DEFAULT date_trunc('milliseconds', now()))""")
    for table, columns in (
        ('game_entries', ('user_id','work_id')),
        ('game_child_states', ('user_id','work_id','base_work_id','base_entry_id')),
        ('game_achievements', ('work_id',)),
        ('game_activity', ('user_id','work_id')),
    ):
        for column in columns:
            op.execute(f'CREATE INDEX ix_{table}_{column} ON {table}({column})')
    # Old catalogue games have never had personal tracking. Backfill only
    # descriptive classification from their retained provider snapshots.
    op.execute("""UPDATE catalogue_entities e SET attributes=e.attributes || jsonb_build_object(
        'game_class', CASE coalesce(s.payload->'game'->'game_type'->>'type', s.payload->'game_type'->>'type')
          WHEN 'Main Game' THEN 'game' WHEN 'DLC' THEN 'dlc' WHEN 'DLC Add-on' THEN 'dlc'
          WHEN 'Expansion' THEN 'expansion' WHEN 'Standalone Expansion' THEN 'standalone_expansion'
          WHEN 'Remake' THEN 'remake' WHEN 'Remaster' THEN 'remaster' WHEN 'Port' THEN 'port'
          WHEN 'Expanded Game' THEN 'expanded_game' WHEN 'Pack / Addon' THEN 'pack'
          WHEN 'Update' THEN 'update' ELSE 'unknown' END)
        FROM catalogue_metadata_snapshots s
        WHERE e.id=s.entity_id AND e.kind='game' AND s.provider='igdb'
          AND NOT e.attributes ? 'game_class'""")
    op.execute("""CREATE FUNCTION game_require_independent() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM 1 FROM catalogue_entities e WHERE e.id=NEW.work_id AND e.kind='game'
        AND e.attributes->>'game_class' IN ('game','standalone_expansion','remake','remaster','expanded_game','port')
        FOR SHARE;
      IF NOT FOUND THEN RAISE EXCEPTION 'Independent game required' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$""")
    for table in ('game_entries','game_favorites'):
        op.execute(f'CREATE TRIGGER {table}_work BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION game_require_independent()')
    op.execute("""CREATE FUNCTION game_require_child() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE cls text;
    BEGIN
      SELECT e.attributes->>'game_class' INTO cls FROM catalogue_entities e
        WHERE e.id=NEW.work_id AND e.kind='game' FOR SHARE;
      IF cls IS NULL OR cls NOT IN ('dlc','expansion') OR
         (NEW.base_entry_id IS NOT NULL AND cls<>'expansion') THEN
        RAISE EXCEPTION 'Playable child required' USING ERRCODE='23514'; END IF;
      PERFORM 1 FROM catalogue_entities e WHERE e.id=NEW.base_work_id AND e.kind='game'
        AND e.attributes->>'game_class' IN ('game','standalone_expansion','remake','remaster','expanded_game','port')
        FOR SHARE;
      IF NOT FOUND THEN RAISE EXCEPTION 'Independent base game required' USING ERRCODE='23514'; END IF;
      PERFORM 1 FROM catalogue_relationships r WHERE r.source_id=NEW.work_id
        AND r.target_id=NEW.base_work_id AND r.relation=cls FOR SHARE;
      IF NOT FOUND THEN RAISE EXCEPTION 'Verified base relationship required' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$""")
    op.execute('CREATE TRIGGER game_child_work BEFORE INSERT OR UPDATE ON game_child_states FOR EACH ROW EXECUTE FUNCTION game_require_child()')
    # Preserve reference validity in both directions, including direct SQL and
    # concurrent metadata corrections. Never silently reparent personal facts.
    op.execute("""CREATE FUNCTION game_protect_work() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF EXISTS(SELECT 1 FROM game_entries WHERE work_id=OLD.id) OR
         EXISTS(SELECT 1 FROM game_favorites WHERE work_id=OLD.id) OR
         EXISTS(SELECT 1 FROM game_child_states WHERE base_work_id=OLD.id) THEN
        IF NEW.kind<>'game' OR coalesce(NEW.attributes->>'game_class','') NOT IN
          ('game','standalone_expansion','remake','remaster','expanded_game','port') THEN
          RAISE EXCEPTION 'Tracked game classification is in use' USING ERRCODE='23514'; END IF;
      END IF;
      IF EXISTS(SELECT 1 FROM game_child_states WHERE work_id=OLD.id) AND
         (NEW.kind<>'game' OR NEW.attributes->>'game_class' IS DISTINCT FROM OLD.attributes->>'game_class') THEN
        RAISE EXCEPTION 'Child classification is in use' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$""")
    op.execute('CREATE TRIGGER game_work_protect BEFORE UPDATE OF kind,attributes ON catalogue_entities FOR EACH ROW EXECUTE FUNCTION game_protect_work()')
    op.execute("""CREATE FUNCTION game_protect_relationship() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' AND (NEW.source_id,NEW.target_id,NEW.relation) IS NOT DISTINCT FROM
          (OLD.source_id,OLD.target_id,OLD.relation) THEN RETURN NEW; END IF;
      IF EXISTS(SELECT 1 FROM game_child_states WHERE work_id=OLD.source_id AND base_work_id=OLD.target_id)
        AND OLD.relation IN ('dlc','expansion') THEN
        RAISE EXCEPTION 'Tracked child relationship is in use' USING ERRCODE='23514'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
    END $$""")
    op.execute('CREATE TRIGGER game_relationship_protect BEFORE UPDATE OR DELETE ON catalogue_relationships FOR EACH ROW EXECUTE FUNCTION game_protect_relationship()')


def downgrade():
    op.execute('DROP TRIGGER game_relationship_protect ON catalogue_relationships')
    op.execute('DROP TRIGGER game_work_protect ON catalogue_entities')
    for table in ('game_activity','game_achievement_unlocks','game_achievements','game_child_states','game_favorites','game_entries'):
        op.execute(f'DROP TABLE {table}')
    for function in ('game_require_independent','game_require_child','game_protect_work','game_protect_relationship'):
        op.execute(f'DROP FUNCTION {function}()')
    for column in ('show_movies_series','show_games','show_game_achievements'):
        op.execute(f'ALTER TABLE tracking_preferences DROP COLUMN {column}')
