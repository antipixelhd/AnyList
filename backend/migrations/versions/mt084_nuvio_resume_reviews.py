"""Reconcile queued Nuvio resume writes with their legacy reviews."""
from alembic import op

revision = 'mt084_nuvio_resume_reviews'
down_revision = 'mt083_snapshot_entity'
branch_labels = depends_on = None


def upgrade():
    # Pair each old review with the closest preceding conflicted upsert. Old
    # reviews had no action ID. Only requeue a pending review if a newer action
    # has not already replaced that write.
    op.execute("""
        WITH matched AS (
            SELECT review.id AS review_id, review.state AS review_state, action.*
            FROM tracking_reviews review
            JOIN media_server_connections conn ON conn.id = review.connection_id AND conn.type = 'nuvio'
            JOIN LATERAL (
                SELECT candidate.id, candidate.connection_id, candidate.media_id, candidate.created_at
                FROM tracking_stream_actions candidate
                WHERE candidate.user_id = review.user_id AND candidate.connection_id = review.connection_id
                  AND candidate.media_id = review.media_id AND candidate.state = 'conflict'
                  AND candidate.action = 'upsert' AND candidate.last_error = 'Playback changed since the last import'
                  AND candidate.created_at <= review.created_at
                ORDER BY candidate.created_at DESC, candidate.id DESC
                LIMIT 1
            ) action ON true
            WHERE review.kind = 'conflict'
              AND review.message = 'Playback changed on a connected account. The queued action was not applied; review the newer activity.'
        ), latest AS (
            SELECT DISTINCT ON (id) id, review_state, connection_id, media_id
            FROM matched ORDER BY id, review_id DESC
        ), planned AS (
            SELECT latest.id, latest.review_state = 'pending' AND NOT EXISTS (
                SELECT 1 FROM tracking_stream_actions other
                WHERE other.connection_id = latest.connection_id AND other.media_id = latest.media_id
                  AND other.action = 'upsert'
                  AND (other.state = 'pending' OR (other.id > latest.id AND other.state IN ('applied', 'conflict')))
            ) AS recheck
            FROM latest
        ), settled AS (
            UPDATE tracking_stream_actions action
            SET state = CASE WHEN planned.recheck THEN 'pending' ELSE 'cancelled' END,
                payload = CASE WHEN planned.recheck THEN action.payload ELSE '{}'::jsonb END,
                last_error = NULL
            FROM planned WHERE action.id = planned.id
            RETURNING action.id
        )
        UPDATE tracking_reviews review
        SET state = 'corrected', dismissed_at = timezone('UTC', now())
        FROM matched JOIN settled ON settled.id = matched.id
        WHERE review.id = matched.review_id AND review.state = 'pending'
    """)


def downgrade():
    # Resolved work stays resolved when rolling back the schema revision.
    pass
