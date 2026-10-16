"""Persist public contributor evidence and bounded background verification."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'mt052'
down_revision = 'mt051'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('catalogue_contributor_evidence',
        sa.Column('kind', sa.String(16), primary_key=True),
        sa.Column('native_id', sa.Integer(), primary_key=True),
        sa.Column('variant', sa.String(64), primary_key=True),
        sa.Column('config', JSONB, nullable=False),
        sa.Column('payload', JSONB),
        sa.Column('requested_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('expires_at', sa.DateTime()),
        sa.Column('next_attempt_at', sa.DateTime()),
        sa.Column('lease_token', sa.String(40)),
        sa.Column('lease_until', sa.DateTime()),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'))
    op.create_index('ix_contributor_evidence_due', 'catalogue_contributor_evidence', ['next_attempt_at'])


def downgrade():
    op.drop_table('catalogue_contributor_evidence')
