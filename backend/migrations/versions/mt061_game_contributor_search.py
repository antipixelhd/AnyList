"""Separate verified game qualification in the shared contributor search index."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt061'
down_revision = 'mt060'
branch_labels = None
depends_on = None

HOOK = 'staff_count=excluded.staff_count,screen_count=excluded.screen_count,roles=excluded.roles;'
CALL = '\n      PERFORM refresh_game_contributor_search(targets);'


def edit_function(name, replacements):
    bind = op.get_bind()
    definition = bind.exec_driver_sql(f"SELECT pg_get_functiondef('{name}'::regprocedure)").scalar_one()
    for old, new, count in replacements:
        if definition.count(old) != count:
            raise RuntimeError(f'Unexpected definition for {name}')
        definition = definition.replace(old, new)
    bind.exec_driver_sql(definition)


def upgrade():
    for name in ('game_work_count', 'game_actor_count', 'game_staff_count'):
        op.add_column('catalogue_contributor_search', sa.Column(name, sa.Integer(), nullable=False, server_default='0'))
    op.add_column('catalogue_contributor_search', sa.Column('game_roles', JSONB(), nullable=False, server_default='[]'))
    # Keep v1 movie/series counts, qualification, aliases and the partial index
    # intact. Game qualification is independent of movie principal cutoffs.
    op.execute("""CREATE FUNCTION refresh_game_contributor_search(targets integer[]) RETURNS void LANGUAGE plpgsql AS $$
    BEGIN
      IF coalesce(cardinality(targets),0)=0 THEN RETURN; END IF;
      PERFORM id FROM catalogue_entities WHERE id=ANY(targets) ORDER BY id FOR NO KEY UPDATE;
      WITH members AS (
        SELECT root.id AS root_id,root.kind AS root_kind,source.id
        FROM catalogue_entities root JOIN catalogue_entities source
          ON coalesce(source.canonical_person_id,source.canonical_company_id,source.id)=root.id
        WHERE root.id=ANY(targets) AND root.kind IN ('person','organization')
          AND root.canonical_person_id IS NULL AND root.canonical_company_id IS NULL
      ), sources AS (
        SELECT m.root_id,m.root_kind,c.work_id,c.role,c.role_label,w.attributes->>'game_class' AS game_class
        FROM members m JOIN catalogue_credits c ON c.contributor_id=m.id
        JOIN catalogue_entities w ON w.id=c.work_id AND w.kind='game'
        WHERE c.attributes->>'source_active' IS DISTINCT FROM 'false'
      ), ports AS (
        SELECT r.source_id,min(r.target_id) AS target_id FROM catalogue_relationships r
        WHERE r.relation='port' AND r.source_id IN (SELECT work_id FROM sources WHERE game_class='port')
        GROUP BY r.source_id HAVING count(DISTINCT r.target_id)=1
      ), root_ids AS (
        SELECT work_id AS id FROM sources WHERE game_class<>'port' UNION SELECT target_id FROM ports
      ), expansions AS (
        SELECT r.source_id,min(r.target_id) AS target_id FROM catalogue_relationships r
        WHERE r.relation='expansion' AND r.source_id IN (SELECT id FROM root_ids)
        GROUP BY r.source_id HAVING count(DISTINCT r.target_id)=1
      ), eligible AS (
        SELECT w.id FROM catalogue_entities w LEFT JOIN expansions x ON x.source_id=w.id
        LEFT JOIN catalogue_entities base ON base.id=x.target_id AND base.kind='game'
        WHERE w.id IN (SELECT id FROM root_ids) AND w.kind='game'
          AND w.attributes->>'adult' IS DISTINCT FROM 'true'
          AND (w.attributes->>'game_class' IN ('game','standalone_expansion','remake','remaster','expanded_game')
            OR (w.attributes->>'game_class'='expansion'
              AND base.attributes->>'game_class' IN ('game','standalone_expansion','remake','remaster','expanded_game')))
      ), works AS (
        SELECT s.root_id,s.root_kind,s.work_id,s.role,s.role_label FROM sources s
          JOIN eligible e ON e.id=s.work_id WHERE s.game_class<>'port'
        UNION ALL
        SELECT s.root_id,s.root_kind,p.target_id,s.role,s.role_label FROM sources s
          JOIN ports p ON p.source_id=s.work_id JOIN eligible e ON e.id=p.target_id WHERE s.game_class='port'
      ), stats AS (
        SELECT root_id,count(DISTINCT work_id)::integer AS total,
          count(DISTINCT work_id) FILTER (WHERE root_kind='person' AND role IN ('actor','voice_actor','narrator'))::integer AS actors,
          count(DISTINCT work_id) FILTER (WHERE root_kind='person' AND role NOT IN ('actor','voice_actor','narrator'))::integer AS staff,
          jsonb_agg(DISTINCT jsonb_build_array(role,role_label)) AS roles
        FROM works WHERE root_kind='person' OR role IN ('developer','publisher','supporting','porting') GROUP BY root_id
      )
      UPDATE catalogue_contributor_search summary SET game_work_count=coalesce(stats.total,0),
        game_actor_count=coalesce(stats.actors,0),game_staff_count=coalesce(stats.staff,0),game_roles=coalesce(stats.roles,'[]')
      FROM catalogue_entities root LEFT JOIN stats ON stats.root_id=root.id
      WHERE summary.entity_id=root.id AND root.id=ANY(targets);
    END $$""")
    edit_function('refresh_contributor_search(integer[])', [(HOOK, HOOK + CALL, 1)])
    edit_function('contributor_search_credit_update()', [
        (f"{prefix}.attributes->'principal')", f"{prefix}.attributes->'principal',{prefix}.attributes->'source_active')", 2)
        for prefix in ('n', 'o')])
    op.execute("""CREATE FUNCTION contributor_game_work_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE changed integer[];
    BEGIN
      SELECT array_agg(n.id) INTO changed FROM new_rows n JOIN old_rows o USING(id)
        WHERE (n.kind='game' OR o.kind='game') AND (n.kind,n.attributes->'game_class',n.attributes->'adult')
          IS DISTINCT FROM (o.kind,o.attributes->'game_class',o.attributes->'adult');
      IF coalesce(cardinality(changed),0)=0 THEN RETURN NULL; END IF;
      PERFORM refresh_game_contributor_search(ARRAY(SELECT DISTINCT coalesce(p.canonical_person_id,p.canonical_company_id,p.id)
        FROM catalogue_credits c JOIN catalogue_entities p ON p.id=c.contributor_id
        WHERE c.work_id=ANY(changed) OR c.work_id IN (SELECT source_id FROM catalogue_relationships
          WHERE target_id=ANY(changed) AND relation IN ('port','expansion'))));
      RETURN NULL;
    END $$""")
    op.execute('CREATE TRIGGER contributor_game_work_changed AFTER UPDATE ON catalogue_entities '
        'REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION contributor_game_work_changed()')
    for event in ('insert', 'update', 'delete'):
        selects = []
        for table in (('old_rows',) if event == 'delete' else ('new_rows',) if event == 'insert' else ('old_rows','new_rows')):
            selects.append(f"SELECT source_id FROM {table} WHERE relation IN ('port','expansion')")
        changed = ' UNION '.join(selects)
        op.execute(f"""CREATE FUNCTION contributor_game_relationship_{event}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM refresh_game_contributor_search(ARRAY(SELECT DISTINCT coalesce(p.canonical_person_id,p.canonical_company_id,p.id)
            FROM catalogue_credits c JOIN catalogue_entities p ON p.id=c.contributor_id
            WHERE c.work_id IN ({changed}))); RETURN NULL;
        END $$""")
        refs = (' OLD TABLE AS old_rows' if event != 'insert' else '') + (' NEW TABLE AS new_rows' if event != 'delete' else '')
        op.execute(f'CREATE TRIGGER contributor_game_relationship_{event} AFTER {event.upper()} ON catalogue_relationships '
            f'REFERENCING{refs} FOR EACH STATEMENT EXECUTE FUNCTION contributor_game_relationship_{event}()')
    bind = op.get_bind()
    schema = bind.exec_driver_sql("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pg_trgm'").scalar_one()
    quoted = bind.dialect.identifier_preparer.quote(schema)
    op.execute(f'CREATE INDEX ix_contributor_game_search_text ON catalogue_contributor_search USING gin '
        f'(search_text {quoted}.gin_trgm_ops) WITH (fastupdate=off) WHERE game_work_count>0')
    op.execute("""DO $$ DECLARE batch integer[]; cursor_id integer:=0;
    BEGIN LOOP
      SELECT array_agg(id) INTO batch FROM (SELECT id FROM catalogue_entities WHERE id>cursor_id
        AND kind IN ('person','organization') AND canonical_person_id IS NULL AND canonical_company_id IS NULL
        ORDER BY id LIMIT 500) roots;
      EXIT WHEN batch IS NULL;
      PERFORM refresh_game_contributor_search(batch); SELECT max(id) INTO cursor_id FROM unnest(batch) id;
    END LOOP; END $$""")
    op.execute('ANALYZE catalogue_contributor_search')


def downgrade():
    op.execute('DROP TRIGGER contributor_game_work_changed ON catalogue_entities')
    op.execute('DROP FUNCTION contributor_game_work_changed()')
    for event in ('insert','update','delete'):
        op.execute(f'DROP TRIGGER contributor_game_relationship_{event} ON catalogue_relationships')
        op.execute(f'DROP FUNCTION contributor_game_relationship_{event}()')
    edit_function('refresh_contributor_search(integer[])', [(HOOK + CALL, HOOK, 1)])
    edit_function('contributor_search_credit_update()', [
        (f"{prefix}.attributes->'principal',{prefix}.attributes->'source_active')", f"{prefix}.attributes->'principal')", 2)
        for prefix in ('n','o')])
    op.execute('DROP FUNCTION refresh_game_contributor_search(integer[])')
    op.drop_index('ix_contributor_game_search_text', table_name='catalogue_contributor_search')
    for name in ('game_roles','game_staff_count','game_actor_count','game_work_count'):
        op.drop_column('catalogue_contributor_search', name)
