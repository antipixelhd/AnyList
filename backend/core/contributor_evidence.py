"""Search reads evidence; a leased, bounded worker verifies public works.

Never store provider credentials, personal lists, or import discovered titles.
Person credits are shared across roles/viewers; studio variants include the
provider-side filters and reviewed company membership, not the requesting user.
"""
import asyncio
import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from core import settings_store, tmdb
from core.browse import anime_visible
from core.catalog_search import apply_result_filters, matches_result_filters
from core.credit_roles import work_credit
from core.studio_groups import studio_companies, studio_root
from db import AsyncSessionLocal
from models.catalogue import CatalogueCredit, CatalogueEntity, ContributorEvidence

MAX_PENDING = 256
VERSION = 2
log = logging.getLogger(__name__)
_wake = asyncio.Event()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def evidence_key(kind, native, filters, show_anime):
    config = {'version': VERSION}
    if kind == 'studio':
        native = studio_root(native)
        config.update(filters={**filters, 'excluded_genres':sorted(filters['excluded_genres'])},
            show_anime=show_anime, companies=studio_companies(native))
    variant = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    return (kind, native, variant), config


def eligible_work(raw, kind, filters, show_anime, *, released=False):
    if not raw.get('id') or raw.get('adult') or kind not in ('movie', 'series'):
        return False
    if released and not '0000' < (raw.get('release_date') or raw.get('first_air_date') or '') <= date.today().isoformat():
        return False
    return matches_result_filters(raw, kind, **filters) and anime_visible(raw, show_anime)


def work_summary(payload, section, filters, show_anime):
    if payload is None:
        return None
    rows = payload.get('works', []) if section == 'studio' else (payload.get('cast', []) if section == 'actor' else payload.get('crew', []))
    visible = {}
    for row in rows:
        kind = {'movie': 'movie', 'tv': 'series'}.get(row.get('media_type'))
        if section != 'studio' and not work_credit('Actor' if section == 'actor' else row.get('job'), row.get('character')):
            continue
        if section == 'staff' and str(row.get('job', '')).casefold() in ('actor', 'acting'):
            continue
        if eligible_work(row, kind, filters, show_anime, released=section == 'studio'):
            visible[(kind, row['id'])] = row
    if not visible and not payload.get('complete', True):
        return None
    return {'works': len(visible), 'latest': max((r.get('release_date') or r.get('first_air_date') or '' for r in visible.values()), default='')}


async def cached_evidence(db, jobs, viewer_id, *, enqueue=True):
    """One indexed read and one bounded upsert; no provider work on this path."""
    if not jobs:
        return {}, False
    table = ContributorEvidence
    current = now()
    existing = { (row.kind, row.native_id, row.variant): row for row in await db.scalars(
        select(table).where(tuple_(table.kind, table.native_id, table.variant).in_(jobs))) }
    due = [key for key in jobs if key not in existing or
           ((existing[key].expires_at is None or existing[key].expires_at <= current)
            and (existing[key].next_attempt_at is None or existing[key].next_attempt_at <= current)
            and (existing[key].lease_until is None or existing[key].lease_until <= current))]
    pending = any(row.next_attempt_at is not None and row.next_attempt_at <= current + timedelta(seconds=8)
                  for row in existing.values())
    if enqueue and due:
        # A competing admission or a full queue is temporary. Let the bounded
        # client refresh retry rather than leaving a fresh query empty forever.
        pending = True
        # Serialize admission only, never hold the lock over network requests.
        if await db.scalar(select(func.pg_try_advisory_xact_lock(724052))):
            count = await db.scalar(select(func.count()).select_from(table).where(table.next_attempt_at.is_not(None)))
            due = due[:max(0, MAX_PENDING - count)]
            if due:
                statement = insert(table).values([dict(kind=k[0], native_id=k[1], variant=k[2], config=jobs[k],
                    requested_by=viewer_id, next_attempt_at=current) for k in due])
                await db.execute(statement.on_conflict_do_update(
                    index_elements=['kind', 'native_id', 'variant'],
                    set_={'next_attempt_at':current, 'requested_by':viewer_id},
                    where=(table.expires_at.is_(None) | (table.expires_at <= current)) &
                          (table.lease_until.is_(None) | (table.lease_until <= current)) &
                          (table.next_attempt_at.is_(None) | (table.next_attempt_at <= current))))
                pending = True
            await db.commit()
            _wake.set()
    return {key: row.payload for key, row in existing.items()}, pending


