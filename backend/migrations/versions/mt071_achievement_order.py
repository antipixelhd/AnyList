"""Preserve Steam schema ordering independently of achievement identities."""
from alembic import op
import sqlalchemy as sa

revision = 'mt071_achievement_order'
down_revision = 'mt070_game_mapping_index'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('game_achievements', sa.Column('sort_order', sa.Integer(), nullable=True))
    op.execute("""
        UPDATE game_achievements AS achievement
        SET sort_order = definition.ordinality - 1
        FROM catalogue_metadata_snapshots AS snapshot,
             LATERAL jsonb_array_elements(snapshot.payload->'definitions')
                 WITH ORDINALITY AS definition(value, ordinality)
        WHERE snapshot.provider = 'steam' AND snapshot.kind = 'game_achievement_schema'
          AND snapshot.entity_id = achievement.work_id
          AND snapshot.coverage->>'steam_app_id' = achievement.app_id
          AND definition.value->>'api_name' = achievement.api_name
    """)


def downgrade():
    op.drop_column('game_achievements', 'sort_order')
