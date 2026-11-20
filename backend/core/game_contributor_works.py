"""Bounded game works on shared contributor pages, without private editor payloads."""
import json

from sqlalchemy import String, and_, case, cast, func, literal, or_, select, tuple_
from sqlalchemy.orm import aliased

from core.game_types import INDEPENDENT,CREDIT_CHILDREN
from core.game_credit_roles import ACTING
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueRelationship
from models.games import GameChildState, GameEntry

COMPANIES = ('developer', 'publisher', 'supporting', 'porting')
PAGE_SIZE = 24


def coverage_notice(kind, *, person=None, company=None):
    """Describe gaps in the selected portfolio, not unrelated credit roles."""
    if kind == 'studio':
        return None if company and company.get('coverage') == 'complete' else 'Game credits may be incomplete.'
    from core.game_imdb_person import VERSION
    coverage = (person or {}).get('mapping') or {}
    role = (coverage.get('role_coverage') or {}).get(kind) or {}
    if (coverage.get('person_version') == VERSION and coverage.get('source_complete')
            and not coverage.get('omitted_rows')
            and type(role.get('unmapped')) is int and role['unmapped'] == 0):
        return None
    if role.get('unmapped', 0):
        return 'Some game credits could not be matched.'
    return 'Game credits may be incomplete.'


class GameWorksChanged(ValueError):
    """A continuation must restart after its eligible collection changes."""


