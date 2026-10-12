"""Keep verified cross-provider person aliases without deleting credit history."""
from alembic import op
import sqlalchemy as sa

revision = 'mt045'
down_revision = 'mt044'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('catalogue_entities', sa.Column('canonical_person_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_catalogue_canonical_person', 'catalogue_entities', 'catalogue_entities',
                          ['canonical_person_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_catalogue_entities_canonical_person_id', 'catalogue_entities', ['canonical_person_id'])
    op.create_check_constraint('ck_catalogue_person_alias', 'catalogue_entities',
                               "canonical_person_id IS NULL OR (kind = 'person' AND canonical_person_id <> id)")


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM catalogue_entities WHERE canonical_person_id IS NOT NULL)')):
        raise RuntimeError('Verified person aliases must be preserved before downgrading')
    op.drop_constraint('ck_catalogue_person_alias', 'catalogue_entities', type_='check')
    op.drop_index('ix_catalogue_entities_canonical_person_id', table_name='catalogue_entities')
    op.drop_constraint('fk_catalogue_canonical_person', 'catalogue_entities', type_='foreignkey')
    op.drop_column('catalogue_entities', 'canonical_person_id')
