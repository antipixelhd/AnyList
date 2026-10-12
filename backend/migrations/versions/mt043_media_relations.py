"""Allow explicit cross-media source, adaptation and franchise relationships."""
from alembic import op
import sqlalchemy as sa

revision = 'mt043'
down_revision = 'mt042'
branch_labels = None
depends_on = None

OLD = "'dlc','expansion','standalone_expansion','remake','remaster','port','version','series_member','recommendation'"
NEW = OLD + ",'adaptation','based_on','prequel','sequel','spin_off','shared_universe'"


def upgrade():
    op.drop_constraint('ck_catalogue_relationship_type', 'catalogue_relationships', type_='check')
    op.create_check_constraint('ck_catalogue_relationship_type', 'catalogue_relationships', f'relation IN ({NEW})')


def downgrade():
    if op.get_bind().scalar(sa.text(f'SELECT EXISTS(SELECT 1 FROM catalogue_relationships WHERE relation NOT IN ({OLD}))')):
        raise RuntimeError('Review new media relationships before downgrading; existing data was preserved')
    op.drop_constraint('ck_catalogue_relationship_type', 'catalogue_relationships', type_='check')
    op.create_check_constraint('ck_catalogue_relationship_type', 'catalogue_relationships', f'relation IN ({OLD})')
