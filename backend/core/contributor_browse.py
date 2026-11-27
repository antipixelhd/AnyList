"""Rank people by observed person popularity and studios by credited works."""
import asyncio
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import Float, and_, case, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import aliased

from core.game_contributor_works import COMPANIES
from core.game_types import INDEPENDENT
from core.principal_credits import ROLE_GROUPS
from core.studio_names import studio_name
from core.tmdb import _TTLCache
from models import Media
from models.catalogue import CatalogueCredit as Credit, CatalogueEntity as Entity, CatalogueLegacyLink

PAGE_SIZE = 24
PREVIEW_SIZE = 12
WORK_LIMIT = 360
GAME_TYPES = tuple(sorted(INDEPENDENT - {'bundle', 'port'}))
StaffJob = Literal['acting', 'directing', 'writing', 'producing', 'music', 'cinematography', 'editing']
JOBS = {
    'acting': ('actor', 'actress', 'voice actor', 'narrator'),
    'directing': ROLE_GROUPS[1],
    'writing': ROLE_GROUPS[0] + ROLE_GROUPS[2],
    'producing': ROLE_GROUPS[-1] + ('co-producer', 'associate producer', 'line producer'),
    'music': ROLE_GROUPS[3] + ('composers', 'music', 'score', 'original score',
        'music by', 'additional music', 'music supervisor', 'music department', 'music editor',
        'songwriter', 'soundtrack'),
    'cinematography': ROLE_GROUPS[4],
    'editing': ROLE_GROUPS[5],
}
_pages = _TTLCache(maxsize=96)
_query_slots = asyncio.Semaphore(2)


def number(value):
    return case((func.jsonb_typeof(value) == 'number', cast(value.astext, Float)))


def visible_work(work, domain, show_anime, hide_erotic):
    attrs = work.attributes
    public = attrs['adult'].astext.is_distinct_from('true')
    if domain == 'game':
        return and_(public, attrs['mobile_only'].astext.is_distinct_from('true'),
            attrs['game_class'].astext.in_(GAME_TYPES),
            attrs['is_erotic'].astext.is_distinct_from('true') if hide_erotic else True)
    legacy_adult = select(CatalogueLegacyLink.entity_id).join(Media, Media.id == CatalogueLegacyLink.media_id).where(
        CatalogueLegacyLink.entity_id == work.id, Media.adult.is_(True)).correlate(work)
    public = and_(public, ~legacy_adult.exists())
    if show_anime:
        return public
    animation = or_(attrs['genres'].contains([{'id': 16}]),
        attrs['genres'].contains([{'name': 'Animation'}]), attrs['genres'].contains(['Animation']))
    japan = or_(attrs['original_language'].astext == 'ja', attrs['countries'].contains(['JP']),
        attrs['origin_countries'].contains(['JP']), attrs['production_countries'].contains(['JP']))
    return and_(public, ~func.coalesce(and_(animation, japan), False))


def work_ranking(domain, sort, show_anime, hide_erotic):
    attrs = Entity.attributes
    if domain == 'game':
        steam = attrs['game_ratings']['all_time']
        votes = func.coalesce(func.nullif(number(attrs['igdb_rating_count']), 0), number(steam['total']))
        score = func.coalesce(func.nullif(number(attrs['igdb_rating']), 0), number(steam['percentage']))
        minimum = 20
    else:
        votes = number(attrs['vote_count'])
        score = number(attrs['vote_average'])
        minimum = 50
    by_score = sort == 'score'
    order = (score.desc(), votes.desc()) if by_score else (votes.desc(),)
    statement = select(Entity.id.label('work_id'), func.rank().over(order_by=order).label('rank')).where(
        Entity.kind == domain, visible_work(Entity, domain, show_anime, hide_erotic),
        votes >= (minimum if by_score else 1))
    if by_score:
        statement = statement.where(score > 0)
    return statement.order_by(*order, Entity.id).limit(WORK_LIMIT).subquery()


def studio_credits(domain, sort, show_anime, hide_erotic):
    works = work_ranking(domain, sort, show_anime, hide_erotic)
    alias = aliased(Entity)
    root_id = func.coalesce(alias.canonical_company_id, alias.id)
    return select(root_id.label('entity_id'), works.c.work_id, works.c.rank, literal(domain).label('domain')).select_from(works).join(
        Credit, Credit.work_id == works.c.work_id).join(alias, alias.id == Credit.contributor_id).where(
        alias.kind == 'organization',
        Credit.attributes['source_active'].astext.is_distinct_from('false'),
        Credit.role.in_(COMPANIES) if domain == 'game' else Credit.role == 'producer',
    ).distinct()


