"""Shared priority, promotion and fair provider admission for game work.

The database arbitrates *starts*, never a future reservation. HTTP retains a
transaction-owned slot until it finishes. Waiting owns no database connection.
"""
import asyncio
import hashlib
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum

from sqlalchemy import case, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert

from models.catalogue import MetadataProviderBudget, MetadataSnapshot
from models.game_runner import GameMapperState, GameProviderTicket, GameProviderTurn, GameWorkDemand


class Priority(IntEnum):
    DIRECT = 0
    FIRST_WORKS = 1
    FINISH = 2
    CONNECTION = 3
    RETRY = 4
    AUTOMATIC = 5


class PriorityYield(asyncio.CancelledError):
    """Resumable work yielded capacity; this is not failed provider evidence."""


@dataclass(frozen=True)
class Work:
    priority: int = Priority.FINISH
    owner: str = 'system'
    scopes: tuple = ()


current = ContextVar('game_work', default=Work())
_dispatchers = {}
_housekeeping=OrderedDict()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def job_key(provider, kind, native):
    return f'job:{provider}:{kind}:{native}'


@contextmanager
def work_context(priority=None, owner=None, scope=None, *, replace=False):
    previous = current.get()
    value = Work(previous.priority if priority is None else int(priority),
        owner or previous.owner, ((scope,) if replace else (*previous.scopes, scope)) if scope else (() if replace else previous.scopes))
    token = current.set(value)
    try:
        yield value
    finally:
        current.reset(token)


async def demand(db, key, *, priority=None, reset=False):
    work = current.get()
    priority = work.priority if priority is None else int(priority)
    if priority==Priority.DIRECT and any(f':{kind}:' in key for kind in (
            'game_person_job','game_person_discovery','game_company_job','game_person_identity')):
        priority=Priority.FINISH
    origin = max(priority, Priority.FINISH)
    stamp = now()
    statement = insert(GameWorkDemand).values(key=key, origin=origin, priority=priority,
        owner=work.owner, active_until=stamp + timedelta(seconds=30))
    await db.execute(statement.on_conflict_do_update(index_elements=['key'], set_={
        'origin':case((GameWorkDemand.active_until<=stamp,origin),else_=func.least(GameWorkDemand.origin,origin)) if reset else func.least(GameWorkDemand.origin, origin),
        'priority':case((GameWorkDemand.active_until<=stamp,priority),else_=func.least(statement.excluded.priority,effective_priority())) if reset else func.least(statement.excluded.priority,
            # Expired foreground demand must not keep a cold task elevated.
            effective_priority(GameWorkDemand)),
        'owner':case((GameWorkDemand.active_until<=stamp,statement.excluded.owner),
            (statement.excluded.priority<effective_priority(),statement.excluded.owner),else_=GameWorkDemand.owner),
        'active_until':statement.excluded.active_until,
    }))
    wake()


def effective_priority(model=GameWorkDemand):
    return case((model.active_until > now(), model.priority), else_=model.origin)


def eligible_jobs(provider=None, kind=None):
    """Normal workers leave automatic-only jobs to the one automatic runner."""
    key = ('job:' + MetadataSnapshot.provider + ':' + MetadataSnapshot.kind + ':' + MetadataSnapshot.external_id)
    return ~select(GameWorkDemand.key).where(GameWorkDemand.key == key,
        effective_priority() == Priority.AUTOMATIC).exists()


def job_order():
    key = ('job:' + MetadataSnapshot.provider + ':' + MetadataSnapshot.kind + ':' + MetadataSnapshot.external_id)
    return func.coalesce(select(effective_priority()).where(GameWorkDemand.key == key).scalar_subquery(), Priority.FINISH)


