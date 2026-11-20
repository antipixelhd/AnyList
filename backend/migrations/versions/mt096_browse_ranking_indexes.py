"""Index fixed public ranking expressions for local browse."""
from alembic import op

revision = 'mt096_browse_ranking_indexes'
down_revision = 'mt095_metadata_revision_shards'
branch_labels = None
depends_on = None


# Frozen DDL: future expression changes require a new migration.
def metric_sql(document, field):
    if field not in ('votes', 'popularity_rank'):
        raise ValueError('Unsupported ranking field')
    raw = f"btrim({document} #>> '{{imdb,{field}}}')"
    # Unknown/malformed legacy evidence must not break writes when an index
    # evaluates it. Restrict numeric parsing before casting; prevent overflow.
    numeric = f"length({raw}) <= 128 AND {raw} ~ '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]{{1,3}})?$'"
    return (f"CASE WHEN {numeric} THEN CASE WHEN abs(({raw})::numeric) > 1.7976931348623157e308 THEN NULL "
            f"WHEN abs(({raw})::numeric) < 4.9406564584124654e-324 THEN 0::double precision "
            f"ELSE ({raw})::double precision END END")



def upgrade():
    for name, field, direction in (('idx_media_imdb_votes', 'votes', 'DESC'),
                                   ('idx_media_imdb_rank', 'popularity_rank', 'ASC')):
        expression = metric_sql('tmdb_data', field)
        op.execute(f'CREATE INDEX {name} ON media (media_type, ({expression}) {direction} NULLS LAST, id) WHERE adult IS FALSE')


def downgrade():
    op.drop_index('idx_media_imdb_rank', table_name='media')
    op.drop_index('idx_media_imdb_votes', table_name='media')