def verified_game_credits(entity_id, kind):
    contributor = CatalogueEntity
    aliases = select(contributor.id).where(or_(contributor.id == entity_id,
        contributor.canonical_person_id == entity_id, contributor.canonical_company_id == entity_id))
    credited, work, base = (aliased(CatalogueEntity) for _ in range(3))
    # Conflicting parent evidence never selects an arbitrary platform edition.
    parents = select(CatalogueRelationship.source_id.label('child'),
        CatalogueRelationship.relation.label('relation'),
        func.min(CatalogueRelationship.target_id).label('parent')).where(
        CatalogueRelationship.relation.in_(('port', *CREDIT_CHILDREN)),
        CatalogueRelationship.attributes['source_active'].astext.is_distinct_from('false')).group_by(
        CatalogueRelationship.source_id, CatalogueRelationship.relation).having(
        func.count(func.distinct(CatalogueRelationship.target_id)) == 1).subquery()
    ports, expansions = parents.alias(), parents.alias()
    role = CatalogueCredit.role
    role_filter = role.in_(COMPANIES) if kind == 'studio' else role.in_(ACTING) if kind == 'actor' else ~role.in_(ACTING) & (role!='thanks')
    detailed=aliased(CatalogueCredit)
    detailed_role=select(detailed.id).where(detailed.work_id==CatalogueCredit.work_id,
        detailed.contributor_id.in_(aliases),detailed.attributes['source_active'].astext.is_distinct_from('false'),
        detailed.scope!='person',
        or_(and_(role.in_(ACTING),detailed.role.in_(ACTING)),detailed.role==role)).correlate(CatalogueCredit).exists()
    # A sparse person connection remains stored as participation evidence, but
    # a title's actual voice/job roles take display precedence for the same work.
    role_filter=role_filter & or_(CatalogueCredit.scope!='person',~detailed_role)
    person_credit=aliased(CatalogueCredit)
    from sqlalchemy.dialects.postgresql import aggregate_order_by
    person_characters=select(func.string_agg(func.distinct(person_credit.character_label),
        aggregate_order_by(literal(' / '),person_credit.character_label))).where(
        person_credit.work_id==CatalogueCredit.work_id,person_credit.contributor_id.in_(aliases),
        person_credit.scope=='person',person_credit.role.in_(ACTING),
        person_credit.attributes['source_active'].astext.is_distinct_from('false'),
        person_credit.character_label.is_not(None),person_credit.character_label!='').correlate(CatalogueCredit).scalar_subquery()
    independent = INDEPENDENT - {'port'}
    def projected(work, *, child=False, expansion=False):
        expansion_base = select(expansions.c.parent).join(base, base.id == expansions.c.parent).where(
            expansions.c.child == work.id, expansions.c.relation == 'expansion', base.kind == 'game',
            base.attributes['game_class'].astext.in_(independent)).correlate(work)
        return select(work.id.label('id'), work.name.label('title'), work.image_url.label('poster'),
            func.coalesce(work.attributes['backdrop_path'].astext, work.attributes['artwork'][0].astext).label('backdrop'),
            func.coalesce(work.attributes['release_date'].astext, '').label('released'),
            work.attributes['game_class'].astext.label('game_class'),
            expansion_base.scalar_subquery().label('base_work_id'),
            (credited.attributes['game_class'].astext if child else CatalogueCredit.attributes['credited_child']['kind'].astext).label('credit_kind'),
            (credited.name if child else CatalogueCredit.attributes['credited_child']['title'].astext).label('credit_title'),
            role.label('role'), CatalogueCredit.role_label.label('role_label'),
            case((role.in_(ACTING),func.coalesce(func.nullif(CatalogueCredit.character_label,''),person_characters)),
                else_=CatalogueCredit.character_label).label('character_label')).where(
            CatalogueCredit.contributor_id.in_(aliases), role_filter,
            CatalogueCredit.attributes['source_active'].astext.is_distinct_from('false'),
            work.kind == 'game', work.attributes['adult'].astext.is_distinct_from('true'),work.attributes['mobile_only'].astext.is_distinct_from('true'),
            or_(work.attributes['game_class'].astext.in_(independent), and_(
                work.attributes['game_class'].astext == 'expansion', expansion_base.exists(), literal(kind!='actor' or expansion))))
    main = projected(credited,expansion=True).select_from(CatalogueCredit).join(credited, credited.id == CatalogueCredit.work_id)
    if kind=='actor':
        direct=aliased(CatalogueCredit)
        main=main.where(or_(CatalogueCredit.attributes['credited_child']['kind'].astext.is_(None),
            ~select(direct.id).where(direct.work_id==credited.id,direct.contributor_id.in_(aliases),direct.role.in_(ACTING),
                direct.attributes['credited_child']['kind'].astext.is_(None),
                direct.attributes['source_active'].astext.is_distinct_from('false')).correlate(credited).exists()))
        # Keep playable expansions as their own works, while preserving the
        # existing suppression for actors already credited on the base game.
        main=main.where(case((credited.attributes['game_class'].astext=='expansion',
            ~select(direct.id).join(CatalogueRelationship,direct.work_id==CatalogueRelationship.target_id).where(
                CatalogueRelationship.source_id==credited.id,CatalogueRelationship.relation=='expansion',
                CatalogueRelationship.attributes['source_active'].astext.is_distinct_from('false'),
                direct.contributor_id.in_(aliases),direct.role.in_(ACTING),
                direct.attributes['credited_child']['kind'].astext.is_(None),
                direct.attributes['source_active'].astext.is_distinct_from('false')).correlate(credited).exists()),else_=True))
    # Separate branches retain indexed PK joins. A CASE in the root join caused
    # quadratic scans of game metadata even when no port existed (measured).
    port = projected(work).select_from(CatalogueCredit).join(credited,
        credited.id == CatalogueCredit.work_id).join(ports, and_(ports.c.child == credited.id,
        ports.c.relation == 'port')).join(work, work.id == ports.c.parent).where(
        credited.kind == 'game', credited.attributes['game_class'].astext == 'port',
        credited.attributes['mobile_only'].astext.is_distinct_from('true'))
    if kind == 'actor':
        direct=aliased(CatalogueCredit)
        direct_participation=select(direct.id).where(direct.work_id==work.id,
            direct.contributor_id.in_(aliases),direct.role.in_(ACTING),
            direct.attributes['credited_child']['kind'].astext.is_(None),
            direct.attributes['source_active'].astext.is_distinct_from('false')).correlate(work).exists()
        child = projected(work,child=True).select_from(CatalogueCredit).join(credited,
            credited.id==CatalogueCredit.work_id).join(ports,and_(ports.c.child==credited.id,
            ports.c.relation==credited.attributes['game_class'].astext)).join(work,work.id==ports.c.parent).where(
            credited.kind=='game',credited.attributes['game_class'].astext.in_(CREDIT_CHILDREN-{'expansion'}),
            credited.attributes['adult'].astext.is_distinct_from('true'),
            credited.attributes['mobile_only'].astext.is_distinct_from('true'),~direct_participation)
        return main.union_all(port,child).cte('verified_game_contributor_credits')
    return main.union_all(port).cte('verified_game_contributor_credits')



def game_credits(entity_id, kind):
    from core.game_bulk import projection
    verified=verified_game_credits(entity_id,kind)
    return select(verified).union_all(projection(entity_id,kind,verified)).cte('game_contributor_credits')


