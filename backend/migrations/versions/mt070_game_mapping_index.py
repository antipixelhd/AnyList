"""Stage public game identity/title evidence and atomically publish generations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'mt070_game_mapping_index'
down_revision = 'mt069_game_achievements'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('game_mapping_generations',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('source', sa.String(24), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('observed_at', sa.DateTime(), nullable=False),
        sa.Column('published_at', sa.DateTime()),
        sa.Column('coverage', postgresql.JSONB(), nullable=False))
    op.create_index('ix_game_mapping_generations_source', 'game_mapping_generations', ['source'])
    op.create_table('game_mapping_references',
        sa.Column('generation', sa.String(40), sa.ForeignKey('game_mapping_generations.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('native', sa.String(200), primary_key=True),
        sa.Column('title', sa.Text(), nullable=False), sa.Column('year', sa.Integer()),
        sa.Column('names', postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column('imdb_ids', postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column('igdb_ids', postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column('data', postgresql.JSONB(), nullable=False))
    for suffix, column in [('names', 'names'), ('imdb', 'imdb_ids'), ('igdb', 'igdb_ids')]:
        op.create_index('ix_game_mapping_' + suffix, 'game_mapping_references', [column], postgresql_using='gin')
    op.create_index('ix_game_binding_dependencies', 'catalogue_identities',
                    [sa.text("(evidence->'dependencies')")], postgresql_using='gin')
    op.execute("""UPDATE catalogue_identities SET evidence=evidence || jsonb_build_object('dependencies',
        jsonb_build_array('imdb:' || external_id, 'igdb:' || (evidence->>'igdb_id')) ||
        CASE WHEN evidence->'wikidata'->>'wikidata_id' IS NOT NULL
          THEN jsonb_build_array('wikidata:' || (evidence->'wikidata'->>'wikidata_id')) ELSE '[]'::jsonb END)
        WHERE namespace='imdb.title' AND source='wikidata' AND evidence->>'igdb_id' IS NOT NULL
          AND NOT (evidence ? 'dependencies')""")


def downgrade():
    op.drop_index('ix_game_binding_dependencies', table_name='catalogue_identities')
    op.drop_table('game_mapping_references')
    op.drop_table('game_mapping_generations')
