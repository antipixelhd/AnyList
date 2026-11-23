"""Use the verified GOTY Steam app for the original Arkham Asylum work."""
from alembic import op

revision = 'mt104_arkham_steam_replacement'
down_revision = 'mt103_works_bundle_evidence'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('SELECT pg_advisory_xact_lock(735903601)')
    op.execute("""
        WITH arkham AS (
            SELECT game.id
            FROM catalogue_entities game
            JOIN catalogue_identities native ON native.entity_id = game.id
                AND native.namespace = 'igdb.game' AND native.external_id = '500'
            WHERE game.kind = 'game' AND game.attributes->>'game_class' = 'game'
              AND COALESCE(game.attributes->>'steam_primary_app_id', '35010') IN ('35010','35140')
              AND NOT (game.protected_fields ? 'attributes.steam_primary_app_id')
              AND game.field_sources->>'attributes.steam_primary_app_id' IS DISTINCT FROM 'manual'
        ), bound AS (
            INSERT INTO catalogue_identities (entity_id, namespace, external_id, source, evidence)
            SELECT id, 'steam.app', '35140', 'reviewed',
                '{"source":"steam_store_replacement","replaces":"35010"}'::jsonb
            FROM arkham
            ON CONFLICT ON CONSTRAINT uq_catalogue_identity DO NOTHING
            RETURNING entity_id
        )
        UPDATE catalogue_entities game
        SET attributes = game.attributes || '{"steam_primary_app_id":"35140"}'::jsonb,
            field_sources = game.field_sources || '{"attributes.steam_primary_app_id":"reviewed"}'::jsonb
        FROM arkham
        WHERE game.id = arkham.id
          AND game.attributes->>'steam_primary_app_id' IS DISTINCT FROM '35140'
          AND (game.id IN (SELECT entity_id FROM bound) OR EXISTS (
              SELECT 1 FROM catalogue_identities app WHERE app.entity_id = game.id
                AND app.namespace = 'steam.app' AND app.external_id = '35140'
          ))
    """)


def downgrade():
    # The verified identity may already own offer rows or be used by Steam sync.
    pass
