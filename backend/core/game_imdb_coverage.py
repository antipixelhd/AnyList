"""IMDb title coverage for games with their own catalogue detail page."""

from sqlalchemy import String, and_, case, cast, func, or_, select, true
from sqlalchemy.orm import aliased

from core.game_edition_sources import BUNDLE_ALIAS
from core.game_imdb_bindings import verified_bindings
from core.game_imdb_enrichment import COMPONENT, JOB
from core.game_release_bundles import active_edge
from core.game_source_cache import SCHEMA_VERSION
from core.game_types import INDEPENDENT
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, CatalogueRelationship, MetadataSnapshot
from models.game_mapping import GameMappingGeneration, GameMappingReference


CHECKS = (
    ('imdb_game_both', 'Games with live and local IMDb records', 'A live IMDb title observation and a title in the active local IMDb dataset.'),
    ('imdb_game_live', 'Games with a live IMDb record only', 'A live IMDb title observation with no match in the active local dataset.'),
    ('imdb_game_local', 'Games with a local IMDb record only', 'A title in the active local IMDb dataset with no confirmed live observation.'),
    ('imdb_game_unverified', 'Games without a verified IMDb record', 'Neither source is confirmed. Unmatched games and failed live requests can still have an IMDb record.'),
)


def coverage_query():
    game = CatalogueEntity
    redirect = aliased(CatalogueEntity)
    kind = game.attributes['game_class'].astext
    alias_target = select(redirect.id).where(redirect.id == game.attributes[BUNDLE_ALIAS]['work_id'].as_integer(),
        redirect.kind == 'game').correlate(game).exists()
    release_bundle = select(CatalogueRelationship.id).join(redirect, redirect.id == CatalogueRelationship.target_id).where(
        CatalogueRelationship.source_id == game.id, *active_edge(CatalogueRelationship), redirect.kind == 'game'
    ).correlate(game).exists()
    # Use the shared catalogue cohort. A private list may still link directly
    # to an edition that redirects for everyone else.
    games = select(game.id, game.name).where(game.kind == 'game',
        kind.in_((INDEPENDENT - {'port'}) | {'expansion'}),
        game.attributes['mobile_only'].astext.is_distinct_from('true'),
        game.attributes['adult'].astext.is_distinct_from('true'),
        ~alias_target, ~and_(kind == 'bundle', release_bundle),
        select(CatalogueIdentity.id).where(CatalogueIdentity.entity_id == game.id,
            CatalogueIdentity.namespace == 'igdb.game').correlate(game).exists()).cte('imdb_games')

    evidence = CatalogueIdentity.evidence
    # The game IMDb binding is unique by work ID. Keep the lookup keyed to
    # that index, so the full cohort cannot multiply the verified bindings.
    binding = verified_bindings().where(CatalogueIdentity.entity_id == games.c.id).add_columns(or_(
        evidence['identity_fetched_at'].astext.is_not(None),
        and_(CatalogueIdentity.source == 'reviewed', evidence['validated_at'].astext.is_not(None))
    ).label('observed_live')).correlate(games).limit(1).lateral('imdb_binding')
    snapshot = MetadataSnapshot
    live_snapshot = select(snapshot.id).where(snapshot.provider == 'imdb', snapshot.fetched_at.is_not(None), or_(
        and_(snapshot.kind == 'game_imdb_identity', snapshot.external_id == f'{SCHEMA_VERSION}:' + binding.c.imdb_id),
        and_(snapshot.kind == COMPONENT, snapshot.entity_id == games.c.id, snapshot.external_id == binding.c.imdb_id),
    )).exists()
    live_credits = select(CatalogueCredit.id).where(CatalogueCredit.work_id == games.c.id,
        CatalogueCredit.provider == 'imdb', CatalogueCredit.scope == 'title',
        CatalogueCredit.attributes['source_active'].astext.is_distinct_from('false'),
        CatalogueCredit.attributes['credited_child'].astext.is_(None)).exists()
    local_title = select(GameMappingReference.native).join(GameMappingGeneration,
        GameMappingGeneration.id == GameMappingReference.generation).where(
        GameMappingGeneration.source == 'imdb', GameMappingGeneration.active.is_(True),
        GameMappingReference.native == binding.c.imdb_id).exists()
    bound = binding.c.imdb_id.is_not(None)
    observations = select(games.c.id, games.c.name, binding.c.imdb_id,
        and_(bound, or_(binding.c.observed_live, live_snapshot, live_credits)).label('live'),
        and_(bound, local_title).label('local')).select_from(games.outerjoin(binding, true())
    ).cte('imdb_observations').prefix_with('MATERIALIZED')
    c = observations.c
    job = aliased(MetadataSnapshot)
    return select(c.id, c.name, case((c.live & c.local, 'imdb_game_both'), (c.live, 'imdb_game_live'),
        (c.local, 'imdb_game_local'), else_='imdb_game_unverified').label('code'),
        case((c.imdb_id.is_(None), 'IMDb title match unverified'),
             (job.error_code.is_not(None), c.imdb_id + ' · Latest live credits refresh failed'),
             else_=c.imdb_id).label('detail')).select_from(observations.outerjoin(job, and_(
                 job.provider == 'imdb', job.kind == JOB, job.entity_id == c.id, job.external_id == cast(c.id, String))))


async def checks(db):
    coverage = coverage_query().subquery()
    c = coverage.c
    ranked = select(c.id, c.name, c.code, c.detail,
        func.row_number().over(partition_by=c.code, order_by=c.id).label('position'),
        func.count().over(partition_by=c.code).label('total')).subquery()
    rows = (await db.execute(select(ranked).where(ranked.c.position <= 8).order_by(ranked.c.code, ranked.c.position))).all()
    counts, examples = {}, {}
    for row in rows:
        counts[row.code] = row.total
        examples.setdefault(row.code, []).append({'id': row.id, 'name': row.name,
            'detail': row.detail, 'href': f'/games/{row.id}'})
    return [{'code': code, 'label': label, 'description': description, 'severity': 'info',
        'unit': 'games', 'action': None, 'count': counts.get(code, 0), 'examples': examples.get(code, [])}
        for code, label, description in CHECKS]
