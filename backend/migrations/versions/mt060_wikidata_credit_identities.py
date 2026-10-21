"""Typed Wikidata and IMDb people identities for verified game credits."""
from alembic import op
import sqlalchemy as sa

revision='mt060'
down_revision='mt059'
branch_labels=None
depends_on=None

ANCHOR="WHEN 'imdb.title' THEN ARRAY['movie','series']::text[]"
EXTRA=" WHEN 'imdb.person' THEN ARRAY['person']::text[] WHEN 'wikidata.item' THEN ARRAY['game','person','organization','character']::text[]"
GUARD="""IF NEW.namespace='wikidata.item' AND NEW.external_id !~ '^Q[1-9][0-9]*$' OR
            NEW.namespace='imdb.person' AND NEW.external_id !~ '^nm[0-9]+$' THEN
            RAISE EXCEPTION 'Invalid Wikidata or IMDb person identifier' USING ERRCODE='23514'; END IF;
        """


def replace_function(up):
    # Preserve every existing reference/performer/immutable-kind check. Do not
    # install an incomplete replacement in a schema missing its foundation.
    anchor=ANCHOR if up else ANCHOR+EXTRA
    replacement=ANCHOR+EXTRA if up else ANCHOR
    old="expected := CASE NEW.namespace" if up else GUARD+"expected := CASE NEW.namespace"
    new=GUARD+"expected := CASE NEW.namespace" if up else "expected := CASE NEW.namespace"
    def quote(value):return value.replace("'","''")
    op.execute(f"""DO $$ DECLARE original text; definition text; BEGIN
      original:=pg_get_functiondef('catalogue_validate_references()'::regprocedure);
      definition:=replace(original,'{quote(anchor)}','{quote(replacement)}');
      IF definition=original THEN RAISE EXCEPTION 'Catalogue identity definition changed'; END IF;
      original:=definition;
      definition:=replace(original,'{quote(old)}','{quote(new)}');
      IF definition=original THEN RAISE EXCEPTION 'Catalogue identity validation changed'; END IF;
      EXECUTE definition;
    END $$""")


def upgrade():
    op.add_column('catalogue_character_performances',sa.Column('source',sa.String(80),nullable=False,server_default='reviewed'))
    replace_function(True)


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM catalogue_identities WHERE namespace IN ('wikidata.item','imdb.person')) THEN
        RAISE EXCEPTION 'Retain verified identities before downgrading';
      END IF;
      IF EXISTS(SELECT 1 FROM catalogue_character_performances WHERE source<>'reviewed') THEN
        RAISE EXCEPTION 'Retain performance source evidence before downgrading';
      END IF;
    END $$""")
    replace_function(False)
    op.drop_column('catalogue_character_performances','source')