@asynccontextmanager
async def job_context(sessions, provider, kind, native, *, first=False, retry=False):
    key = job_key(provider, kind, native)
    async with sessions() as db:
        result=(await db.execute(select(GameWorkDemand,MetadataSnapshot.error_code)
            .select_from(MetadataSnapshot).outerjoin(GameWorkDemand,GameWorkDemand.key==key)
            .where(MetadataSnapshot.provider==provider,MetadataSnapshot.kind==kind,
                MetadataSnapshot.external_id==str(native)))).first()
        row=result[0] if result else None
        retry=retry or bool(result and result[1])
    priority = (row.origin if row else Priority.FINISH)
    if retry and priority < Priority.RETRY:
        priority = Priority.RETRY
    if first and not retry and priority < Priority.AUTOMATIC:
        priority = Priority.FIRST_WORKS
    # Persistent demand, including a page opened while the job was running,
    # is resolved again at every provider start.
    with work_context(priority, row.owner if row else f'{provider}:{native}', key, replace=True):
        yield


def wake():
    for dispatcher in list(_dispatchers.values()):
        dispatcher.wake.set()


def _lock_key(value):
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'big', signed=True)


async def prune_ephemeral(db):
    """Scheduler hygiene remains active when automatic ingestion is disabled."""
    for model,column,condition in (
            (GameProviderTurn,GameProviderTurn.key,GameProviderTurn.served_at<now()-timedelta(days=7)),
            (GameWorkDemand,GameWorkDemand.key,(GameWorkDemand.active_until<now()-timedelta(days=1)) & GameWorkDemand.key.startswith('cache:'))):
        old=select(column).where(condition).limit(250)
        await db.execute(delete(model).where(column.in_(old)))


def choose(tickets, demands, turns, stamp):
    """Priority, then user, then entity: opening more pages buys no extra turns."""
    ranked = []
    for ticket in tickets:
        priority, owner = ticket.priority, ticket.owner
        for scope in ticket.scopes:
            row = demands.get(scope)
            if row:
                value = row.priority if row.active_until > stamp else max(row.origin,Priority.RETRY) if ticket.priority==Priority.RETRY else row.origin
                if value < priority:
                    priority, owner = value, row.owner
        entity = next((scope for scope in ticket.scopes if scope.startswith('job:')),ticket.scopes[-1] if ticket.scopes else 'request')
        owner_key = f'{ticket.provider}:{priority}:{owner}'
        entity_key = f'{owner_key}:{entity}'
        rank = (priority, turns.get(owner_key, datetime.min), turns.get(entity_key, datetime.min), ticket.created_at, ticket.id)
        ranked.append((rank, ticket, owner_key, entity_key))
    return min(ranked, key=lambda row:row[0]) if ranked else None


