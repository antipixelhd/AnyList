"""Independent preference for season-completion rating popups."""
from alembic import op
import sqlalchemy as sa

revision = 'mt092_season_rating_popup'
down_revision = 'mt091_season_cursor'
branch_labels = depends_on = None


def upgrade():
    op.add_column('tracking_preferences', sa.Column('show_season_rating_popup', sa.Boolean(),
        nullable=False, server_default=sa.true()))
    # Preserve an existing popup opt-out; the two controls are independent
    # after migration, with both enabled by default for new accounts.
    op.execute('UPDATE tracking_preferences SET show_season_rating_popup = show_new_ratings_popup')


def downgrade():
    op.drop_column('tracking_preferences', 'show_season_rating_popup')
