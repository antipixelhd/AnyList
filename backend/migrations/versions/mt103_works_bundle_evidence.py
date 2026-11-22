"""Queue known and plausible duplicate pairs for provider verification."""
from alembic import op

revision = 'mt103_works_bundle_evidence'
down_revision = 'mt102_game_time_tenths'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO catalogue_metadata_snapshots AS snapshot
            (provider, kind, external_id, entity_id, next_attempt_at, coverage)
        SELECT 'igdb', 'game', native.external_id, game.id, CURRENT_TIMESTAMP AT TIME ZONE 'UTC',
            '{"priority":"background"}'::jsonb
        FROM catalogue_entities AS game
        JOIN catalogue_identities AS native ON native.entity_id = game.id
            AND native.namespace = 'igdb.game' AND native.external_id = game.attributes->>'igdb_id'
        WHERE game.kind = 'game' AND game.attributes->>'game_class' IN ('expanded_game','remaster')
          AND (
            (game.field_sources->>'attributes.igdb_edition_sources' = 'igdb'
              AND game.attributes->'igdb_edition_sources'->>'parent_igdb_id' IS NOT NULL
              AND game.attributes->'igdb_edition_sources'->>'steam_bundle_id' IS NOT NULL
              AND game.attributes->'igdb_edition_sources'->>'steam_app_id' IS NOT NULL)
            OR (game.attributes->'igdb_edition_sources' IS NULL AND EXISTS (
                SELECT 1 FROM catalogue_credits AS edition_credit
                JOIN catalogue_entities AS studio ON studio.id = edition_credit.contributor_id
                    AND studio.kind = 'organization'
                JOIN catalogue_credits AS bundle_credit ON bundle_credit.contributor_id = studio.id
                JOIN catalogue_entities AS bundle ON bundle.id = bundle_credit.work_id
                    AND bundle.kind = 'game' AND bundle.attributes->>'game_class' = 'bundle'
                    AND lower(bundle.name) = lower(game.name)
                JOIN catalogue_identities AS app ON app.entity_id = bundle.id AND app.namespace = 'steam.app'
                WHERE edition_credit.work_id = game.id
                  AND edition_credit.role IN ('developer','publisher','supporting','porting')
                  AND bundle_credit.role IN ('developer','publisher','supporting','porting')
                  AND edition_credit.attributes->>'source_active' IS DISTINCT FROM 'false'
                  AND bundle_credit.attributes->>'source_active' IS DISTINCT FROM 'false'
            ))
          )
        ON CONFLICT ON CONSTRAINT uq_catalogue_snapshot DO UPDATE
        SET expires_at = NULL, next_attempt_at = EXCLUDED.next_attempt_at,
            coverage = (snapshot.coverage - 'primary_only') || EXCLUDED.coverage
        WHERE snapshot.entity_id = EXCLUDED.entity_id AND snapshot.next_attempt_at IS NULL
          AND (snapshot.lease_until IS NULL OR snapshot.lease_until <= CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
          AND snapshot.coverage->>'works_bundle_version' IS DISTINCT FROM '1'
    """)


def downgrade():
    # Queued game metadata refreshes are valid on either revision.
    pass
