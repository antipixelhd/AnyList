"""Independent private Games statistics and reliable personal invalidation."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision='mt066'
down_revision='mt065'
branch_labels=depends_on=None


def upgrade():
    op.create_table('game_stats_state',
        sa.Column('user_id',sa.Integer,sa.ForeignKey('users.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('source_revision',sa.BigInteger,nullable=False,server_default='0'),
        sa.Column('metadata_source_revision',sa.BigInteger,nullable=False,server_default='0'),
        sa.Column('computed_source_revision',sa.BigInteger),sa.Column('metadata_revision',sa.BigInteger),
        sa.Column('generation',sa.BigInteger,nullable=False,server_default='0'),
        sa.Column('contract_version',sa.Integer,nullable=False,server_default='0'),sa.Column('payload',pg.JSONB),
        sa.Column('computed_at',pg.TIMESTAMP(precision=3)),
        sa.Column('next_due_at',pg.TIMESTAMP(precision=3),nullable=False,server_default=sa.text("date_trunc('milliseconds',now() AT TIME ZONE 'UTC')")),
        sa.Column('lease_token',sa.String(36)),sa.Column('lease_until',pg.TIMESTAMP(precision=3)),
        sa.Column('attempts',sa.Integer,nullable=False,server_default='0'),sa.Column('error_code',sa.String(32)))
    op.create_index('ix_game_stats_state_next_due_at','game_stats_state',['next_due_at'])
    op.execute("""CREATE FUNCTION game_stats_mark_user(target integer) RETURNS void LANGUAGE plpgsql AS $$
      BEGIN
        INSERT INTO game_stats_state(user_id,source_revision,next_due_at)
          SELECT id,1,date_trunc('milliseconds',now() AT TIME ZONE 'UTC') FROM users WHERE id=target
        ON CONFLICT(user_id) DO UPDATE SET source_revision=game_stats_state.source_revision+1,
          payload=NULL,next_due_at=date_trunc('milliseconds',now() AT TIME ZONE 'UTC');
      END $$""")
    op.execute("""CREATE FUNCTION game_stats_fact_changed() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='UPDATE' THEN
          IF TG_TABLE_NAME='game_entries' AND
            to_jsonb(OLD)-'notes'-'revision'-'updated_at'=to_jsonb(NEW)-'notes'-'revision'-'updated_at' THEN RETURN NEW; END IF;
          IF TG_TABLE_NAME='game_achievement_unlocks' THEN
            IF OLD.user_id=NEW.user_id AND OLD.work_id=NEW.work_id AND OLD.achievement_id=NEW.achievement_id THEN RETURN NEW; END IF;
          END IF;
        END IF;
        IF TG_OP='DELETE' THEN PERFORM game_stats_mark_user(OLD.user_id); RETURN OLD; END IF;
        IF TG_OP='UPDATE' AND OLD.user_id<>NEW.user_id THEN PERFORM game_stats_mark_user(OLD.user_id); END IF;
        PERFORM game_stats_mark_user(NEW.user_id); RETURN NEW;
      END $$""")
    for table in ('game_entries','game_achievement_unlocks'):
        op.execute(f'CREATE TRIGGER game_stats_fact_changed AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION game_stats_fact_changed()')
    op.execute('INSERT INTO game_stats_state(user_id) SELECT DISTINCT user_id FROM game_entries ON CONFLICT DO NOTHING')
    op.execute("""CREATE FUNCTION game_stats_mark_metadata(works integer[]) RETURNS void LANGUAGE plpgsql AS $$
      BEGIN
        UPDATE game_stats_state SET metadata_source_revision=metadata_source_revision+1,payload=NULL,
          next_due_at=date_trunc('milliseconds',now() AT TIME ZONE 'UTC')
        WHERE user_id IN(SELECT user_id FROM game_entries WHERE work_id=ANY(works));
      END $$""")
    op.execute("""CREATE FUNCTION game_stats_entity_metadata_changed() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE works integer[];
      BEGIN
        WITH changed AS(SELECT n.id,n.kind FROM new_rows n JOIN old_rows o USING(id) WHERE
          (n.name,n.image_url,n.canonical_person_id,n.canonical_company_id,n.attributes->'game_class',
           n.attributes->'adult',n.attributes->'genres',n.attributes->'genre_filters',n.attributes->'release_date',
           n.attributes->'completion_estimates',n.attributes->'achievement_catalogue'->'total') IS DISTINCT FROM
          (o.name,o.image_url,o.canonical_person_id,o.canonical_company_id,o.attributes->'game_class',
           o.attributes->'adult',o.attributes->'genres',o.attributes->'genre_filters',o.attributes->'release_date',
           o.attributes->'completion_estimates',o.attributes->'achievement_catalogue'->'total')),
        affected AS(SELECT id FROM changed WHERE kind='game' UNION
          SELECT c.work_id FROM catalogue_credits c WHERE c.contributor_id IN(
            SELECT id FROM changed UNION SELECT p.id FROM catalogue_entities p WHERE
              p.canonical_person_id IN(SELECT id FROM changed) OR p.canonical_company_id IN(SELECT id FROM changed)))
        SELECT array_agg(id) INTO works FROM affected;
        PERFORM game_stats_mark_metadata(works); RETURN NULL;
      END $$""")
    op.execute("""CREATE TRIGGER game_stats_entity_metadata_changed AFTER UPDATE ON catalogue_entities
      REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION game_stats_entity_metadata_changed()""")
    op.execute("""CREATE FUNCTION game_stats_credit_metadata_changed() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE works integer[];
      BEGIN
        IF TG_OP='INSERT' THEN SELECT array_agg(DISTINCT work_id) INTO works FROM new_rows;
        ELSIF TG_OP='DELETE' THEN SELECT array_agg(DISTINCT work_id) INTO works FROM old_rows;
        ELSE
          SELECT array_agg(DISTINCT work_id) INTO works FROM(
            SELECT n.work_id FROM new_rows n JOIN old_rows o USING(id) WHERE
              (n.work_id,n.contributor_id,n.role,n.role_label,n.attributes->>'source_active') IS DISTINCT FROM
              (o.work_id,o.contributor_id,o.role,o.role_label,o.attributes->>'source_active')
            UNION SELECT o.work_id FROM old_rows o JOIN new_rows n USING(id) WHERE
              (n.work_id,n.contributor_id,n.role,n.role_label,n.attributes->>'source_active') IS DISTINCT FROM
              (o.work_id,o.contributor_id,o.role,o.role_label,o.attributes->>'source_active')) changed;
        END IF;
        PERFORM game_stats_mark_metadata(works); RETURN NULL;
      END $$""")
    for action,reference in (('INSERT','NEW TABLE AS new_rows'),('DELETE','OLD TABLE AS old_rows'),('UPDATE','OLD TABLE AS old_rows NEW TABLE AS new_rows')):
        op.execute(f'CREATE TRIGGER game_stats_credit_{action.lower()} AFTER {action} ON catalogue_credits REFERENCING {reference} FOR EACH STATEMENT EXECUTE FUNCTION game_stats_credit_metadata_changed()')


def downgrade():
    for action in ('insert','delete','update'):
        op.execute(f'DROP TRIGGER game_stats_credit_{action} ON catalogue_credits')
    op.execute('DROP TRIGGER game_stats_entity_metadata_changed ON catalogue_entities')
    op.execute('DROP FUNCTION game_stats_credit_metadata_changed()')
    op.execute('DROP FUNCTION game_stats_entity_metadata_changed()')
    op.execute('DROP FUNCTION game_stats_mark_metadata(integer[])')
    for table in ('game_entries','game_achievement_unlocks'):
        op.execute(f'DROP TRIGGER game_stats_fact_changed ON {table}')
    op.execute('DROP FUNCTION game_stats_fact_changed()')
    op.execute('DROP FUNCTION game_stats_mark_user(integer)')
    op.drop_table('game_stats_state')