async def pending_characters(db,work_ids):
    from models.catalogue import MetadataSnapshot
    from core.game_imdb_enrichment import now
    jobs=select(MetadataSnapshot.entity_id).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',
        MetadataSnapshot.entity_id.in_(work_ids),MetadataSnapshot.error_code.is_(None),
        MetadataSnapshot.coverage['characters_complete'].astext.is_distinct_from('true'),
        or_(MetadataSnapshot.next_attempt_at.is_not(None),MetadataSnapshot.lease_until>now()))
    if not work_ids:return set()
    from sqlalchemy import Integer,column
    from sqlalchemy.orm import aliased
    active=aliased(MetadataSnapshot)
    bulk=func.jsonb_to_recordset(MetadataSnapshot.payload['rows']).table_valued(
        column('work_id',Integer)).render_derived(name='pending_character_rows',with_types=True)
    awaiting=select(bulk.c.work_id).select_from(MetadataSnapshot).join(bulk,literal(True)).where(
        MetadataSnapshot.provider=='anylist',MetadataSnapshot.kind=='game_bulk',bulk.c.work_id.in_(work_ids),
        select(active.id).where(active.provider=='imdb',active.kind.in_(('game_person_job','game_person_discovery')),
            active.entity_id==MetadataSnapshot.entity_id,active.error_code.is_(None),
            or_(active.next_attempt_at.is_not(None),active.lease_until>now())).exists())
    # Participation may publish before the final career tick queues detail jobs.
    # Keep that known enrichment handoff pending rather than flashing an empty caption.
    waiting_people=select(active.entity_id).where(active.provider=='imdb',
        active.kind.in_(('game_person_job','game_person_discovery')),active.error_code.is_(None),
        or_(active.next_attempt_at.is_not(None),active.lease_until>now()))
    future=select(CatalogueCredit.work_id).where(CatalogueCredit.provider=='imdb',CatalogueCredit.scope=='person',
        CatalogueCredit.role.in_(ACTING),CatalogueCredit.contributor_id.in_(waiting_people),
        CatalogueCredit.attributes['source_active'].astext.is_distinct_from('false'))
    parents=select(CatalogueRelationship.target_id).where(CatalogueRelationship.source_id.in_(future),
        CatalogueRelationship.relation.in_(('port',*CREDIT_CHILDREN)),
        CatalogueRelationship.attributes['source_active'].astext.is_distinct_from('false'))
    future_ids=future.union(parents).subquery()
    queued=select(future_ids.c.work_id).where(future_ids.c.work_id.in_(work_ids))
    candidates=jobs.union(awaiting,queued).subquery()
    completed=aliased(MetadataSnapshot)
    return set(await db.scalars(select(candidates.c.entity_id).where(~select(completed.id).where(
        completed.provider=='igdb',completed.kind=='game',completed.entity_id==candidates.c.entity_id,
        completed.coverage['characters_complete'].astext=='true').exists())))


async def role_captions(db,entity_id,kind,work_ids):
    """Small public projection for displayed works, never a title hydration fan-out."""
    credits=game_credits(entity_id,kind)
    rows=(await db.execute(select(credits.c.id,credits.c.role,credits.c.role_label,
        credits.c.character_label).where(credits.c.id.in_(work_ids)).distinct())).all()
    result={}
    for identifier,role,label,character in rows:
        item=result.setdefault(identifier,{'id':identifier,'roles':set(),'characters':set()})
        item['roles'].add(label or role.replace('_',' ').title())
        if character and role in ACTING:item['characters'].add(character)
    pending=await pending_characters(db,work_ids) if kind=='actor' else set()
    return [{**item,'roles':sorted(item['roles']),'characters':sorted(item['characters']),
        **({'characters_pending':True} if item['id'] in pending and not item['characters'] else {})} for item in result.values()]


