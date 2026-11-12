"""Preserve explicit update-to-game relationships for contributor credits."""
from alembic import op

revision = 'mt072_game_update_credits'
down_revision = 'mt071_achievement_order'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_catalogue_relationship_type', 'catalogue_relationships', type_='check')
    op.create_check_constraint('ck_catalogue_relationship_type', 'catalogue_relationships',
        "relation IN ('dlc','expansion','update','standalone_expansion','remake','remaster','port','version','series_member','recommendation','adaptation','based_on','prequel','sequel','spin_off','shared_universe')")


def downgrade():
    op.execute("DELETE FROM catalogue_relationships WHERE relation = 'update'")
    op.drop_constraint('ck_catalogue_relationship_type', 'catalogue_relationships', type_='check')
    op.create_check_constraint('ck_catalogue_relationship_type', 'catalogue_relationships',
        "relation IN ('dlc','expansion','standalone_expansion','remake','remaster','port','version','series_member','recommendation','adaptation','based_on','prequel','sequel','spin_off','shared_universe')")
