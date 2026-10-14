"""Preserve credit-level selection evidence and episode contribution counts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt048'
down_revision = 'mt047'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('catalogue_credits', sa.Column('attributes', JSONB(), nullable=False, server_default='{}'))
    # Reproject cached provider responses on the next credit repair, without
    # requiring another provider request or deleting preserved source credits.
    op.execute("UPDATE catalogue_metadata_snapshots SET coverage = coverage - 'detail_credits_version' "
               "WHERE provider IN ('tmdb', 'tvdb') AND kind IN ('movie', 'series')")


def downgrade():
    op.drop_column('catalogue_credits', 'attributes')
