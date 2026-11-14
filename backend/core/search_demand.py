"""An active subscriber promotes shared jobs without owning their lifetime.

Generations prevent a late request/cancellation from reviving an old query.
Different tabs/users retain independent demand. No raw query or private results
are stored here, and losing the last subscriber never cancels canonical work.
"""
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy import String, cast

from models.search_demand import SearchDemand
from models.game_runner import GameProviderTurn

current = ContextVar('search_demand', default=None)
LEASE = timedelta(seconds=10)


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def subscriber(viewer, client):
    return hashlib.sha256(f'{viewer}:{client}'.encode()).hexdigest()


async def begin(db, viewer, client, generation, term, domain):
    key = subscriber(viewer, client)
    query = hashlib.sha256(f'{domain}:{term.strip().casefold()}'.encode()).hexdigest()
    stamp = now()
    statement = insert(SearchDemand).values(subscriber=key, owner=f'search-user:{viewer}',
        generation=generation, query=query, targets=[], expires_at=stamp + LEASE)
    await db.execute(statement.on_conflict_do_update(index_elements=['subscriber'], set_={
        'generation':generation, 'query':query, 'expires_at':stamp + LEASE,
        'targets':case((SearchDemand.generation == generation, SearchDemand.targets), else_=cast([], JSONB))},
        where=(SearchDemand.generation < generation) |
              ((SearchDemand.generation == generation) & (SearchDemand.query == query)
               & (SearchDemand.expires_at <= stamp + LEASE / 2))))
    # Keep a short tombstone after cancellation so late old requests cannot
    # re-admit demand. Bounded pruning never touches public verification jobs.
    expired = select(SearchDemand.subscriber).where(SearchDemand.expires_at < stamp - timedelta(days=1)).limit(100)
    await db.execute(delete(SearchDemand).where(SearchDemand.subscriber.in_(expired)))
    await db.commit()
    return key, generation, query


@contextmanager
def context(value):
    token = current.set(value)
    try:
        yield
    finally:
        current.reset(token)


async def release(db, viewer, client, generation):
    statement = insert(SearchDemand).values(subscriber=subscriber(viewer, client), owner=f'search-user:{viewer}',
        generation=generation, query='released', targets=[], expires_at=now())
    await db.execute(statement.on_conflict_do_update(index_elements=['subscriber'],
        set_={'generation':generation, 'expires_at':now(), 'targets':[], 'query':'released'},
        where=SearchDemand.generation <= generation))
    await db.commit()


async def mark(db, targets):
    value = current.get()
    if value is None or not targets:
        return
    key, generation, query = value
    # Append only new scopes under a row update, preserving simultaneous
    # people/studio admissions. Repeated polls do not grow the array.
    targets = list(dict.fromkeys(targets))
    elements = func.jsonb_array_elements_text(SearchDemand.targets.op('||')(cast(targets, JSONB))).table_valued('value')
    combined = select(func.jsonb_agg(func.distinct(elements.c.value))).scalar_subquery()
    await db.execute(update(SearchDemand).where(SearchDemand.subscriber == key,
        SearchDemand.generation == generation, SearchDemand.query == query,
        SearchDemand.expires_at > now(), ~SearchDemand.targets.contains(targets))
        .values(targets=combined))


def active(target):
    return select(SearchDemand.subscriber).where(SearchDemand.expires_at > now(),
        SearchDemand.targets.has_key(target)).exists()


async def subscribers(db, scopes):
    if not scopes:
        return []
    return list(await db.scalars(select(SearchDemand).where(SearchDemand.expires_at > now(),
        SearchDemand.targets.has_any(cast(list(scopes), ARRAY(String))))))


def fair_order(target):
    return select(func.min(func.coalesce(GameProviderTurn.served_at, datetime.min))).select_from(SearchDemand).outerjoin(
        GameProviderTurn, GameProviderTurn.key == 'search-evidence:' + SearchDemand.owner).where(
            SearchDemand.expires_at > now(), SearchDemand.targets.has_key(target)).scalar_subquery()


async def serve(db, scopes):
    rows = await subscribers(db, scopes)
    values = [{'key':'search-evidence:' + owner, 'served_at':now()} for owner in {row.owner for row in rows}]
    if values:
        statement = insert(GameProviderTurn).values(values)
        await db.execute(statement.on_conflict_do_update(index_elements=['key'],
            set_={'served_at':statement.excluded.served_at}))
