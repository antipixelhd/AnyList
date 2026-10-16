"""Shared screen-credit eligibility and bounded title presentation.

Source credits remain complete. Selection is projected at ingestion so search
and detail reads can exclude supporting jobs before loading people or artwork.
"""
import math
import re
from collections import defaultdict
from types import SimpleNamespace

from sqlalchemy import and_, case, func, or_

from core.credit_roles import work_credit

VERSION = 1
CAST_LIMIT = {'movie': 16, 'series': 24}
CREW_LIMIT = 16
# Exact jobs, never substring matches ("assistant director" is not "director").
ROLE_GROUPS = (
    ('creator', 'showrunner', 'head writer'),
    ('director', 'supervising director'),
    ('writer', 'screenplay', 'screenwriter', 'teleplay', 'story', 'author',
     'novel', 'book', 'play', 'original story', 'characters', 'characters created by'),
    ('original music composer', 'composer'),
    ('director of photography', 'cinematographer', 'cinematography'),
    ('editor', 'film editor'),
    ('production design', 'production designer'),
    ('costume design', 'costume designer'),
    ('casting', 'casting director'),
    ('visual effects supervisor', 'animation director'),
    ('producer', 'executive producer'),
)
GROUP_LIMITS = (4, 4, 4, 2, 2, 2, 1, 1, 1, 1, 2)
PRINCIPAL_JOBS = tuple(job for group in ROLE_GROUPS for job in group)
SEARCH_DEPARTMENTS = {'Acting', 'Directing', 'Writing', 'Production', 'Camera',
                      'Sound', 'Editing', 'Art', 'Costume & Make-Up', 'Visual Effects'}


def label(credit):
    return re.sub(r'[_\s-]+', ' ', str(credit.role_label or credit.role or '').strip().casefold())


def group(credit):
    value = label(credit)
    return next((i for i, jobs in enumerate(ROLE_GROUPS) if value in jobs), None)


def positive(value):
    return value if type(value) is int and value > 0 else None


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value > 0 else 0


def evidence(credit):
    return getattr(credit, 'attributes', None) or {}


def eligible(credit, kind):
    if credit.role == 'actor':
        return work_credit('Actor', credit.character_label)
    rank = group(credit)
    return rank is not None and not (kind == 'movie' and label(credit) == 'executive producer')


def select_credits(rows, kind):
    """Select people once per role group, retaining their eligible source credits.

    Billing protects the first eight cast members. Series then favour sustained
    appearances, with featured status, billing and recognition as tie breakers.
    Counts for overlapping jobs/characters are compared, never added together.
    """
    if kind not in CAST_LIMIT:
        return rows
    cast, crew, organizations = defaultdict(list), defaultdict(list), []
    for credit, person in rows:
        if person.kind == 'organization':
            organizations.append((credit, person))
        elif eligible(credit, kind):
            (cast if credit.role == 'actor' else crew)[person.id].append((credit, person))

    def position(entries):
        return min((c.position for c, _ in entries if c.position is not None), default=10**9)

    def appearances(entries):
        return max((positive(evidence(c).get('total_episode_count')) or
                    positive(evidence(c).get('episode_count')) or 0 for c, _ in entries), default=0)

    def recognition(entries):
        return max((number(evidence(c).get('popularity')) for c, _ in entries), default=0)

    def cast_rank(entries):
        billed = position(entries)
        featured = any(evidence(c).get('featured') is True for c, _ in entries)
        if kind == 'movie':
            return (billed, -recognition(entries), str(entries[0][1].id))
        return (0 if billed < 8 else 1, -appearances(entries), -int(featured), billed,
                -recognition(entries), str(entries[0][1].id))

    selected_cast = sorted(cast, key=lambda identity: cast_rank(cast[identity]))[:CAST_LIMIT[kind]]
    selected_crew, counts = [], defaultdict(int)
    def crew_rank(entries):
        ranks = [group(c) for c, _ in entries]
        return (min(ranks), -recognition(entries), -appearances(entries),
                not bool(entries[0][1].image_url), position(entries), str(entries[0][1].id))

    for identity in sorted(crew, key=lambda identity: crew_rank(crew[identity])):
        rank = min(group(c) for c, _ in crew[identity])
        if counts[rank] >= GROUP_LIMITS[rank] or len(selected_crew) >= CREW_LIMIT:
            continue
        selected_crew.append(identity)
        counts[rank] += 1
    return ([row for identity in selected_cast for row in cast[identity]] +
            [row for identity in selected_crew for row in crew[identity]] + organizations)


def project_selection(doc):
    """Persist eligibility beside each source credit; keep every source row."""
    kind = doc['work']['kind']
    rows = []
    for index, raw in enumerate(doc['credits']):
        person = raw['contributor']
        identity = tuple((i['namespace'], i['external_id']) for i in person['identities'])
        rows.append((SimpleNamespace(**{**raw, 'role_label': raw.get('role_label'),
            'character_label': raw.get('character_label'), 'position': raw.get('position'),
            'attributes': raw.get('attributes', {}), 'index': index}),
            SimpleNamespace(id=identity, kind=person['kind'], image_url=person.get('image_url'))))
    selected = {c.index for c, _ in select_credits(rows, kind)}
    for index, credit in enumerate(doc['credits']):
        credit['attributes'] = {**credit.get('attributes', {}),
                                'selection_version': VERSION, 'principal': index in selected}
    return doc


def principal_clause(credit, work_kind):
    """Indexed credit reads, including a conservative pre-migration fallback."""
    job = func.lower(func.coalesce(credit.role_label, func.replace(credit.role, '_', ' ')))
    acting = and_(credit.role == 'actor',
        ~func.coalesce(credit.character_label, '').op('~*')(r'\y(uncredited|archive footage|archive sound)\y'))
    fallback = or_(and_(acting, or_(credit.position.is_(None),
        credit.position < case((work_kind == 'movie', CAST_LIMIT['movie']), else_=CAST_LIMIT['series']))),
        and_(job.in_(PRINCIPAL_JOBS), or_(work_kind != 'movie', job != 'executive producer')))
    return or_(credit.attributes['principal'].as_boolean().is_(True),
        and_(credit.attributes['principal'].as_boolean().is_(None), fallback))


def remote_person_kind(raw):
    department = raw.get('known_for_department')
    if department not in SEARCH_DEPARTMENTS or not raw.get('known_for'):
        return None
    # Primary creative professions qualify directly. Broader departments need
    # known works; no per-search profile/filmography fan-out is performed.
    if department not in ('Acting', 'Directing', 'Writing', 'Production') and number(raw.get('popularity')) < 5:
        return None
    return 'actor' if department == 'Acting' else 'staff'
