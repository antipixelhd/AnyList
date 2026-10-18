"""Enable all category exclusions by default without changing saved selections."""
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt055'
down_revision = 'mt054'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('user_profiles', 'excluded_categories', existing_type=JSONB(),
                    existing_nullable=False, server_default='[99, 10402, 10763, 10764, 10766, 10767, 10770]')


def downgrade():
    op.alter_column('user_profiles', 'excluded_categories', existing_type=JSONB(),
                    existing_nullable=False, server_default='[]')
