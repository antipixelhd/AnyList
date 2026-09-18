"""Store field-level rating conflicts for cloud reconciliation."""
from alembic import op
import sqlalchemy as sa

revision = "mt006"
down_revision = "mt005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tracking_reviews", sa.Column("previous_score", sa.Float(), nullable=True))
    op.add_column("tracking_reviews", sa.Column("proposed_score", sa.Float(), nullable=True))
    op.add_column("tracking_reviews", sa.Column("season_number", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("tracking_reviews", "season_number")
    op.drop_column("tracking_reviews", "proposed_score")
    op.drop_column("tracking_reviews", "previous_score")
