"""Bound media-server pulls across accounts without retaining SQL sessions."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary


@dataclass
class Account:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


@dataclass
class State:
    slots: asyncio.Semaphore
    accounts: dict[int, Account] = field(default_factory=dict)


class SyncAdmission:
    def __init__(self, capacity):
        self.capacity = capacity
        self.states = WeakKeyDictionary()

    @asynccontextmanager
    async def slot(self, user_id):
        # Tests and embedded callers may start a fresh loop in the same process.
        for old_loop in list(self.states):
            if old_loop.is_closed():
                del self.states[old_loop]
        loop = asyncio.get_running_loop()
        state = self.states.get(loop)
        if state is None:
            state = self.states[loop] = State(asyncio.Semaphore(self.capacity))
        account = state.accounts.setdefault(user_id, Account())
        account.users += 1
        try:
            # Waiting for one's own account must not consume another account's
            # global slot. Acquisition happens before a worker opens its session.
            async with account.lock, state.slots:
                yield
        finally:
            account.users -= 1
            if not account.users:
                del state.accounts[user_id]