async def local_evidence(db, rows, section, filters, show_anime):
    """Check indexed qualifying-work existence; never transfer whole filmographies.

    Counts/latest dates belong to the precomputed summary and cached provider
    evidence. One eligible credit is sufficient for quick-search eligibility.
    """
    from sqlalchemy import Integer, and_, column, union_all, values
    from sqlalchemy.dialects.postgresql import JSONPATH
    from sqlalchemy import cast

    ids = list(dict.fromkeys(int(r['key'].split(':')[1]) for r in rows
                            if r['key'].startswith('catalogue:')))[:8]
    if not ids:
        return {}
    candidates = values(column('id', Integer), name='candidates').data([(i,) for i in ids])
    person, work = aliased(CatalogueEntity), aliased(CatalogueEntity)
    credit, data = CatalogueCredit, work.attributes
    statement = select(work.id).join_from(person, credit,
        credit.contributor_id == person.id).join(work, work.id == credit.work_id).where(
        or_(person.id == candidates.c.id, person.canonical_person_id == candidates.c.id,
            person.canonical_company_id == candidates.c.id),
        credit.role == 'producer' if section == 'studio' else credit.role == 'actor' if section == 'actor' else credit.role != 'actor',
        ~func.coalesce(data['adult'].astext == 'true', False),
        ~func.coalesce(credit.character_label, '').op('~*')(r'\y(uncredited|archive footage|archive sound)\y'))
    if section == 'staff':
        from core.principal_credits import PRINCIPAL_JOBS
        statement = statement.where(func.lower(func.coalesce(credit.role_label, credit.role)).in_(PRINCIPAL_JOBS))
    if not show_anime:
        def contains(path):
            return func.coalesce(func.jsonb_path_exists(data, cast(path, JSONPATH)), False)
        # Match anime_visible's numeric-genre precedence and case-insensitive
        # names/countries, including provider production-country representations.
        animated = or_(data['genre_ids'].contains([16]), and_(
            or_(data['genre_ids'].astext.is_(None), data['genre_ids'] == []),
            contains('$.genres[*] ? (@ like_regex "^animation$" flag "i" || @.name like_regex "^animation$" flag "i")')))
        japanese = or_(data['original_language'].astext == 'ja',
            contains('$.origin_country[*] ? (@ like_regex "^jp$" flag "i")'),
            contains('$.production_countries[*] ? (@.iso_3166_1 like_regex "^jp$" flag "i")'))
        statement = statement.where(~func.coalesce(and_(animated, japanese), False))
    queries = []
    for kind in ('movie', 'series'):
        query = apply_result_filters(statement.where(work.kind == kind), data, kind, **filters)
        if section == 'studio':
            released = func.coalesce(func.nullif(data['release_date'].astext, ''), data['first_air_date'].astext, '')
            query = query.where(released > '0000', released <= date.today().isoformat())
        queries.append(query.correlate(candidates))
    eligible = union_all(*queries).exists()
    matches = await db.scalars(select(candidates.c.id).where(eligible))
    return {f'catalogue:{identity}': {'works': 1, 'latest': ''} for identity in matches}


