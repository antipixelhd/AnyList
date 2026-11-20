"""Bounded public revision waits; viewer projections remain request-owned."""
import asyncio
import hashlib
from dataclasses import dataclass, field
from time import monotonic

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.game_imdb_enrichment import now
from models.catalogue import MetadataSnapshot as Snapshot

_waits = {}
MAX_WAITS = 64
MAX_SUBSCRIBERS = 128


class Disconnected(Exception):
    pass


async def revision(db, work_id):
    rows = (await db.execute(select(Snapshot.provider, Snapshot.kind, Snapshot.external_id,
        Snapshot.fetched_at, Snapshot.next_attempt_at, Snapshot.lease_until, Snapshot.error_code,
        Snapshot.coverage['state'].astext, Snapshot.coverage['received'].astext,
        Snapshot.coverage['complete'].astext, Snapshot.coverage['characters_complete'].astext).where(or_(
            and_(Snapshot.provider == 'imdb', Snapshot.kind == 'game_credit_job', Snapshot.external_id == str(work_id)),
            and_(Snapshot.provider == 'igdb', Snapshot.kind == 'game', Snapshot.entity_id == work_id)))
        .order_by(Snapshot.provider, Snapshot.external_id))).all()
    stamp = now()
    waitable = any(not row.error_code and (
        row.lease_until and row.lease_until > stamp or
        row.next_attempt_at and row.next_attempt_at <= stamp) for row in rows)
    return hashlib.sha256(repr([tuple(row) for row in rows]).encode()).hexdigest(), bool(waitable)


@dataclass
class Wait:
    task: asyncio.Task | None = None
    subscribers: int = 0
    wake: asyncio.Event = field(default_factory=asyncio.Event)


def published(engine, work_id):
    for (scope, work, _), wait in list(_waits.items()):
        if scope == id(engine) and work == work_id:
            wait.wake.set()


async def _observe(engine, work_id, known, wait, timeout):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    deadline = monotonic() + timeout
    while True:
        wait.wake.clear()
        async with sessions() as db:
            current, waitable = await revision(db, work_id)
        if current != known or not waitable or monotonic() >= deadline:
            return
        # Local commits notify immediately; compact shared header reads cover
        # other processes and optional IGDB character publication.
        try:
            await asyncio.wait_for(wait.wake.wait(), min(.25, max(.001, deadline - monotonic())))
        except TimeoutError:
            pass


async def wait_for_revision(engine, work_id, known, *, timeout=15, disconnected=None):
    key = (id(engine), work_id, known)
    wait = _waits.get(key)
    if sum(item.subscribers for item in _waits.values()) >= MAX_SUBSCRIBERS:
        return False
    if wait is None:
        if len(_waits) >= MAX_WAITS:
            return False
        wait = Wait()
        _waits[key] = wait
        wait.task = asyncio.create_task(_observe(engine, work_id, known, wait, min(15, timeout)))
    wait.subscribers += 1
    try:
        while not wait.task.done():
            if disconnected and await disconnected():
                raise Disconnected()
            try:
                await asyncio.wait_for(asyncio.shield(wait.task), .25)
            except TimeoutError:
                pass
        await asyncio.shield(wait.task)
        return True
    finally:
        wait.subscribers -= 1
        if not wait.subscribers:
            if _waits.get(key) is wait:
                _waits.pop(key)
            if not wait.task.done():
                wait.task.cancel()
            await asyncio.gather(wait.task, return_exceptions=True)
