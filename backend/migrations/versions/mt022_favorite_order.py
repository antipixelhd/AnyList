"""Persist profile favorite card ordering."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "mt022"
down_revision = "mt021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tracking_preferences",
        sa.Column(
            "favorite_order",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade():
    op.drop_column("tracking_preferences", "favorite_order")
