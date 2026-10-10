"""Keep character artwork on the title/performer credit and invalidate stats."""
from alembic import op
import sqlalchemy as sa

revision = "mt041"
down_revision = "mt040"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("catalogue_credits", sa.Column("character_image_url", sa.String(2048)))
    op.execute("""DO $$ DECLARE definition text; original text; BEGIN
      IF to_regprocedure('catalogue_validate_references()') IS NOT NULL THEN
        original := pg_get_functiondef('catalogue_validate_references()'::regprocedure);
        definition := replace(original,
          'WHEN ''tvdb.series'' THEN ARRAY[''series'']::text[]',
          'WHEN ''tvdb.series'' THEN ARRAY[''series'']::text[] WHEN ''tvdb.movie'' THEN ARRAY[''movie'']::text[]');
        IF definition = original THEN RAISE EXCEPTION 'Catalogue identity validation definition changed'; END IF;
        EXECUTE definition;
      END IF;
    END $$""")
    # Catalogue-only test installations can apply this column without stats.
    op.execute("""DO $$ BEGIN
      IF to_regprocedure('stats_metadata_changed()') IS NOT NULL THEN
        CREATE TRIGGER stats_metadata_changed AFTER INSERT OR UPDATE OR DELETE ON catalogue_credits
          FOR EACH STATEMENT EXECUTE FUNCTION stats_metadata_changed();
        CREATE TRIGGER stats_metadata_changed AFTER INSERT OR UPDATE OR DELETE ON title_credits
          FOR EACH STATEMENT EXECUTE FUNCTION stats_metadata_changed();
      END IF;
    END $$""")


def downgrade():
    raise RuntimeError("Role artwork and movie identities require reviewed data retention before downgrade")
