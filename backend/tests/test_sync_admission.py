import asyncio
import unittest

from core.sync_admission import SyncAdmission


class SyncAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_account_does_not_block_another_account_or_take_its_slot(self):
        gate = SyncAdmission(2)
        first, release, same, other = (asyncio.Event() for _ in range(4))

        async def active():
            async with gate.slot(1):
                first.set()
                await release.wait()

        async def waiting(user, entered):
            async with gate.slot(user):
                entered.set()

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(active())
            await first.wait()
            tasks.create_task(waiting(1, same))
            tasks.create_task(waiting(2, other))
            await asyncio.wait_for(other.wait(), 1)
            self.assertFalse(same.is_set())
            release.set()
        self.assertTrue(same.is_set())
        self.assertEqual(gate.states[asyncio.get_running_loop()].accounts, {})

    async def test_capacity_and_cancelled_waiter_release_all_admission_state(self):
        gate = SyncAdmission(1)
        entered = asyncio.Event()

        async def waiting(user):
            async with gate.slot(user):
                entered.set()

        async with gate.slot(1):
            blocked = asyncio.create_task(waiting(2))
            await asyncio.sleep(0)
            self.assertFalse(entered.is_set())
            blocked.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await blocked
            self.assertEqual(set(gate.states[asyncio.get_running_loop()].accounts), {1})
        await asyncio.wait_for(waiting(2), 1)
        self.assertTrue(entered.is_set())
        self.assertEqual(gate.states[asyncio.get_running_loop()].accounts, {})
