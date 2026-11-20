"""Real transaction contention and commit cutoffs for statistics invalidation."""
import asyncio
import importlib
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

import test_statistics_pg as fixture
from core.statistics_snapshots import metadata_revision


@unittest.skipUnless(fixture.URL, 'Requires disposable PostgreSQL')
class MetadataRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def migrate(self, direction):
        def run(conn):
            with Operations.context(MigrationContext.configure(conn)):
                getattr(importlib.import_module('migrations.versions.mt095_metadata_revision_shards'), direction)()
        async with self.engine.begin() as conn:
            await conn.run_sync(run)

    async def test_single_counter_blocks_unrelated_writes_but_shards_allow_reverse_commits_and_rollback(self):
        async def change(db, identifier):
            await db.execute(text('UPDATE catalogue_entities SET description=:value WHERE id=:id'),
                             {'id': identifier, 'value': 'Transaction fixture'})

        async with self.Session() as observer:
            original = await metadata_revision(observer)
        await self.migrate('downgrade')
        async with self.Session() as observer:
            self.assertEqual(await metadata_revision(observer), original)
            self.assertEqual(await observer.scalar(text('SELECT count(*) FROM stats_metadata_revision')), 1)
        async with self.Session() as first, self.Session() as second:
            await change(first, self.studio_id)
            task = asyncio.create_task(change(second, self.network_id))
            try:
                await asyncio.sleep(.15)
                self.assertFalse(task.done(), 'The original singleton should block disjoint metadata writes')
            finally:
                await first.commit()
                await asyncio.wait_for(task, 3)
                await second.commit()
        await self.migrate('upgrade')

        async with self.Session() as observer:
            initial = await metadata_revision(observer)
            self.assertEqual(initial, original + 2)
            self.assertEqual(await observer.scalar(text('SELECT count(*) FROM stats_metadata_revision')), 64)
        async with self.Session() as first, self.Session() as second:
            first_id = await first.scalar(text('SELECT txid_current()'))
            second_id = await second.scalar(text('SELECT txid_current()'))
            self.assertNotEqual(first_id % 64, second_id % 64)
            await change(first, self.studio_id)
            await asyncio.wait_for(change(second, self.network_id), 3)
            await second.commit()  # Later transaction commits first.
            async with self.Session() as observer:
                self.assertEqual(await metadata_revision(observer), initial + 1)
            await first.commit()
        async with self.Session() as observer:
            self.assertEqual(await metadata_revision(observer), initial + 2)
        async with self.Session() as db:
            await change(db, self.studio_id)
            await db.rollback()
        async with self.Session() as observer:
            self.assertEqual(await metadata_revision(observer), initial + 2)

    async def test_repeatable_read_preserves_cutoff_until_next_transaction(self):
        async with self.Session() as reader, self.Session() as writer:
            await reader.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            initial = await metadata_revision(reader)
            await writer.execute(text('UPDATE media SET title=:title WHERE id=:id'),
                                 {'title': 'Changed during statistics computation', 'id': self.media_id})
            await writer.commit()
            self.assertEqual(await metadata_revision(reader), initial)
            await reader.rollback()
            self.assertEqual(await metadata_revision(reader), initial + 1)
