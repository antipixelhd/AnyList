"""Allow Played game entries and listed expansion state."""
from alembic import op

revision = 'mt086_game_categories'
down_revision = 'mt085_merge_sync_backport'
branch_labels = depends_on = None


def constraints(statuses):
    for table, name in (('game_entries', 'ck_game_entry_status'), ('game_child_states', 'ck_game_child_status')):
        op.drop_constraint(name, table, type_='check')
        op.create_check_constraint(name, table, f"status IN ({statuses})")


def upgrade():
    constraints("'planning','playing','played','paused','completed','dropped'")


def downgrade():
    # Refuse a lossy downgrade rather than reclassifying the owner's decisions.
    constraints("'planning','playing','paused','completed','dropped'")
