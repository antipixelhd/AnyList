"""Private sync review and per-connection baseline storage."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='mt002'
down_revision='mt001'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('tracking_preferences',sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='CASCADE'),primary_key=True),sa.Column('auto_confirm',sa.Boolean(),nullable=False,server_default='false'))
    op.create_table('tracking_baselines',
        sa.Column('connection_id',sa.Integer(),sa.ForeignKey('media_server_connections.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('snapshot',JSONB(),nullable=False,server_default='{}'),sa.Column('approved',sa.Boolean(),nullable=False,server_default='false'),
        sa.Column('observed_at',sa.DateTime(),nullable=False,server_default=sa.func.now()))
    op.create_index('ix_tracking_baselines_user_id','tracking_baselines',['user_id'])
    op.create_table('tracking_reviews',
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('media_id',sa.Integer(),sa.ForeignKey('media.id',ondelete='CASCADE')),sa.Column('connection_id',sa.Integer(),sa.ForeignKey('media_server_connections.id',ondelete='CASCADE')),
        sa.Column('kind',sa.String(32),nullable=False),sa.Column('state',sa.String(16),nullable=False,server_default='pending'),
        sa.Column('previous_status',sa.String(16)),sa.Column('proposed_status',sa.String(16)),sa.Column('message',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False,server_default=sa.func.now()))
    op.create_index('ix_tracking_reviews_user_id','tracking_reviews',['user_id'])


def downgrade():
    for table in ['tracking_reviews','tracking_baselines','tracking_preferences']:op.drop_table(table)