class Dispatcher:
    def __init__(self, sessions, provider):
        self.sessions = sessions
        self.provider = provider
        self.pending = {}
        self.active = 0
        self.wake = asyncio.Event()
        self.task = None

    def retire(self):
        if not self.pending and not self.active and self.task is None:
            for key,value in list(_dispatchers.items()):
                if value is self:_dispatchers.pop(key,None)

    @asynccontextmanager
    async def enter(self, provider):
        work = current.get()
        ticket_id = uuid.uuid4().hex
        stamp = now()
        future = asyncio.get_running_loop().create_future()
        slot = None
        entered=False
        self.pending[ticket_id] = (future,time.monotonic(),work)
        try:
            async with self.sessions() as db:
                key=(id(asyncio.get_running_loop()),id(self.sessions.kw.get('bind')),provider)
                if time.monotonic()>=_housekeeping.get(key,0):
                    await prune_ephemeral(db)
                    _housekeeping[key]=time.monotonic()+30
                    _housekeeping.move_to_end(key)
                    while len(_housekeeping)>32:_housekeeping.popitem(last=False)
                await db.execute(delete(GameProviderTicket).where(GameProviderTicket.expires_at <= stamp))
                count = await db.scalar(select(func.count()).select_from(GameProviderTicket).where(GameProviderTicket.provider==provider))
                if count >= 256:
                    from core.catalogue_providers import ProviderError
                    raise ProviderError('rate_limited', 1)
                db.add(GameProviderTicket(id=ticket_id, provider=provider, priority=work.priority,
                    owner=work.owner, scopes=list(work.scopes), created_at=stamp,
                    expires_at=stamp + timedelta(seconds=5)))
                await db.commit()
            if self.task is None:
                self.task = asyncio.create_task(self.run())
            self.wake.set()
            slot = await asyncio.shield(future)
            self.active+=1
            entered=True
            self.pending.pop(ticket_id,None)
            self.wake.set()
            yield
        finally:
            self.pending.pop(ticket_id, None)
            if slot is None and future.done() and not future.cancelled() and future.exception() is None:
                slot=future.result()
            # This context owns the session from admission through HTTP. Release
            # capacity before any cancellable ticket-cleanup database operation.
            if slot is not None:
                await slot.close()
                if entered:self.active-=1
            if not future.done():
                future.cancel()
            try:
                async with self.sessions() as db:
                    await db.execute(delete(GameProviderTicket).where(GameProviderTicket.id == ticket_id))
                    await db.commit()
            finally:
                self.wake.set()
                self.retire()

    async def arbitrate(self):
        from core.catalogue_providers import INTERVALS, ProviderError
        stamp = now()
        slot = self.sessions()
        admitted = False
        try:
            # One short nonblocking critical section across processes.
            async with self.sessions() as db:
                if not await db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),
                        {'key':_lock_key('game-provider-admission:'+self.provider)}):
                    return .05
                from sqlalchemy import update
                await db.execute(update(GameProviderTicket).where(GameProviderTicket.id.in_(self.pending),
                    GameProviderTicket.expires_at<stamp+timedelta(seconds=3)).values(expires_at=stamp+timedelta(seconds=5)))
                tickets = list(await db.scalars(select(GameProviderTicket).where(GameProviderTicket.provider==self.provider,
                    GameProviderTicket.expires_at > stamp)))
                scopes = {scope for ticket in tickets for scope in ticket.scopes}
                demands = {row.key:row for row in await db.scalars(select(GameWorkDemand).where(GameWorkDemand.key.in_(scopes)))} if scopes else {}
                for ticket in tickets:
                    local=self.pending.get(ticket.id)
                    if local and not local[0].done() and time.monotonic()-local[1]>.5:
                        effective=choose([ticket],demands,{},stamp)[0][0]
                        if effective==Priority.AUTOMATIC:local[0].set_exception(PriorityYield())
                tickets=[ticket for ticket in tickets if ticket.id not in self.pending or not self.pending[ticket.id][0].done()]
                turn_keys = set()
                for ticket in tickets:
                    possible = [(ticket.priority, ticket.owner)] + [(row.priority if row.active_until > stamp else max(row.origin,Priority.RETRY) if ticket.priority==Priority.RETRY else row.origin, row.owner)
                        for scope in ticket.scopes if (row := demands.get(scope))]
                    entity = next((scope for scope in ticket.scopes if scope.startswith('job:')),ticket.scopes[-1] if ticket.scopes else 'request')
                    for priority, owner in possible:
                        key = f'{ticket.provider}:{priority}:{owner}'
                        turn_keys.update((key, f'{key}:{entity}'))
                turns = dict((await db.execute(select(GameProviderTurn.key, GameProviderTurn.served_at)
                    .where(GameProviderTurn.key.in_(turn_keys)))).all()) if turn_keys else {}
                selected = choose(tickets, demands, turns, stamp)
                if not selected:
                    return .2
                rank, ticket, owner_key, entity_key = selected
                local = self.pending.get(ticket.id)
                if local is None:
                    return .05  # The owning process admits the selected ticket.
                future, started, work = local
                if future.done():
                    return 0
                if rank[0] == Priority.AUTOMATIC:
                    from core.config import settings
                    state = await db.get(GameMapperState, 1)
                    if not settings.game_background_mapper_enabled or state and not state.enabled:
                        future.set_exception(PriorityYield())
                        return 0
                budget = await db.get(MetadataProviderBudget, ticket.provider)
                delay = max(0, (budget.next_request_at - stamp).total_seconds()) if budget and budget.next_request_at else 0
                if delay > 5:
                    future.set_exception(ProviderError('rate_limited', delay))
                    return 0
                if delay:
                    return min(.15, delay)
                capacity = 2 if ticket.provider == 'igdb' else 1
                if rank[0] >= Priority.FIRST_WORKS and not await slot.scalar(
                        text('SELECT pg_try_advisory_xact_lock(:key)'),
                        {'key':_lock_key(f'catalogue-{ticket.provider}-lower-priority')}):
                    return .05
                for index in range(capacity):
                    if await slot.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),
                            {'key':_lock_key(f'catalogue-{ticket.provider}-slot:{index}')}):
                        break
                else:
                    return .05
                # Same rate lock as the pre-existing provider budget layer.
                if not await db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),
                        {'key':_lock_key('catalogue-provider:' + ticket.provider)}):
                    return .05
                await db.execute(insert(MetadataProviderBudget).values(provider=ticket.provider).on_conflict_do_nothing())
                budget = await db.get(MetadataProviderBudget, ticket.provider, populate_existing=True,with_for_update=True)
                if budget.next_request_at and budget.next_request_at > now():
                    return .05
                month=stamp.strftime('%Y-%m')
                if budget.month!=month:budget.month,budget.request_count=month,0
                from core.config import settings
                if ticket.provider=='rawg' and budget.request_count>=settings.rawg_monthly_budget:
                    future.set_exception(ProviderError('monthly_budget'));return 0
                if ticket.provider in {'steam','hardcover'}:
                    day=stamp.strftime('%Y-%m-%d')
                    if budget.day!=day:budget.day,budget.daily_request_count=day,0
                    limit=settings.steam_daily_budget if ticket.provider=='steam' else settings.hardcover_daily_budget
                    if budget.daily_request_count>=limit:
                        tomorrow=stamp.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
                        future.set_exception(ProviderError('daily_budget',(tomorrow-stamp).total_seconds()));return 0
                    budget.daily_request_count+=1
                budget.request_count += 1
                interval=1.1 if ticket.provider=='openlibrary' and not settings.openlibrary_contact_email else INTERVALS[ticket.provider]
                budget.next_request_at = max(budget.next_request_at or stamp,now()+timedelta(seconds=interval))
                for key in (owner_key, entity_key):
                    statement = insert(GameProviderTurn).values(key=key, served_at=stamp)
                    await db.execute(statement.on_conflict_do_update(index_elements=['key'], set_={'served_at':stamp}))
                await db.execute(delete(GameProviderTicket).where(GameProviderTicket.id == ticket.id))
                # Commit admission, retaining only slot's transaction through HTTP.
                await db.commit()
                admitted = True
                future.set_result(slot)
                return 0
        finally:
            if not admitted:
                await slot.close()

    async def run(self):
        try:
            while self.pending:
                self.wake.clear()
                # An automatic waiter never holds a job lease through a long
                # foreground burst. Its caller checkpoints without error/backoff.
                if all(future.done() for future, _, _ in self.pending.values()):
                    await asyncio.sleep(.01)
                    continue
                delay = await self.arbitrate()
                if delay:
                    try:
                        await asyncio.wait_for(self.wake.wait(), delay)
                    except TimeoutError:
                        pass
        except BaseException as error:
            for future, _, _ in self.pending.values():
                if not future.done():
                    future.set_exception(error)
        finally:
            self.task = None
            self.retire()


@asynccontextmanager
async def provider_slot(sessions, provider):
    # Factories over the same engine share one local pump; workers in other
    # processes share the durable tickets, turns, budgets and advisory slots.
    engine = sessions.kw.get('bind')
    key = (id(asyncio.get_running_loop()), id(engine),provider)
    dispatcher = _dispatchers.get(key)
    if dispatcher is None:
        dispatcher = _dispatchers[key] = Dispatcher(sessions,provider)
    async with dispatcher.enter(provider):
        yield


class GameWorkMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        credential = headers.get(b'authorization') or headers.get(b'x-api-key') or str(scope.get('client', ('anonymous',))[0]).encode()
        owner = 'viewer:' + hashlib.sha256(credential).hexdigest()[:32]
        with work_context(Priority.DIRECT, owner, replace=True):
            await self.app(scope, receive, send)
