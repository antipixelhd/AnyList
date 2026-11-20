"""Focus shared detail snapshot reads on their catalogue entity."""
from alembic import op

revision = 'mt083_snapshot_entity'
down_revision = 'mt082_steam_wishlist'
branch_labels = depends_on = None


def upgrade():
    op.create_index('ix_catalogue_snapshot_entity', 'catalogue_metadata_snapshots', ['entity_id'])


def downgrade():
    op.drop_index('ix_catalogue_snapshot_entity', table_name='catalogue_metadata_snapshots')
