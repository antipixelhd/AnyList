"""Versioned season cards for actor filmographies."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'mt098_screen_actor_appearances'
down_revision = 'mt097_series_list_status'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'screen_actor_appearances',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('series_id', sa.Integer(), nullable=False),
        sa.Column('projection', postgresql.JSONB(), nullable=False),
    )
    op.create_index('ix_screen_actor_appearance_lookup', 'screen_actor_appearances', ['person_id', 'series_id', 'id'])


def downgrade():
    op.drop_table('screen_actor_appearances')
