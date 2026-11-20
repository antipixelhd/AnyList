"""Preserve acknowledgements without treating thanks as game work participation."""
from alembic import op

revision='mt064'
down_revision='mt063'
branch_labels=depends_on=None
OLD="FROM works WHERE root_kind='person' OR role IN"
NEW="FROM works WHERE (root_kind='person' AND role<>'thanks') OR role IN"


def apply(up):
    old,new=(OLD,NEW) if up else (NEW,OLD)
    old,new=old.replace("'","''"),new.replace("'","''")
    op.execute(f"""DO $$ DECLARE original text; definition text; BEGIN
      original:=pg_get_functiondef('refresh_game_contributor_search(integer[])'::regprocedure);
      definition:=replace(original,'{old}','{new}');
      IF definition=original THEN RAISE EXCEPTION 'Game contribution summary definition changed'; END IF;
      EXECUTE definition;
    END $$""")
    op.execute("""DO $$ DECLARE batch integer[]; cursor_id integer:=0;
    BEGIN LOOP
      SELECT array_agg(id) INTO batch FROM (SELECT id FROM catalogue_entities WHERE id>cursor_id
        AND kind IN ('person','organization') AND canonical_person_id IS NULL AND canonical_company_id IS NULL
        ORDER BY id LIMIT 500) s;
      EXIT WHEN batch IS NULL;
      PERFORM refresh_game_contributor_search(batch);
      SELECT max(id) INTO cursor_id FROM unnest(batch) id;
    END LOOP; END $$""")


def upgrade():apply(True)
def downgrade():apply(False)
