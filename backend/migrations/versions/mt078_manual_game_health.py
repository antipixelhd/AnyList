"""Retire legacy automatic Steam-only identity repair jobs."""
from alembic import op

revision = 'mt078_manual_game_health'
down_revision = 'mt077_mobile_game_policy'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""UPDATE catalogue_metadata_snapshots
        SET next_attempt_at=NULL,lease_token=NULL,lease_until=NULL,error_code=NULL,
            coverage=coalesce(coverage,'{}'::jsonb)||jsonb_build_object('state','manual_review','complete',true)
        WHERE provider='igdb' AND kind='game_steam_identity'""")


def downgrade():
    # Retired jobs must not be silently restarted by a downgrade. A deliberate
    # request in older code can admit new work if an operator restores it.
    pass
