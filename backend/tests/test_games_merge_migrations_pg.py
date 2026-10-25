"""Upgrade both deployed lineages in newly created disposable databases."""
import asyncio
from datetime import datetime
import os
from pathlib import Path
import sys
import unittest
import uuid

import asyncpg
from sqlalchemy.engine import make_url

URL = os.getenv('GAMES_TEST_DATABASE_URL')
BACKEND = Path(__file__).resolve().parents[1]


@unittest.skipUnless(URL, 'Requires explicit disposable Games PostgreSQL')
class GamesMergeMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not (':55449/catalogue_test' in URL or '/anylist_test' in URL):
            raise RuntimeError('Refusing non-disposable migration DB')
        self.url = make_url(URL).set(drivername='postgresql')
        self.admin = await asyncpg.connect(self.url.render_as_string(hide_password=False))
        self.database = 'anylist_test_merge_' + uuid.uuid4().hex
        await self.admin.execute(f'CREATE DATABASE "{self.database}"')
        self.test_url = self.url.set(database=self.database)
        self.connection = await asyncpg.connect(self.test_url.render_as_string(hide_password=False))

    async def asyncTearDown(self):
        await self.connection.close()
        await self.admin.execute(f'DROP DATABASE "{self.database}" WITH (FORCE)')
        await self.admin.close()

    async def upgrade(self, target):
        environment = {**os.environ, 'SECRET_KEY': 'disposable-migration-test-only',
            'DATABASE_URL': self.test_url.set(drivername='postgresql+asyncpg').render_as_string(hide_password=False),
            'PYTHONUTF8': '1'}
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'alembic', 'upgrade', target,
            cwd=BACKEND, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        output, _ = await process.communicate()
        self.assertEqual(process.returncode, 0, output.decode(errors='replace'))

    async def seed_profiles(self):
        for index, votes in enumerate((0, 777)):
            user_id = await self.connection.fetchval('''INSERT INTO users
                (email,username,api_key,role,is_admin,email_confirmed,totp_enabled)
                VALUES ($1,$2,$3,'user',false,true,false) RETURNING id''',
                f'migration{index}@example.test', f'migration{index}', f'test-{index}')
            await self.connection.execute('''INSERT INTO user_profiles(user_id,minimum_votes)
                VALUES ($1,$2)''', user_id, votes)

    async def assert_head(self):
        self.assertEqual([dict(row) for row in await self.connection.fetch('SELECT version_num FROM alembic_version')],
            [dict(version_num='mt068')])
        for table in ('game_entries', 'game_child_states', 'game_stats_state', 'catalogue_identities'):
            self.assertEqual(await self.connection.fetchval('SELECT to_regclass($1)::text', table), table)
        default = await self.connection.fetchval('''SELECT column_default FROM information_schema.columns
            WHERE table_schema='public' AND table_name='user_profiles' AND column_name='minimum_votes' ''')
        self.assertEqual(default, '250')
        self.assertEqual(await self.connection.fetchval('''SELECT datetime_precision FROM information_schema.columns
            WHERE table_schema='public' AND table_name='users' AND column_name='games_approved_at' '''), 3)

    async def test_fresh_database_has_one_head_and_both_features(self):
        await self.upgrade('head')
        await self.assert_head()
        # A repeated deployment has no pending/repeated schema work.
        await self.upgrade('head')
        await self.assert_head()

    async def test_main_mt058_keeps_its_identity_and_runs_games_branch(self):
        await self.upgrade('mt058')
        self.assertIsNone(await self.connection.fetchval("SELECT to_regclass('game_entries')::text"))
        await self.seed_profiles()
        await self.upgrade('head')
        await self.assert_head()
        # Already-applied main migration is not repeated over later user choices.
        self.assertEqual([dict(row) for row in await self.connection.fetch('SELECT minimum_votes FROM user_profiles ORDER BY id')],
            [dict(minimum_votes=0), dict(minimum_votes=777)])

    async def test_beta_mt066_keeps_games_data_and_applies_main_vote_migration(self):
        await self.upgrade('mt066')
        await self.seed_profiles()
        work_id = await self.connection.fetchval('''INSERT INTO catalogue_entities(kind,name,attributes)
            VALUES ('game','Retained game','{"game_class":"game"}') RETURNING id''')
        user_id = await self.connection.fetchval("SELECT id FROM users WHERE username='migration0'")
        await self.connection.execute('''INSERT INTO game_entries(user_id,work_id,status,total_seconds,notes)
            VALUES ($1,$2,'completed',18000,'Retained note')''', user_id, work_id)
        await self.upgrade('head')
        await self.assert_head()
        self.assertEqual([dict(row) for row in await self.connection.fetch('SELECT minimum_votes FROM user_profiles ORDER BY id')],
            [dict(minimum_votes=250), dict(minimum_votes=777)])
        self.assertEqual(await self.connection.fetchval('SELECT name FROM catalogue_entities WHERE id=$1', work_id), 'Retained game')
        row = await self.connection.fetchrow('SELECT status,total_seconds,notes FROM game_entries WHERE work_id=$1', work_id)
        self.assertEqual(dict(row), dict(status='completed',total_seconds=18000,notes='Retained note'))

    async def test_existing_approval_dates_truncate_without_rounding_or_changing_access(self):
        await self.upgrade('mt067')
        await self.seed_profiles()
        await self.connection.execute('''UPDATE users SET games_access=true,
            games_approved_at='2026-10-25 12:00:00.931995' WHERE username='migration0' ''')
        await self.upgrade('head')
        await self.assert_head()
        rows = await self.connection.fetch('SELECT games_access,games_approved_at FROM users ORDER BY username')
        self.assertEqual([dict(row) for row in rows], [
            dict(games_access=True,games_approved_at=datetime(2026,10,25,12,0,0,931000)),
            dict(games_access=False,games_approved_at=None)])
