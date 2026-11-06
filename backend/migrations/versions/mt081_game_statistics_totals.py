"""Invalidate Games statistics when component-app denominators change."""
import importlib
from alembic import op

revision = 'mt081_game_statistics_totals'
down_revision = 'mt080_game_background_runner'
branch_labels = depends_on = None


def change(reverse=False):
    edit = importlib.import_module('migrations.versions.mt061_game_contributor_search').edit_function
    pairs = [(f"{a}.attributes->'achievement_catalogue'->'total'",
        f"{a}.attributes->'achievement_catalogue'->'total',{a}.attributes->'steam_progress_apps',{a}.attributes->'steam_progress_totals'")
        for a in ('n', 'o')]
    edit('game_stats_entity_metadata_changed()', [(new, old, 1) if reverse else (old, new, 1) for old, new in pairs])


def upgrade():
    change()
    op.execute("UPDATE game_stats_state SET payload=NULL,next_due_at=now() AT TIME ZONE 'UTC'")


def downgrade():
    change(True)
