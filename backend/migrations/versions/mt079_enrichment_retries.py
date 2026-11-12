"""Repair stale failure state and retry jobs affected by corrected validators."""
from alembic import op

revision = 'mt079_enrichment_retries'
down_revision = 'mt078_manual_game_health'
branch_labels = None
depends_on = None


def upgrade():
    # Existing queued work starts with a bounded demand window. No provider
    # deadline, in-flight lease or source-cache record is changed here.
    op.execute("""UPDATE catalogue_metadata_snapshots SET coverage=coverage ||
      jsonb_build_object('requested_at',timezone('UTC',now()))
      WHERE next_attempt_at IS NOT NULL AND NOT (coverage ? 'requested_at')
      AND kind IN ('game','game_credit_job','game_ratings_job','game_achievement_job','game_info_job','price')
      AND (lease_until IS NULL OR lease_until<=timezone('UTC',now()))""")
    # Settled discovery outcomes are not transport failures. Preserve their
    # cooldowns, mappings and all already-published credits.
    op.execute("""UPDATE catalogue_metadata_snapshots SET error_code=NULL,attempts=0,
      coverage=coverage || '{"retry_mode":"on_demand"}'::jsonb
      WHERE provider='imdb' AND kind='game_credit_job' AND error_code IS NOT NULL
      AND next_attempt_at IS NULL AND coverage->>'state' IN ('unmapped','unresolved','conflict','excluded')
      AND (lease_until IS NULL OR lease_until<=timezone('UTC',now()))""")
    # Retire only verified aliases with an explicit canonical identity on the
    # same root; genuine conflicting/missing identities still need attention.
    op.execute("""UPDATE catalogue_metadata_snapshots s SET error_code=NULL,next_attempt_at=NULL,
      coverage=s.coverage || jsonb_build_object('state','superseded','canonical_igdb_id',e.attributes->>'igdb_id')
      FROM catalogue_entities e WHERE s.entity_id=e.id AND s.provider='igdb' AND s.kind='game'
      AND s.error_code='identity_conflict' AND s.external_id<>e.attributes->>'igdb_id'
      AND EXISTS (SELECT 1 FROM catalogue_identities i WHERE i.entity_id=e.id
         AND i.namespace='igdb.game' AND i.external_id=s.external_id)
      AND EXISTS (SELECT 1 FROM catalogue_identities i WHERE i.entity_id=e.id
         AND i.namespace='igdb.game' AND i.external_id=e.attributes->>'igdb_id')
      AND (s.lease_until IS NULL OR s.lease_until<=timezone('UTC',now()))""")
    # Give demanded work one immediate attempt with the fixed implementation,
    # rather than applying long content cooldowns to old implementation errors.
    op.execute("""UPDATE catalogue_metadata_snapshots s SET next_attempt_at=timezone('UTC',now()),
      attempts=0, coverage=s.coverage || jsonb_build_object('same_failure_count',0)
      WHERE s.next_attempt_at IS NOT NULL AND (s.lease_until IS NULL OR s.lease_until<=timezone('UTC',now()))
      AND ((s.provider='imdb' AND s.kind='game_credit_job' AND s.error_code='identity_conflict'
          AND EXISTS (SELECT 1 FROM catalogue_entities e WHERE e.id=s.entity_id
             AND e.attributes->>'game_class' IN ('dlc','expansion','episode','update')))
       OR (s.provider='tvdb' AND s.kind='character_art' AND s.error_code='identity_conflict')
       OR (s.provider='steam' AND s.kind='game_ratings_job' AND s.error_code='bad_response'))""")


def downgrade():
    # Operational failure counters/cooldowns cannot be reconstructed safely.
    pass
