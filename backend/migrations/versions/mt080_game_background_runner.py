"""Shared provider admission and resumable automatic mapping demand."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt080_game_background_runner'
down_revision = 'mt079_enrichment_retries'
branch_labels = None
depends_on = None


def upgrade():
    metadata=sa.MetaData()
    # Frozen definitions: replay never imports future application models.
    sa.Table('users',metadata,sa.Column('id',sa.Integer,primary_key=True))
    sa.Table('catalogue_entities',metadata,sa.Column('id',sa.Integer,primary_key=True))
    tables=[
        sa.Table('game_work_demand',metadata,
            sa.Column('key',sa.String(320),primary_key=True),sa.Column('origin',sa.Integer,nullable=False),
            sa.Column('priority',sa.Integer,nullable=False),sa.Column('owner',sa.String(80),nullable=False),
            sa.Column('active_until',sa.DateTime,nullable=False)),
        sa.Table('game_provider_tickets',metadata,
            sa.Column('id',sa.String(40),primary_key=True),sa.Column('provider',sa.String(32),nullable=False),
            sa.Column('priority',sa.Integer,nullable=False),sa.Column('owner',sa.String(80),nullable=False),
            sa.Column('scopes',JSONB,nullable=False),sa.Column('created_at',sa.DateTime,nullable=False),
            sa.Column('expires_at',sa.DateTime,nullable=False),sa.Index('ix_game_provider_ticket_live','provider','expires_at')),
        sa.Table('game_provider_turns',metadata,
            sa.Column('key',sa.String(600),primary_key=True),sa.Column('served_at',sa.DateTime,nullable=False)),
        sa.Table('game_mapper_state',metadata,
            sa.Column('id',sa.Integer,primary_key=True),sa.Column('enabled',sa.Boolean,nullable=False),
            sa.Column('payload',JSONB,nullable=False),sa.Column('heartbeat',sa.DateTime),
            sa.Column('lease_token',sa.String(40)),sa.Column('lease_until',sa.DateTime)),
        sa.Table('game_mapper_items',metadata,
            sa.Column('key',sa.String(100),primary_key=True),sa.Column('kind',sa.String(16),nullable=False),
            sa.Column('native',sa.String(40),nullable=False),
            sa.Column('entity_id',sa.Integer,sa.ForeignKey('catalogue_entities.id',ondelete='SET NULL')),
            sa.Column('category',sa.Integer,nullable=False),sa.Column('weight',sa.Integer,nullable=False),
            sa.Column('counts',JSONB,nullable=False),sa.Column('next_at',sa.DateTime,nullable=False),
            sa.Column('checked_at',sa.DateTime),sa.Column('processed',sa.Integer,nullable=False),
            sa.Column('quanta',sa.Integer,nullable=False),sa.Column('error',sa.String(80)),
            sa.Index('ix_game_mapper_due','next_at','category','weight')),
        sa.Table('game_mapper_interests',metadata,
            sa.Column('source',sa.String(120),primary_key=True),sa.Column('game',sa.String(40),primary_key=True),
            sa.Column('category',sa.Integer,nullable=False),
            sa.Column('user_id',sa.Integer,sa.ForeignKey('users.id',ondelete='CASCADE')),
            sa.Column('expires_at',sa.DateTime,nullable=False),
            sa.Index('ix_game_mapper_interest_game','game','expires_at'),
            sa.Index('ix_game_mapper_interest_scan','category','source','game')),
        sa.Table('game_mapper_edges',metadata,
            sa.Column('game',sa.String(40),primary_key=True),
            sa.Column('target',sa.String(100),sa.ForeignKey('game_mapper_items.key',ondelete='CASCADE'),primary_key=True),
            sa.Index('ix_game_mapper_edge_target','target')),
    ]
    for table in tables:table.create(op.get_bind(),checkfirst=True)


def downgrade():
    for table in ('game_mapper_edges', 'game_mapper_interests', 'game_mapper_items',
                  'game_mapper_state', 'game_provider_turns', 'game_provider_tickets', 'game_work_demand'):
        op.drop_table(table)
