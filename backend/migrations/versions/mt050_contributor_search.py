"""Maintain bounded contributor search reads without aggregating source credits."""
from alembic import op

revision = 'mt050'
down_revision = 'mt049'
branch_labels = None
depends_on = None

# Frozen v1 principal policy; source credits and authoritative aliases stay intact.
JOBS = """'creator','showrunner','head writer','director','supervising director',
'writer','screenplay','screenwriter','teleplay','story','author','novel','book','play',
'original story','characters','characters created by','original music composer','composer',
'director of photography','cinematographer','cinematography','editor','film editor',
'production design','production designer','costume design','costume designer','casting',
'casting director','visual effects supervisor','animation director','producer','executive producer'"""


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    op.execute('CREATE INDEX ix_catalogue_contributor_root ON catalogue_entities '
               '((coalesce(canonical_person_id, canonical_company_id, id)))')
    op.execute("""CREATE TABLE catalogue_contributor_search (
        entity_id integer PRIMARY KEY REFERENCES catalogue_entities(id) ON DELETE CASCADE,
        kind varchar(24) NOT NULL, name varchar(500) NOT NULL, image_url text,
        search_text text NOT NULL, work_count integer NOT NULL DEFAULT 0,
        actor_count integer NOT NULL DEFAULT 0, staff_count integer NOT NULL DEFAULT 0,
        screen_count integer NOT NULL DEFAULT 0, roles jsonb NOT NULL DEFAULT '[]')""")
    extension_schema = op.get_bind().exec_driver_sql("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pg_trgm'").scalar_one()
    quoted = op.get_bind().dialect.identifier_preparer.quote(extension_schema)
    op.execute(fr"""CREATE FUNCTION refresh_contributor_search(targets integer[]) RETURNS void LANGUAGE plpgsql AS $$
    BEGIN
      IF coalesce(cardinality(targets),0)=0 THEN RETURN; END IF;
      -- Serialize competing updates to the same summary before taking its
      -- aggregate snapshot. Ordered root locks also cover non-import writers.
      PERFORM id FROM catalogue_entities WHERE id=ANY(targets) ORDER BY id FOR NO KEY UPDATE;
      DELETE FROM catalogue_contributor_search s USING catalogue_entities e
        WHERE s.entity_id=e.id AND e.id=ANY(targets)
          AND (e.kind NOT IN ('person','organization') OR e.canonical_person_id IS NOT NULL OR e.canonical_company_id IS NOT NULL);
      INSERT INTO catalogue_contributor_search
        (entity_id,kind,name,image_url,search_text,work_count,actor_count,staff_count,screen_count,roles)
      SELECT root.id,root.kind,root.name,root.image_url,
        root.name || ' ' || coalesce(names.labels,''),
        coalesce(stats.works,0),coalesce(stats.actors,0),coalesce(stats.staff,0),coalesce(stats.screen,0),coalesce(stats.roles,'[]')
      FROM catalogue_entities root
      LEFT JOIN LATERAL (
        SELECT string_agg(e.name || ' ' || coalesce(e.attributes->>'also_known_as',''),' ') AS labels
        FROM catalogue_entities e WHERE coalesce(e.canonical_person_id,e.canonical_company_id,e.id)=root.id
      ) names ON true
      LEFT JOIN LATERAL (
        SELECT count(DISTINCT c.work_id) FILTER (WHERE c.qualified)::integer AS works,
          count(DISTINCT c.work_id) FILTER (WHERE c.qualified AND c.role='actor')::integer AS actors,
          count(DISTINCT c.work_id) FILTER (WHERE c.qualified AND c.role<>'actor')::integer AS staff,
          count(DISTINCT c.work_id)::integer AS screen,
          jsonb_agg(DISTINCT jsonb_build_array(c.role,c.role_label)) FILTER (WHERE c.qualified AND c.clean) AS roles
        FROM (
          SELECT credit.work_id,credit.role,credit.role_label,
            coalesce(credit.character_label,'') !~* '\y(uncredited|archive footage|archive sound)\y' AS clean,
            (root.kind='organization' AND credit.role='producer') OR
            (root.kind='person' AND (credit.attributes->>'principal'='true' OR
             (credit.attributes->>'principal' IS NULL AND (
               (credit.role='actor' AND coalesce(credit.character_label,'') !~* '\y(uncredited|archive footage|archive sound)\y'
                 AND (credit.position IS NULL OR credit.position < CASE WHEN work.kind='movie' THEN 16 ELSE 24 END)) OR
               (lower(coalesce(credit.role_label,replace(credit.role,'_',' '))) IN ({JOBS}) AND
                (work.kind<>'movie' OR lower(coalesce(credit.role_label,replace(credit.role,'_',' ')))<>'executive producer')))))) AS qualified
          FROM catalogue_entities source
          JOIN catalogue_credits credit ON credit.contributor_id=source.id
          JOIN catalogue_entities work ON work.id=credit.work_id AND work.kind IN ('movie','series')
          WHERE coalesce(source.canonical_person_id,source.canonical_company_id,source.id)=root.id
        ) c
      ) stats ON true
      WHERE root.id=ANY(targets) AND root.kind IN ('person','organization')
        AND root.canonical_person_id IS NULL AND root.canonical_company_id IS NULL
      ON CONFLICT(entity_id) DO UPDATE SET kind=excluded.kind,name=excluded.name,image_url=excluded.image_url,
        search_text=excluded.search_text,work_count=excluded.work_count,actor_count=excluded.actor_count,
        staff_count=excluded.staff_count,screen_count=excluded.screen_count,roles=excluded.roles;
    END $$""")
    # Transition tables batch changed contributors once per SQL statement. Image
    # and character-link updates do not invalidate qualification or work counts.
    for event in ('INSERT','UPDATE','DELETE'):
        old = 'SELECT contributor_id FROM old_rows' if event != 'INSERT' else 'SELECT NULL::integer AS contributor_id WHERE false'
        new = 'SELECT contributor_id FROM new_rows' if event != 'DELETE' else 'SELECT NULL::integer AS contributor_id WHERE false'
        if event == 'UPDATE':
            changed = """SELECT n.contributor_id FROM new_rows n JOIN old_rows o USING(id)
              WHERE (n.work_id,n.contributor_id,n.role,n.role_label,n.character_label,n.position,n.attributes->'principal')
                IS DISTINCT FROM (o.work_id,o.contributor_id,o.role,o.role_label,o.character_label,o.position,o.attributes->'principal')
              UNION SELECT o.contributor_id FROM new_rows n JOIN old_rows o USING(id)
              WHERE (n.work_id,n.contributor_id,n.role,n.role_label,n.character_label,n.position,n.attributes->'principal')
                IS DISTINCT FROM (o.work_id,o.contributor_id,o.role,o.role_label,o.character_label,o.position,o.attributes->'principal')"""
        else:
            changed = f'{old} UNION {new}'
        op.execute(f"""CREATE FUNCTION contributor_search_credit_{event.lower()}() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN PERFORM refresh_contributor_search(ARRAY(SELECT DISTINCT coalesce(e.canonical_person_id,e.canonical_company_id,e.id)
            FROM catalogue_entities e JOIN ({changed}) c ON c.contributor_id=e.id)); RETURN NULL; END $$""")
        refs = (' OLD TABLE AS old_rows' if event != 'INSERT' else '') + (' NEW TABLE AS new_rows' if event != 'DELETE' else '')
        op.execute(f'CREATE TRIGGER contributor_search_credit_{event.lower()} AFTER {event} ON catalogue_credits '
                   f'REFERENCING{refs} FOR EACH STATEMENT EXECUTE FUNCTION contributor_search_credit_{event.lower()}()')
    op.execute("""CREATE FUNCTION contributor_search_entity_changed() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='INSERT' AND NEW.canonical_person_id IS NULL AND NEW.canonical_company_id IS NULL THEN
          -- A newly inserted root cannot have existing FK-backed credits.
          -- Avoid an aggregate probe for every person in a cold import.
          IF NEW.kind IN ('person','organization') THEN
            INSERT INTO catalogue_contributor_search(entity_id,kind,name,image_url,search_text)
              VALUES(NEW.id,NEW.kind,NEW.name,NEW.image_url,NEW.name || ' ' || NEW.name || ' ' || coalesce(NEW.attributes->>'also_known_as',''));
          END IF;
          RETURN NULL;
        END IF;
        IF TG_OP='DELETE' THEN
          PERFORM refresh_contributor_search(ARRAY[coalesce(OLD.canonical_person_id,OLD.canonical_company_id,OLD.id)]);
          RETURN NULL;
        END IF;
        IF TG_OP='UPDATE' AND (NEW.name,NEW.kind,NEW.image_url,NEW.attributes->'also_known_as',NEW.canonical_person_id,NEW.canonical_company_id)
          IS NOT DISTINCT FROM (OLD.name,OLD.kind,OLD.image_url,OLD.attributes->'also_known_as',OLD.canonical_person_id,OLD.canonical_company_id) THEN RETURN NULL; END IF;
        IF TG_OP='UPDATE' THEN
          PERFORM refresh_contributor_search(ARRAY[OLD.id,coalesce(OLD.canonical_person_id,OLD.canonical_company_id,OLD.id),NEW.id,coalesce(NEW.canonical_person_id,NEW.canonical_company_id,NEW.id)]);
          IF OLD.kind IS DISTINCT FROM NEW.kind THEN
            PERFORM refresh_contributor_search(ARRAY(SELECT DISTINCT coalesce(e.canonical_person_id,e.canonical_company_id,e.id)
              FROM catalogue_credits c JOIN catalogue_entities e ON e.id=c.contributor_id WHERE c.work_id=NEW.id));
          END IF;
        ELSE
          PERFORM refresh_contributor_search(ARRAY[coalesce(NEW.canonical_person_id,NEW.canonical_company_id,NEW.id)]);
        END IF;
        RETURN NULL;
      END $$""")
    op.execute('CREATE TRIGGER contributor_search_entity_changed AFTER INSERT OR UPDATE OR DELETE ON catalogue_entities '
               'FOR EACH ROW EXECUTE FUNCTION contributor_search_entity_changed()')
    # The new expression index has no expression statistics yet. Without this,
    # PostgreSQL can plan a full credit scan for every root during backfill.
    op.execute('ANALYZE catalogue_entities')
    # Bound temporary arrays while backfilling existing installations.
    op.execute("""DO $$ DECLARE batch integer[]; cursor_id integer := 0;
      BEGIN LOOP
        SELECT array_agg(id) INTO batch FROM (SELECT id FROM catalogue_entities WHERE id>cursor_id
          AND kind IN ('person','organization') ORDER BY id LIMIT 1000) e;
        EXIT WHEN batch IS NULL;
        PERFORM refresh_contributor_search(batch); SELECT max(id) INTO cursor_id FROM unnest(batch) id;
      END LOOP; END $$""")
    # Build once after backfill. Disable pending-list writes for this small,
    # read-heavy index so searches never scan an accumulated insertion queue.
    op.execute(f'CREATE INDEX ix_contributor_search_text ON catalogue_contributor_search USING gin '
               f'(search_text {quoted}.gin_trgm_ops) WITH (fastupdate=off) WHERE work_count > 0')
    op.execute('ANALYZE catalogue_contributor_search')


def downgrade():
    op.execute('DROP TRIGGER contributor_search_entity_changed ON catalogue_entities')
    op.execute('DROP FUNCTION contributor_search_entity_changed()')
    for event in ('insert','update','delete'):
        op.execute(f'DROP TRIGGER contributor_search_credit_{event} ON catalogue_credits')
        op.execute(f'DROP FUNCTION contributor_search_credit_{event}()')
    op.execute('DROP FUNCTION refresh_contributor_search(integer[])')
    op.execute('DROP TABLE catalogue_contributor_search')
    op.execute('DROP INDEX ix_catalogue_contributor_root')
