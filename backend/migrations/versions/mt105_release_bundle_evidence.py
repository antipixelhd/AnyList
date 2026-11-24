"""Queue existing same-title bundles for single-game release verification."""
from alembic import op


revision = 'mt105_release_bundle_evidence'
down_revision = 'mt104_arkham_steam_replacement'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO catalogue_metadata_snapshots AS snapshot
            (provider, kind, external_id, entity_id, next_attempt_at, coverage)
        SELECT 'igdb', 'game', native.external_id, bundle.id, CURRENT_TIMESTAMP AT TIME ZONE 'UTC',
            '{"priority":"background"}'::jsonb
        FROM catalogue_entities AS bundle
        JOIN catalogue_identities AS native ON native.entity_id = bundle.id
            AND native.namespace = 'igdb.game' AND native.external_id = bundle.attributes->>'igdb_id'
        WHERE bundle.kind = 'game' AND bundle.attributes->>'game_class' = 'bundle'
          AND bundle.attributes->'game_bundle_edition' IS NULL
          AND EXISTS (
              SELECT 1 FROM catalogue_entities AS original
              JOIN catalogue_identities AS original_id ON original_id.entity_id = original.id
                AND original_id.namespace = 'igdb.game'
              WHERE original.kind = 'game' AND original.attributes->>'game_class' = 'game'
                AND lower(original.name) = lower(bundle.name)
          )
        ON CONFLICT ON CONSTRAINT uq_catalogue_snapshot DO UPDATE
        SET expires_at = NULL, next_attempt_at = EXCLUDED.next_attempt_at,
            coverage = (snapshot.coverage - 'primary_only') || EXCLUDED.coverage
        WHERE snapshot.entity_id = EXCLUDED.entity_id AND snapshot.next_attempt_at IS NULL
          AND (snapshot.lease_until IS NULL OR snapshot.lease_until <= CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
          AND snapshot.coverage->>'release_bundle_version' IS DISTINCT FROM '1'
    """)


def downgrade():
    # Older workers can process the same metadata refresh requests.
    pass
