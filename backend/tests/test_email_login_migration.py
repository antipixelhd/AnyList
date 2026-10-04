"""Exercise the migration in a private schema of the disposable test DB."""
import importlib
import os
import unittest
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine


@unittest.skipUnless(os.getenv("TRACKING_TEST_DATABASE_URL"), "Requires disposable PostgreSQL database")
class EmailLoginMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TRACKING_TEST_DATABASE_URL"])
        self.schema = "email_login_test_" + uuid.uuid4().hex
        self.conn = await self.engine.connect()
        self.transaction = await self.conn.begin()
        await self.conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        await self.conn.execute(text(f'SET LOCAL search_path TO "{self.schema}"'))
        await self.conn.execute(text("CREATE TABLE users (id integer PRIMARY KEY, email text UNIQUE NOT NULL)"))

    async def asyncTearDown(self):
        await self.transaction.rollback()
        await self.conn.close()
        await self.engine.dispose()

    async def upgrade(self):
        migration = importlib.import_module("migrations.versions.mt032_email_login_identity")

        def run(conn):
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()

        await self.conn.run_sync(run)

    async def test_normalization_preserves_ids_and_enforces_unique_login(self):
        await self.conn.execute(text("INSERT INTO users VALUES (42, ' Owner@Example.COM '), (99, 'other@example.com')"))
        await self.upgrade()
        rows = (await self.conn.execute(text("SELECT id, email FROM users ORDER BY id"))).all()
        self.assertEqual(rows, [(42, "owner@example.com"), (99, "other@example.com")])
        async with self.conn.begin_nested() as savepoint:
            with self.assertRaises(IntegrityError):
                await self.conn.execute(text("INSERT INTO users VALUES (100, ' OWNER@example.com ' )"))
            await savepoint.rollback()

    async def test_collision_audit_stops_before_changing_accounts(self):
        await self.conn.execute(text("INSERT INTO users VALUES (42, 'Owner@example.com'), (99, ' owner@EXAMPLE.COM ' )"))
        with self.assertRaisesRegex(RuntimeError, r"Conflicting user ID groups:.*42.*99"):
            await self.upgrade()
        rows = (await self.conn.execute(text("SELECT id, email FROM users ORDER BY id"))).all()
        self.assertEqual(rows, [(42, "Owner@example.com"), (99, " owner@EXAMPLE.COM ")])
