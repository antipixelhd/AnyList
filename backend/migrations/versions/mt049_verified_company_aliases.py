"""Preserve company URLs when authoritative provider IDs identify one company."""
from alembic import op
import sqlalchemy as sa

revision = 'mt049'
down_revision = 'mt048'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('catalogue_entities', sa.Column('canonical_company_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_catalogue_company_alias', 'catalogue_entities', 'catalogue_entities',
                          ['canonical_company_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_catalogue_entities_canonical_company_id', 'catalogue_entities', ['canonical_company_id'])
    op.create_check_constraint('ck_catalogue_company_alias', 'catalogue_entities',
        "canonical_company_id IS NULL OR (kind = 'organization' AND canonical_company_id <> id)")


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM catalogue_entities WHERE canonical_company_id IS NOT NULL)')):
        raise RuntimeError('Verified company aliases must be preserved before downgrading')
    op.drop_constraint('ck_catalogue_company_alias', 'catalogue_entities')
    op.drop_index('ix_catalogue_entities_canonical_company_id', 'catalogue_entities')
    op.drop_constraint('fk_catalogue_company_alias', 'catalogue_entities', type_='foreignkey')
    op.drop_column('catalogue_entities', 'canonical_company_id')
