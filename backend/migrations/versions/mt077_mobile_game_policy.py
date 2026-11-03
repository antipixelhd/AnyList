"""Keep contributor search qualification aligned with mobile game exclusions."""
import importlib
from alembic import op

revision='mt077_mobile_game_policy'
down_revision='mt076_personal_artwork'
branch_labels=None
depends_on=None


def change(reverse=False):
    edit=importlib.import_module('migrations.versions.mt061_game_contributor_search').edit_function
    replacements=[
        ("WHERE c.attributes->>'source_active' IS DISTINCT FROM 'false'",
         "WHERE c.attributes->>'source_active' IS DISTINCT FROM 'false' AND w.attributes->>'mobile_only' IS DISTINCT FROM 'true'",1),
        ("AND w.attributes->>'adult' IS DISTINCT FROM 'true'",
         "AND w.attributes->>'adult' IS DISTINCT FROM 'true' AND w.attributes->>'mobile_only' IS DISTINCT FROM 'true'",1),
        ("base.kind='game'", "base.kind='game' AND base.attributes->>'mobile_only' IS DISTINCT FROM 'true'",1),
    ]
    trigger=[(f"{alias}.attributes->'adult'",f"{alias}.attributes->'adult',{alias}.attributes->'mobile_only'",1) for alias in ('n','o')]
    if reverse:
        replacements=[(new,old,count) for old,new,count in replacements]
        trigger=[(new,old,count) for old,new,count in trigger]
    edit('refresh_game_contributor_search(integer[])',replacements)
    edit('contributor_game_work_changed()',trigger)
    op.execute('SELECT refresh_game_contributor_search(ARRAY(SELECT entity_id FROM catalogue_contributor_search WHERE game_work_count>0))')


def upgrade():change()
def downgrade():change(reverse=True)
