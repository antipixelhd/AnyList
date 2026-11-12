"""Join season completion and game category migrations."""

revision = 'mt087_merge_game_categories'
down_revision = ('mt086_season_completion', 'mt086_game_categories')
branch_labels = depends_on = None


def upgrade():
    pass


def downgrade():
    pass
