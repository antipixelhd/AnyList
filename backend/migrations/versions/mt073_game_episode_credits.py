"""Preserve explicit episode-to-game relationships for contributor credits."""
from alembic import op
import sqlalchemy as sa

revision = 'mt073_game_episode_credits'
down_revision = 'mt072_game_update_credits'
branch_labels = None
depends_on = None

OLD = "'dlc','expansion','update','standalone_expansion','remake','remaster','port','version','series_member','recommendation','adaptation','based_on','prequel','sequel','spin_off','shared_universe'"


def upgrade():
    op.drop_constraint('ck_catalogue_relationship_type', 'catalogue_relationships', type_='check')
    op.create_check_constraint('ck_catalogue_relationship_type', 'catalogue_relationships',
        f"relation IN ({OLD},'episode')")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM catalogue_relationships WHERE relation='episode')")):
        raise RuntimeError('Episode relationships prevent downgrade; existing data was preserved')
    op.drop_constraint('ck_catalogue_relationship_type', 'catalogue_relationships', type_='check')
    op.create_check_constraint('ck_catalogue_relationship_type', 'catalogue_relationships', f'relation IN ({OLD})')
