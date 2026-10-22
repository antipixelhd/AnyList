"""Validated game IMDb bindings, without changing movie/series identities."""
from alembic import op

revision='mt063'
down_revision='mt062'
branch_labels=depends_on=None

OLD="WHEN 'imdb.title' THEN ARRAY['movie','series']::text[]"
NEW="WHEN 'imdb.title' THEN ARRAY['movie','series','game']::text[]"
GUARD="""IF NEW.namespace='imdb.title' AND catalogue_require_kind(NEW.entity_id,ARRAY['game'])
       AND (NEW.evidence->>'title_type') IS DISTINCT FROM 'videoGame' THEN
       RAISE EXCEPTION 'Game IMDb identity requires validated videoGame evidence' USING ERRCODE='23514'; END IF;
    """


def replace(up):
    quote=lambda value:value.replace("'","''")
    old,new=(OLD,NEW) if up else (NEW,OLD)
    anchor='expected := CASE NEW.namespace'
    before,after=(anchor,GUARD+anchor) if up else (GUARD+anchor,anchor)
    op.execute(f"""DO $$ DECLARE original text; definition text; BEGIN
      original:=pg_get_functiondef('catalogue_validate_references()'::regprocedure);
      definition:=replace(original,'{quote(old)}','{quote(new)}');
      IF definition=original THEN RAISE EXCEPTION 'Catalogue IMDb identity definition changed'; END IF;
      original:=definition;
      definition:=replace(original,'{quote(before)}','{quote(after)}');
      IF definition=original THEN RAISE EXCEPTION 'Catalogue IMDb guard definition changed'; END IF;
      EXECUTE definition;
    END $$""")


def upgrade():
    replace(True)
    op.execute("""CREATE UNIQUE INDEX uq_catalogue_game_imdb ON catalogue_identities(entity_id)
        WHERE namespace='imdb.title' AND evidence->>'title_type'='videoGame'""")


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM catalogue_identities i JOIN catalogue_entities e ON e.id=i.entity_id
          WHERE i.namespace='imdb.title' AND e.kind='game') THEN
        RAISE EXCEPTION 'Retain reviewed game IMDb identities before downgrading'; END IF;
    END $$""")
    op.drop_index('uq_catalogue_game_imdb',table_name='catalogue_identities')
    replace(False)
