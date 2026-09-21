"""Coordinate overlapping provider pulls before any outbound delivery.

Scheduled pulls for one user share a short-lived in-process cycle. Pulls may
commit their independently verified observations, but fan-out is accumulated
until every available pull has finished. The final delivery therefore sees
the reconciled local state and runs once per bounded scheduler cycle.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import AsyncIterator

from models.base import CollectionSource
from models.ratings import RatingChanges, RatingKey


@dataclass
class PullCycleState:
    user_id: int
    new_watched_ids: set[int] = field(default_factory=set)
    new_ratings: RatingChanges = field(default_factory=dict)
    removed_ratings: set[RatingKey] = field(default_factory=set)
    new_collected_ids: set[int] = field(default_factory=set)
    removed_collected_ids: set[int] = field(default_factory=set)
    excluded_connection_ids: set[int] = field(default_factory=set)
    excluded_cloud_sources: set[CollectionSource] = field(default_factory=set)
    library_new_ids: set[int] = field(default_factory=set)
    library_removed_ids: set[int] = field(default_factory=set)
    library_source_ids: set[int] = field(default_factory=set)
    library_api_key: str | None = None


_states: dict[int, PullCycleState] = {}
_locks: dict[int, asyncio.Lock] = {}
_delivery_users: ContextVar[frozenset[int]] = ContextVar(
    "pull_cycle_delivery_users", default=frozenset()
)


def is_active(user_id: int) -> bool:
    return user_id in _states and user_id not in _delivery_users.get()


@contextmanager
def allow_cycle_delivery(state: PullCycleState):
    """Let only the cycle owner deliver while peers still see the barrier."""
    allowed = _delivery_users.get()
    token = _delivery_users.set(allowed | {state.user_id})
    try:
        yield
    finally:
        _delivery_users.reset(token)


@asynccontextmanager
async def coordinated_pull_cycle(user_id: int) -> AsyncIterator[PullCycleState]:
    """Serialize cycles per user while allowing the cycle's pulls to overlap."""
    lock = _locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        state = PullCycleState(user_id=user_id)
        _states[user_id] = state
        try:
            yield state
        finally:
            _states.pop(user_id, None)


def defer_fan_out(
    user_id: int,
    *,
    exclude_connection_id: int | None,
    exclude_cloud_source: CollectionSource | None,
    new_watched_ids: set[int],
    new_ratings: RatingChanges,
    removed_ratings: set[RatingKey],
    new_collected_ids: set[int],
    removed_collected_ids: set[int],
) -> bool:
    state = _states.get(user_id)
    if state is None or not is_active(user_id):
        return False
    state.new_watched_ids.update(new_watched_ids)
    state.new_ratings.update(new_ratings)
    state.removed_ratings.update(removed_ratings)
    state.new_collected_ids.update(new_collected_ids)
    state.removed_collected_ids.update(removed_collected_ids)
    if exclude_connection_id is not None:
        state.excluded_connection_ids.add(exclude_connection_id)
    if exclude_cloud_source is not None:
        state.excluded_cloud_sources.add(exclude_cloud_source)
    return True


def defer_library_fan_out(
    user_id: int,
    *,
    source_connection_id: int,
    new_collected_ids: set[int],
    removed_collected_ids: set[int],
    api_key: str | None,
) -> bool:
    state = _states.get(user_id)
    if state is None or not is_active(user_id):
        return False
    state.library_new_ids.update(new_collected_ids)
    state.library_removed_ids.update(removed_collected_ids)
    state.library_source_ids.add(source_connection_id)
    if state.library_api_key is None:
        state.library_api_key = api_key
    return True
