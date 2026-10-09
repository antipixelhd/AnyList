"""Allow typed Open Library identities and durable daily provider accounting.

Revision ID: mt038
Revises: mt037
"""

from alembic import op
import sqlalchemy as sa

revision = "mt038"
down_revision = "mt037"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "catalogue_provider_budgets", sa.Column("day", sa.String(10), nullable=True)
    )
    op.add_column(
        "catalogue_provider_budgets",
        sa.Column(
            "daily_request_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.execute("""
CREATE OR REPLACE FUNCTION catalogue_validate_references() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE expected text[];
BEGIN
    IF TG_TABLE_NAME='catalogue_entities' THEN
        IF NEW.kind<>OLD.kind THEN RAISE EXCEPTION 'Canonical entity kind is immutable' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_identities' THEN
        expected := CASE NEW.namespace
            WHEN 'tmdb.movie' THEN ARRAY['movie']::text[]
            WHEN 'tmdb.series' THEN ARRAY['series']::text[]
            WHEN 'tvdb.series' THEN ARRAY['series']::text[]
            WHEN 'imdb.title' THEN ARRAY['movie','series']::text[]
            WHEN 'tmdb.person' THEN ARRAY['person']::text[]
            WHEN 'tvdb.person' THEN ARRAY['person']::text[]
            WHEN 'tmdb.company' THEN ARRAY['organization']::text[]
            WHEN 'tmdb.network' THEN ARRAY['organization']::text[]
            WHEN 'tvdb.company' THEN ARRAY['organization']::text[]
            WHEN 'igdb.game' THEN ARRAY['game']::text[]
            WHEN 'igdb.character' THEN ARRAY['character']::text[]
            WHEN 'igdb.company' THEN ARRAY['organization']::text[]
            WHEN 'igdb.collection' THEN ARRAY['game_collection']::text[]
            WHEN 'igdb.release' THEN ARRAY['release']::text[]
            WHEN 'igdb.game_version' THEN ARRAY['release']::text[]
            WHEN 'rawg.game' THEN ARRAY['game']::text[]
            WHEN 'rawg.developer' THEN ARRAY['organization']::text[]
            WHEN 'rawg.publisher' THEN ARRAY['organization']::text[]
            WHEN 'rawg.release' THEN ARRAY['release']::text[]
            WHEN 'openlibrary.book' THEN ARRAY['book']::text[]
            WHEN 'openlibrary.edition' THEN ARRAY['edition']::text[]
            WHEN 'openlibrary.author' THEN ARRAY['person']::text[]
            WHEN 'hardcover.book' THEN ARRAY['book']::text[]
            WHEN 'hardcover.author' THEN ARRAY['person']::text[]
            WHEN 'hardcover.publisher' THEN ARRAY['organization']::text[]
            WHEN 'hardcover.character' THEN ARRAY['character']::text[]
            WHEN 'hardcover.edition' THEN ARRAY['edition']::text[]
            WHEN 'hardcover.series' THEN ARRAY['book_series']::text[]
            WHEN 'isbn.10' THEN ARRAY['edition']::text[]
            WHEN 'isbn.13' THEN ARRAY['edition']::text[]
            WHEN 'steam.app' THEN ARRAY['game']::text[]
            WHEN 'itad.game' THEN ARRAY['game']::text[]
            ELSE ARRAY[]::text[] END;
        IF NOT catalogue_require_kind(NEW.entity_id,expected) THEN RAISE EXCEPTION 'Identity namespace/kind mismatch' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_book_editions' THEN
        IF NOT catalogue_require_kind(NEW.entity_id,ARRAY['edition']) OR NOT catalogue_require_kind(NEW.work_id,ARRAY['book'])
           OR (NEW.publisher_id IS NOT NULL AND NOT catalogue_require_kind(NEW.publisher_id,ARRAY['organization']))
        THEN RAISE EXCEPTION 'Invalid book edition references' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_game_releases' THEN
        IF NOT catalogue_require_kind(NEW.entity_id,ARRAY['release']) OR NOT catalogue_require_kind(NEW.work_id,ARRAY['game'])
        THEN RAISE EXCEPTION 'Invalid game release references' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_credits' THEN
        IF NOT catalogue_require_kind(NEW.work_id,ARRAY['movie','series','game','book']) OR NOT catalogue_require_kind(NEW.contributor_id,ARRAY['person','organization'])
           OR (NEW.edition_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM catalogue_book_editions WHERE entity_id=NEW.edition_id AND work_id=NEW.work_id))
        THEN RAISE EXCEPTION 'Invalid contributor references' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_character_appearances' THEN
        IF NOT catalogue_require_kind(NEW.work_id,ARRAY['movie','series','game','book']) OR NOT catalogue_require_kind(NEW.character_id,ARRAY['character'])
        THEN RAISE EXCEPTION 'Invalid fictional character references' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_character_performances' THEN
        IF NOT EXISTS(SELECT 1 FROM catalogue_credits c JOIN catalogue_entities e ON e.id=c.contributor_id
                      WHERE c.id=NEW.credit_id AND c.work_id=NEW.work_id AND e.kind='person' AND c.role IN ('actor','voice_actor','narrator'))
        THEN RAISE EXCEPTION 'Invalid performer references' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_relationships' THEN
        IF NEW.relation='series_member' THEN
            IF NOT ((catalogue_require_kind(NEW.source_id,ARRAY['book']) AND catalogue_require_kind(NEW.target_id,ARRAY['book_series']))
                OR (catalogue_require_kind(NEW.source_id,ARRAY['game']) AND catalogue_require_kind(NEW.target_id,ARRAY['game_collection'])))
            THEN RAISE EXCEPTION 'Invalid series membership' USING ERRCODE='23514'; END IF;
        ELSIF NEW.relation='recommendation' THEN
            IF NOT EXISTS(SELECT 1 FROM catalogue_entities s JOIN catalogue_entities t ON s.kind=t.kind
                          WHERE s.id=NEW.source_id AND t.id=NEW.target_id AND s.kind IN ('movie','series','game','book'))
            THEN RAISE EXCEPTION 'Invalid recommendation references' USING ERRCODE='23514'; END IF;
        ELSIF NOT catalogue_require_kind(NEW.source_id,ARRAY['game']) OR NOT catalogue_require_kind(NEW.target_id,ARRAY['game'])
        THEN RAISE EXCEPTION 'Game relationship requires game works' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_show_links' THEN
        IF NOT catalogue_require_kind(NEW.entity_id,ARRAY['series']) THEN RAISE EXCEPTION 'Show bridge requires a series' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_legacy_links' THEN
        IF NOT EXISTS(SELECT 1 FROM media m JOIN catalogue_entities e ON e.kind=m.media_type::text WHERE m.id=NEW.media_id AND e.id=NEW.entity_id)
        THEN RAISE EXCEPTION 'Legacy bridge kind mismatch' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='catalogue_steam_prices' THEN
        IF NOT catalogue_require_kind(NEW.work_id,ARRAY['game']) OR NOT EXISTS(SELECT 1 FROM catalogue_identities WHERE namespace='steam.app' AND external_id=NEW.steam_app_id AND entity_id=NEW.work_id)
        THEN RAISE EXCEPTION 'Price requires a verified Steam game identity' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$
    """)


def downgrade():
    raise RuntimeError("Review retained Open Library identities before downgrading")
