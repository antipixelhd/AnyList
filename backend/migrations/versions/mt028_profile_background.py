"""Store profile banner image URLs."""
from alembic import op
import sqlalchemy as sa

revision = "mt028"
down_revision = "mt027"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("user_profiles", sa.Column("background_url", sa.String(2048), nullable=True))

def downgrade():
    op.drop_column("user_profiles", "background_url")