def birthday_people(day):
    birthday = Entity.attributes['birthday'].astext
    return select(Entity.id).where(Entity.kind == 'person', Entity.canonical_person_id.is_(None),
        birthday.op('~')(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$'), func.right(birthday, 5) == day).cte()


def eligible_staff_credit(domains, job, show_anime, hide_erotic):
    work, alias = aliased(Entity), aliased(Entity)
    members = select(alias.id).where(or_(alias.id == Entity.id, alias.canonical_person_id == Entity.id)).correlate(Entity)
    statement = select(Credit.id).select_from(Credit).join(
        work, work.id == Credit.work_id).where(
        Credit.contributor_id.in_(members),
        Credit.attributes['source_active'].astext.is_distinct_from('false'), Credit.role != 'thanks',
        or_(*(and_(work.kind == domain, visible_work(work, domain, show_anime, hide_erotic))
            for domain in domains))).correlate(Entity)
    if job:
        # A person can qualify for several jobs. Use their credit, including
        # composition credits stored under TMDB's general Sound department.
        role = func.replace(Credit.role, '_', ' ')
        label = func.lower(func.replace(Credit.role_label, '_', ' '))
        statement = statement.where(or_(role.in_(JOBS[job]), label.in_(JOBS[job])))
        if job == 'acting':
            statement = statement.where(~func.coalesce(Credit.character_label, '').op('~*')(
                r'\y(uncredited|archive footage|archive sound)\y'))
    return statement.exists()


def observed_person_popularity(birthdays):
    source = aliased(Entity)
    root_id = func.coalesce(source.canonical_person_id, source.id)
    popularity = number(Credit.attributes['popularity'])
    # TMDB attaches its person-level popularity to cast and crew responses.
    # Use one observation per canonical person, including credits on aliases.
    statement = select(root_id.label('entity_id'), popularity.label('popularity')).select_from(
        Credit).join(source, source.id == Credit.contributor_id).where(
        source.kind == 'person', Credit.provider == 'tmdb', popularity >= 0)
    if birthdays is not None:
        statement = statement.join(birthdays, birthdays.c.id == root_id)
    return statement.distinct(root_id).order_by(root_id, Credit.id.desc()).subquery()


async def browse_contributors(db, *, kind, domains, sort, page, day=None, job=None, preview=False,
        show_anime=True, hide_erotic=True):
    if not domains:
        return {'results': [], 'has_more': False}
    day = (day or datetime.now(timezone.utc).strftime('%m-%d')) if sort == 'birthdays' else None
    key = (db.bind, kind, domains, sort, page, day, job, show_anime, hide_erotic)
    cached = _pages.get(key)
    if cached is not None:
        return preview_page(cached) if preview else cached
    async with _query_slots:
        cached = _pages.get(key)
        if cached is not None:
            return preview_page(cached) if preview else cached
        results = await ranked_page(db, kind, domains, sort, page, day, job, show_anime, hide_erotic)
        _pages.set(key, results, 90)
        return preview_page(results) if preview else results


def preview_page(page):
    results = page['results']
    return {'results': results[:PREVIEW_SIZE], 'has_more': page['has_more'] or len(results) > PREVIEW_SIZE}


def staff_page(domains, day, job, show_anime, hide_erotic):
    birthdays = birthday_people(day) if day else None
    ranking = observed_person_popularity(birthdays)
    profile = select(Entity.id, Entity.name, Entity.image_url, literal(None).label('domain'),
        Entity.attributes['known_for_department'].astext.label('department')).where(
        eligible_staff_credit(domains, job, show_anime, hide_erotic))
    if birthdays is not None:
        profile = profile.join(birthdays, birthdays.c.id == Entity.id).outerjoin(
            ranking, ranking.c.entity_id == Entity.id)
    else:
        profile = profile.join(ranking, ranking.c.entity_id == Entity.id).where(
            ranking.c.popularity > 0, Entity.kind == 'person', Entity.canonical_person_id.is_(None))
    return profile, ranking.c.popularity.desc().nulls_last()


def studio_page(domains, sort, show_anime, hide_erotic):
    credits = [studio_credits(domain, sort, show_anime, hide_erotic) for domain in domains]
    memberships = (union_all(*credits).subquery() if len(credits) > 1 else credits[0].subquery())
    strongest = select(memberships, func.row_number().over(partition_by=memberships.c.entity_id,
        order_by=(memberships.c.rank, memberships.c.work_id)).label('position')).subquery()
    # Reward up to three distinct works. Each title's position is relative to
    # its media type, so different providers' raw vote volumes never compete.
    ranking = select(strongest.c.entity_id, func.sum(1.0 / (10 + strongest.c.rank)).label('weight'),
        func.max(strongest.c.domain).filter(strongest.c.position == 1).label('domain')).where(
        strongest.c.position <= 3).group_by(strongest.c.entity_id).subquery()
    profile = select(Entity.id, Entity.name, Entity.image_url, ranking.c.domain,
        literal(None).label('department')).join(ranking, ranking.c.entity_id == Entity.id).where(
        Entity.kind == 'organization', Entity.canonical_company_id.is_(None))
    return profile, ranking.c.weight.desc()


async def ranked_page(db, kind, domains, sort, page, day, job, show_anime, hide_erotic):
    if kind == 'staff':
        profile, order = staff_page(domains, day, job, show_anime, hide_erotic)
    else:
        profile, order = studio_page(domains, sort, show_anime, hide_erotic)
    rows = (await db.execute(profile.order_by(order,
        func.lower(Entity.name), Entity.id).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1))).all()
    return {'results': [{'key': f'catalogue:{row.id}',
        'name': studio_name(row.name) if kind == 'studio' else row.name,
        'image': row.image_url, 'department': row.department,
        'domain': row.domain or (domains[0] if len(domains) == 1 else None)} for row in rows[:PAGE_SIZE]],
        'has_more': len(rows) > PAGE_SIZE}
