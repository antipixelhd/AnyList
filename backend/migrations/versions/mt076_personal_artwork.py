"""Account-specific game banners and profile background framing."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='mt076_personal_artwork'
down_revision='mt075_steam_progress'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('user_profiles',sa.Column('background_crop',JSONB(),nullable=True))
    op.add_column('user_profiles',sa.Column('game_banners',JSONB(),nullable=False,server_default='{}'))

def downgrade():
    op.drop_column('user_profiles','game_banners')
    op.drop_column('user_profiles','background_crop')
