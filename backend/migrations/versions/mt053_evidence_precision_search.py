"""Keep contributor job timestamps precise and index alternate title search."""
from alembic import op

revision = 'mt053'
down_revision = 'mt052'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('expires_at', 'next_attempt_at', 'lease_until'):
        op.execute(f"ALTER TABLE catalogue_contributor_evidence ALTER COLUMN {name} TYPE timestamp(3) USING date_trunc('milliseconds', {name})")
    op.execute('CREATE INDEX ix_media_original_title_trgm ON media USING gin (original_title gin_trgm_ops)')


def downgrade():
    op.execute('DROP INDEX ix_media_original_title_trgm')
    for name in ('expires_at', 'next_attempt_at', 'lease_until'):
        op.execute(f'ALTER TABLE catalogue_contributor_evidence ALTER COLUMN {name} TYPE timestamp')