async def works_page(db, viewer, entity_id, kind, *, key, list_scope, status, page, cursor):
    if entity_id is None:
        if cursor:
            raise ValueError('No saved game works')
        return {'works': [], 'known_works': 0, 'list_count': 0 if viewer else None, 'roles': [],
            'total': 0, 'page': page, 'has_more': False, 'next_cursor': None, 'source': 'local'}
    credits = game_credits(entity_id, kind)
    public = select(credits.c.id, credits.c.title, credits.c.poster, credits.c.backdrop,
        credits.c.released, credits.c.game_class, credits.c.base_work_id).distinct().cte('game_contributor_titles')
    if viewer:
        # Exact indexed owner/work joins avoid scanning a whole private union
        # for every public work, and keep retained child facts unlisted.
        entry, child, parent = GameEntry, GameChildState, aliased(GameEntry)
        child_active = and_(public.c.game_class == 'expansion', parent.id.is_not(None),
            child.base_work_id == public.c.base_work_id)
        titles = select(public,
            case((public.c.game_class == 'expansion', case((child_active, child.status))), else_=entry.status).label('list_status'),
            case((public.c.game_class == 'expansion', case((child_active, child.score))), else_=entry.score).label('score')).outerjoin(
            entry, and_(entry.user_id == viewer.id, entry.work_id == public.c.id, public.c.game_class != 'expansion')).outerjoin(
            child, and_(child.user_id == viewer.id, child.work_id == public.c.id, public.c.game_class == 'expansion')).outerjoin(
            parent, and_(parent.id == child.base_entry_id, parent.user_id == child.user_id,
                parent.work_id == child.base_work_id)).cte('game_contributor_state')
    else:
        titles = select(public, literal(None, String).label('list_status'),
            literal(None).label('score')).cte('game_contributor_state')
    labels = select(func.array_agg(func.distinct(func.coalesce(credits.c.role_label,
        func.initcap(func.replace(credits.c.role, '_', ' ')))))).scalar_subquery()
    counts = (await db.execute(select(func.count(), func.count(titles.c.list_status), labels).select_from(titles))).one()
    filters = []
    if list_scope != 'all':
        filters.append(titles.c.list_status.is_not(None) if list_scope == 'in' else titles.c.list_status.is_(None))
    if status:
        filters.append(titles.c.list_status == status)
    eligible = select(titles).where(*filters).cte('game_contributor_eligible')
    # The digest covers ordering/membership only. Notes, scores and editor state
    # never enter public source caches or the continuation token.
    from sqlalchemy.dialects.postgresql import aggregate_order_by
    token = cast(eligible.c.id, String) + ':' + eligible.c.released
    total, generation = (await db.execute(select(func.count(), func.md5(func.coalesce(
        func.string_agg(token, aggregate_order_by(literal('|'), eligible.c.id)), ''))).select_from(eligible))).one()
    scope = f'{kind}:{key}:{viewer.id if viewer else "anonymous"}:{list_scope}:{status}'
    query = select(eligible).order_by(eligible.c.released.desc(), eligible.c.id.desc())
    if cursor:
        try:
            parsed = json.loads(cursor)
            after = parsed['after']
            if parsed.get('v') != 1 or parsed.get('scope') != scope or not isinstance(after, list) or len(after) != 2:
                raise ValueError()
            released, identifier = after
            if not isinstance(released, str) or len(released) > 32 or type(identifier) is not int or identifier <= 0:
                raise ValueError()
            if parsed.get('generation') != generation:
                raise GameWorksChanged()
            query = query.where(tuple_(eligible.c.released, eligible.c.id) < tuple_(released, identifier))
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError('Invalid game works cursor') from None
    elif page > 1:
        query = query.offset((page - 1) * PAGE_SIZE)
    rows = (await db.execute(query.limit(PAGE_SIZE + 1))).mappings().all()
    has_more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    displayed = {row['id']: {**dict(row), 'roles': [], 'characters': []} for row in rows}
    # Only visible expansions need hover eligibility. Keep this private read
    # out of count/paging queries and batch all displayed bases together.
    base_ids={row['base_work_id'] for row in rows if row['game_class']=='expansion' and row['base_work_id']}
    if viewer and base_ids:
        listed_bases=set(await db.scalars(select(GameEntry.work_id).where(
            GameEntry.user_id==viewer.id,GameEntry.work_id.in_(base_ids))))
        for item in displayed.values():
            if item['game_class']=='expansion':item['base_listed']=item['base_work_id'] in listed_bases
    if displayed:
        associations = (await db.execute(select(credits.c.id, credits.c.role, credits.c.role_label,
            credits.c.character_label,credits.c.credit_kind,credits.c.credit_title).where(credits.c.id.in_(displayed)).distinct())).all()
        for identifier, role, label, character,credit_kind,credit_title in associations:
            item = displayed[identifier]
            if credit_kind:
                source={'kind':credit_kind,'title':credit_title}
                if source not in item.setdefault('credit_sources',[]):item['credit_sources'].append(source)
            item['roles'].append(label or role.replace('_', ' ').title())
            if character and role in ACTING:
                item['characters'].append(character)
    pending=await pending_characters(db,set(displayed)) if kind=='actor' else set()
    result = []
    for item in displayed.values():
        if item.get('credit_sources'):item['credit_sources'].sort(key=lambda row:(row['kind'],row['title']))
        released = item.pop('released')
        result.append({**item, 'key': f'game:{item["id"]}', 'type': 'game', 'href': f'/games/{item["id"]}',
            'year': released[:4], 'release_date': released or None,
            'roles': sorted(set(item['roles'])), 'characters': sorted(set(item['characters'])),
            'characters_pending':item['id'] in pending and not item['characters'],
            'entry': None, 'rating_mode': 'manual'})
    next_cursor = json.dumps({'v': 1, 'scope': scope, 'generation': generation,
        'after': [rows[-1]['released'], rows[-1]['id']]}, separators=(',', ':')) if has_more else None
    return {'works': result, 'known_works': counts[0], 'list_count': counts[1] if viewer else None,
        'roles': sorted(set(counts[2] or [])),
        'total': total, 'page': page, 'has_more': has_more, 'next_cursor': next_cursor, 'source': 'local'}