async def load_evidence(job, api_key):
    if job['kind'] == 'person':
        profile = await tmdb.get_person(job['native_id'], api_key=api_key)
        if profile.get('id') != job['native_id'] or not isinstance(profile.get('combined_credits'), dict):
            raise ValueError('Incomplete person credits')
        credits = profile['combined_credits']
        if not isinstance(credits.get('cast'), list) or not isinstance(credits.get('crew'), list):
            raise ValueError('Incomplete person credits')
        return {'cast':credits['cast'], 'crew':credits['crew'], 'complete':True}
    config = job['config']
    previous = job.get('payload') or {}
    pages = previous.get('pages', {'movie':1, 'series':1}) if not previous.get('complete') else {'movie':1, 'series':1}
    async def discover(kind):
        page = pages.get(kind)
        if page is None:
            return kind, [], None
        filters = config['filters']
        response = await (tmdb.discover_movies if kind == 'movie' else tmdb.discover_shows)(
            page=page, with_companies=config['companies'], vote_count_min=0,
            minimum_runtime=filters['min_runtime'] if kind == 'movie' else 0,
            excluded_genre_ids=filters['excluded_genres'], released_before=date.today().isoformat(),
            sort_by='primary_release_date.desc' if kind == 'movie' else 'first_air_date.desc', api_key=api_key)
        if not isinstance(response.get('results'), list) or 'total_pages' not in response:
            raise ValueError('Incomplete company works')
        rows = [{**r, 'media_type':'movie' if kind == 'movie' else 'tv'} for r in response['results']]
        visible = [r for r in rows if eligible_work(r, kind, filters, config['show_anime'], released=True)]
        # Positive evidence is enough. Otherwise continue on a later worker tick,
        # never drain an entire company timeline in one job.
        # Search verifies a sample, not the complete studio timeline. A job has
        # a lifetime budget of two pages per media kind, even if no work fits.
        next_page = page + 1 if not visible and page < min(response['total_pages'], 2) else None
        return kind, visible, next_page
    results = await asyncio.gather(discover('movie'), discover('series'))
    works = [] if previous.get('complete') else list(previous.get('works', []))
    works.extend(row for _, rows, _ in results for row in rows)
    return {'works':works, 'pages':{kind:page for kind, _, page in results},
            'scope':'eligibility_sample',
            'complete':all(page is None for _, _, page in results)}


async def run_batch(factory=AsyncSessionLocal):
    """Two jobs, at most four concurrent provider calls; durable cross-worker leases."""
    table, current = ContributorEvidence, now()
    async with factory() as db, db.begin():
        rows = list(await db.scalars(select(table).where(table.next_attempt_at <= current,
            or_(table.lease_until.is_(None), table.lease_until <= current))
            .order_by(table.next_attempt_at).limit(2).with_for_update(skip_locked=True)))
        jobs = []
        for row in rows:
            row.lease_token, row.lease_until = str(uuid.uuid4()), current + timedelta(seconds=45)
            jobs.append({field:getattr(row, field) for field in ('kind', 'native_id', 'variant', 'config', 'payload', 'requested_by', 'lease_token', 'attempts')})
    async def process(job):
        values = {}
        try:
            async with factory() as db:
                key = await settings_store.get_user_tmdb_key(db, job['requested_by'] or -1)
            if not key:
                raise ValueError('Provider unavailable')
            async with asyncio.timeout(20):
                payload = await load_evidence(job, key)
            values = dict(payload=payload, attempts=0,
                expires_at=now() + timedelta(days=7 if any(payload.get(k) for k in ('works', 'cast', 'crew')) else 1),
                next_attempt_at=None if payload['complete'] else now() + timedelta(seconds=2))
        except asyncio.CancelledError:
            raise
        except Exception:
            values = dict(attempts=job['attempts'] + 1,
                next_attempt_at=now() + timedelta(seconds=min(3600, 60 * 2 ** min(job['attempts'], 6))))
            if job['attempts'] >= 4:
                # Broken credentials/providers must not occupy every admission
                # slot forever. A later search can retry after the cooldown.
                values.update(next_attempt_at=None, expires_at=now() + timedelta(hours=6))
        async with factory() as db, db.begin():
            await db.execute(update(table).where(table.kind == job['kind'], table.native_id == job['native_id'],
                table.variant == job['variant'], table.lease_token == job['lease_token'])
                .values(**values, lease_token=None, lease_until=None))
    await asyncio.gather(*(process(job) for job in jobs))
    return len(jobs)


async def scheduler():
    while True:
        _wake.clear()
        try:
            count = await run_batch()
        except Exception:
            log.warning('Contributor verification tick failed')
            count = 0
        try:
            await asyncio.wait_for(_wake.wait(), timeout=.05 if count else 3)
        except TimeoutError:
            pass
