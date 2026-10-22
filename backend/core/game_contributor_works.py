"""Bounded game works on shared contributor pages, without private editor payloads."""
import json

from sqlalchemy import String, and_, case, cast, func, literal, or_, select, tuple_
from sqlalchemy.orm import aliased

from core.game_types import INDEPENDENT
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueRelationship
from models.games import GameChildState, GameEntry

ACTING = ('actor', 'voice_actor', 'narrator')
COMPANIES = ('developer', 'publisher', 'supporting', 'porting')
PAGE_SIZE = 24


class GameWorksChanged(ValueError):
    """A continuation must restart after its eligible collection changes."""


def game_credits(entity_id, kind):
    contributor = CatalogueEntity
    aliases = select(contributor.id).where(or_(contributor.id == entity_id,
        contributor.canonical_person_id == entity_id, contributor.canonical_company_id == entity_id))
    credited, work, base = (aliased(CatalogueEntity) for _ in range(3))
    # Conflicting parent evidence never selects an arbitrary platform edition.
    parents = select(CatalogueRelationship.source_id.label('child'),
        CatalogueRelationship.relation.label('relation'),
        func.min(CatalogueRelationship.target_id).label('parent')).where(
        CatalogueRelationship.relation.in_(('port', 'expansion')),
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
    independent = INDEPENDENT - {'port'}
    def projected(work):
        expansion_base = select(expansions.c.parent).join(base, base.id == expansions.c.parent).where(
            expansions.c.child == work.id, expansions.c.relation == 'expansion', base.kind == 'game',
            base.attributes['game_class'].astext.in_(independent)).correlate(work)
        return select(work.id.label('id'), work.name.label('title'), work.image_url.label('poster'),
            func.coalesce(work.attributes['backdrop_path'].astext, work.attributes['artwork'][0].astext).label('backdrop'),
            func.coalesce(work.attributes['release_date'].astext, '').label('released'),
            work.attributes['game_class'].astext.label('game_class'),
            expansion_base.scalar_subquery().label('base_work_id'),
            role.label('role'), CatalogueCredit.role_label.label('role_label'),
            CatalogueCredit.character_label.label('character_label')).where(
            CatalogueCredit.contributor_id.in_(aliases), role_filter,
            CatalogueCredit.attributes['source_active'].astext.is_distinct_from('false'),
            work.kind == 'game', work.attributes['adult'].astext.is_distinct_from('true'),
            or_(work.attributes['game_class'].astext.in_(independent), and_(
                work.attributes['game_class'].astext == 'expansion', expansion_base.exists())))
    main = projected(credited).select_from(CatalogueCredit).join(credited, credited.id == CatalogueCredit.work_id)
    # Separate branches retain indexed PK joins. A CASE in the root join caused
    # quadratic scans of game metadata even when no port existed (measured).
    port = projected(work).select_from(CatalogueCredit).join(credited,
        credited.id == CatalogueCredit.work_id).join(ports, and_(ports.c.child == credited.id,
        ports.c.relation == 'port')).join(work, work.id == ports.c.parent).where(
        credited.kind == 'game', credited.attributes['game_class'].astext == 'port')
    return main.union_all(port).cte('game_contributor_credits')



async def works_page(db, viewer, entity_id, kind, *, key, list_scope, status, page, cursor):
    if entity_id is None:
        if cursor:
            raise ValueError('No saved game works')
        return {'works': [], 'known_works': 0, 'list_count': 0 if viewer else None, 'roles': [],
            'total': 0, 'page': page, 'has_more': False, 'next_cursor': None, 'source': 'local',
            'notice': 'No verified saved game credits are available. Coverage may be incomplete.'}
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
    if displayed:
        associations = (await db.execute(select(credits.c.id, credits.c.role, credits.c.role_label,
            credits.c.character_label).where(credits.c.id.in_(displayed)).distinct())).all()
        for identifier, role, label, character in associations:
            item = displayed[identifier]
            item['roles'].append(label or role.replace('_', ' ').title())
            if character and role in ACTING:
                item['characters'].append(character)
    result = []
    for item in displayed.values():
        released = item.pop('released')
        result.append({**item, 'key': f'game:{item["id"]}', 'type': 'game', 'href': f'/games/{item["id"]}',
            'year': released[:4], 'release_date': released or None,
            'roles': sorted(set(item['roles'])), 'characters': sorted(set(item['characters'])),
            'entry': None, 'rating_mode': 'manual'})
    next_cursor = json.dumps({'v': 1, 'scope': scope, 'generation': generation,
        'after': [rows[-1]['released'], rows[-1]['id']]}, separators=(',', ':')) if has_more else None
    return {'works': result, 'known_works': counts[0], 'list_count': counts[1] if viewer else None,
        'roles': sorted(set(counts[2] or [])),
        'total': total, 'page': page, 'has_more': has_more, 'next_cursor': next_cursor, 'source': 'local',
        'notice': 'Showing verified saved game credits. Coverage may be incomplete.'}
