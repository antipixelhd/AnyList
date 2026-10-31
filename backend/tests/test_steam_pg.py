"""End-to-end Steam review and sync against an isolated PostgreSQL schema."""
import unittest
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select, text
from alembic.migration import MigrationContext
from alembic.operations import Operations

import test_games_pg as fixture
from core import steam_sync
from models import MediaServerConnection, SteamConnection, SyncJob, SyncStatus
from models.base import CollectionSource
from models.catalogue import CatalogueEntity, CatalogueIdentity
from models.games import GameEntry
from routers import steam


def game(app, minutes, count=None, total=None):
    return {'app_id': app, 'name': 'Game ' + app, 'playtime_minutes': minutes, 'last_played': 0,
            'poster': f'https://cdn.akamai.steamstatic.com/steam/apps/{app}/library_600x900.jpg',
            'release_date': None, 'achievement_count': count, 'achievement_total': total}


@unittest.skipUnless(fixture.URL, 'Requires disposable Games PostgreSQL')
class SteamDatabaseTests(unittest.IsolatedAsyncioTestCase):
    cleanup = fixture.GamesDatabaseTests.cleanup

    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        self.client._transport.app.include_router(steam.router)
        self.source = SimpleNamespace(profile=AsyncMock(return_value={'steam_id': '76561198000000000', 'name': 'Test player'}),
            metadata=AsyncMock(return_value={'release_date': '2022-02-25'}),
            library=AsyncMock(return_value=[game('1245620', 60), game('20', 0), game('30', 0)]),
            achievements=AsyncMock(side_effect=lambda native, app: {'achievement_count': 1 if app == '20' else 0, 'achievement_total': 3}))
        for target, value in (('routers.steam.AsyncSessionLocal', self.Session), ('routers.steam.SteamSource', lambda *a, **k: self.source),
                              ('core.steam_sync.SteamSource', lambda *a, **k: self.source)):
            patcher = patch(target, value); patcher.start(); self.addCleanup(patcher.stop)

    async def connect(self):
        response = await self.client.post('/connections/steam', json={'profile': 'some-player'})
        self.assertEqual(response.status_code, 201, response.text)
        self.id = response.json()['id']
        return self.id

    async def imported(self, overrides=None):
        await self.connect()
        response = await self.client.post(f'/connections/steam/{self.id}/import', json={'generation': 1, 'overrides': overrides or {}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def sync(self):
        async with self.Session() as db:
            job = SyncJob(user_id=self.user.id, connection_id=self.id, source=CollectionSource.steam, job_type='pull', status=SyncStatus.pending)
            db.add(job); await db.commit(); job_id = job.id
        await steam_sync.sync(self.Session, self.user.id, job_id, self.id)
        async with self.Session() as db:
            job = await db.get(SyncJob, job_id)
            self.assertEqual(job.status, SyncStatus.completed, job.error_message)
            return job.stats

    async def test_review_default_or_manual_override_persistence_and_exact_identity(self):
        await self.connect()
        response = await self.client.get(f'/connections/steam/{self.id}')
        self.assertEqual(response.json()['job']['status'], 'completed')
        self.assertEqual(len(response.json()['library']), 3)
        draft = {'generation': 1, 'overrides': {'1245620': False, '30': True}, 'minimum_playtime': 5}
        self.assertEqual((await self.client.put(f'/connections/steam/{self.id}/draft', json=draft)).status_code, 200)
        self.assertEqual((await self.client.get(f'/connections/steam/{self.id}')).json()['draft']['overrides'], draft['overrides'])
        response = await self.client.post(f'/connections/steam/{self.id}/import', json=draft)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'imported': 2, 'selected': 2})
        async with self.Session() as db:
            state = await db.get(SteamConnection, self.id)
            self.assertEqual(state.selection, ['20', '30'])
            self.assertEqual(state.library, [])
            self.assertTrue((await db.get(MediaServerConnection, self.id)).libraries_confirmed)
            identities = list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id == '30')))
            self.assertEqual(len(identities), 1)
        self.assertEqual((await self.client.post(f'/connections/steam/{self.id}/import', json=draft)).status_code, 409)

    async def test_sync_poll_cost_delta_idempotency_manual_fields_and_deleted_entries(self):
        result = await self.imported()
        self.assertEqual(result['selected'], 2)
        self.source.achievements.reset_mock()
        stats = await self.sync()
        self.assertEqual(stats['achievement_requests'], 0)
        self.source.achievements.assert_not_awaited()
        self.source.library.return_value = [game('1245620', 65), game('20', 0), game('30', 400)]
        self.source.achievements.side_effect = None
        self.source.achievements.return_value = {'achievement_count': 2, 'achievement_total': 3}
        async with self.Session() as db:
            entry = await db.scalar(select(GameEntry).where(GameEntry.user_id == self.user.id, GameEntry.work_id == self.base))
            entry.notes = 'keep'; entry.status = 'completed'; entry.total_seconds += 100
            await db.commit()
        stats = await self.sync()
        self.assertEqual(stats['achievement_requests'], 1)
        async with self.Session() as db:
            entry = await db.scalar(select(GameEntry).where(GameEntry.user_id == self.user.id, GameEntry.work_id == self.base))
            self.assertEqual((entry.total_seconds, entry.achievement_count, entry.notes, entry.status), (4000, 2, 'keep', 'completed'))
        self.assertEqual((await self.sync())['updated'], 0)
        async with self.Session() as db:
            await db.execute(delete(GameEntry).where(GameEntry.user_id == self.user.id, GameEntry.work_id == self.base)); await db.commit()
        await self.sync()
        async with self.Session() as db:
            self.assertIsNone(await db.scalar(select(GameEntry).where(GameEntry.user_id == self.user.id, GameEntry.work_id == self.base)))
            self.assertIsNone(await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id == '30')))

    async def test_owner_access_unknown_preservation_and_forged_selection(self):
        await self.connect()
        path = f'/connections/steam/{self.id}'
        forged = await self.client.post(path + '/import', json={'generation': 1, 'overrides': {'999999': True}})
        self.assertEqual(forged.status_code, 422)
        self.viewer = self.other
        self.assertEqual((await self.client.get(path)).status_code, 404)
        self.viewer = self.user
        self.viewer.games_access = False
        self.viewer.is_admin = False
        self.assertEqual((await self.client.get(path)).status_code, 403)

    async def test_exposed_card_metadata_is_bounded_and_deduplicated(self):
        response = await self.client.post('/connections/steam/metadata', json={'apps': ['20', '20', '30']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'20': {'release_date': '2022-02-25'}, '30': {'release_date': '2022-02-25'}})
        self.assertEqual(self.source.metadata.await_count, 2)
        self.assertEqual((await self.client.post('/connections/steam/metadata', json={'apps': ['20'] * 9})).status_code, 422)

    async def test_two_disabled_sync_options_make_no_provider_calls(self):
        await self.imported()
        response = await self.client.patch(f'/connections/steam/{self.id}', json={'sync_playtime': False, 'sync_achievements': False})
        self.assertEqual(response.status_code, 200, response.text)
        self.source.library.reset_mock()
        await self.sync()
        self.source.library.assert_not_awaited()

    async def test_disconnect_cancels_work_and_removes_private_snapshot(self):
        from core.connection_lifecycle import delete_connection
        await self.connect()
        async with self.Session() as db:
            job = SyncJob(user_id=self.user.id, connection_id=self.id, source=CollectionSource.steam,
                          job_type='steam_prepare', status=SyncStatus.pending)
            db.add(job); await db.commit(); job_id = job.id
            await delete_connection(db, await db.get(MediaServerConnection, self.id))
        async with self.Session() as db:
            self.assertIsNone(await db.get(SteamConnection, self.id))
            job = await db.get(SyncJob, job_id)
            self.assertEqual(job.status, SyncStatus.cancelled)
            self.assertIsNone(job.connection_id)

    async def test_real_migration_accepts_steam_on_existing_connections_table(self):
        async with self.engine.connect() as conn:
            await conn.execute(text('DROP TABLE steam_connections'))
            await conn.execute(text('ALTER TABLE media_server_connections DROP CONSTRAINT ck_msc_type'))
            await conn.execute(text("ALTER TABLE media_server_connections ADD CONSTRAINT ck_msc_type CHECK(type IN ('plex','jellyfin','emby','nuvio','stremio','arvio'))"))
            await conn.commit()
            def migrate(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    importlib.import_module('migrations.versions.mt074_steam_connections').upgrade()
            await conn.run_sync(migrate)
            await conn.commit()
        await self.connect()

    async def test_shared_account_queue_accepts_steam_without_tmdb(self):
        from core.account_sync import queue_account_pull
        await self.imported()
        async with self.Session() as db:
            parent = await queue_account_pull(db, self.user.id)
            children = list(await db.scalars(select(SyncJob).where(SyncJob.id.in_(parent.stats['child_job_ids']))))
            self.assertEqual([(row.source, row.connection_id) for row in children], [(CollectionSource.steam, self.id)])

    async def test_unavailable_achievements_preserve_imported_counts_and_private_library_preserves_everything(self):
        from core.steam import SteamError
        await self.imported()
        self.source.library.return_value = [game('1245620', 61), game('20', 1)]
        self.source.achievements.side_effect = None
        self.source.achievements.return_value = {'achievement_count': None, 'achievement_total': None}
        await self.sync()
        async with self.Session() as db:
            entry = await db.scalar(select(GameEntry).join(CatalogueIdentity, CatalogueIdentity.entity_id == GameEntry.work_id)
                .where(GameEntry.user_id == self.user.id, CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id == '20'))
            self.assertEqual(entry.achievement_count, 1)
            self.assertEqual(entry.total_seconds, 60)
        self.source.library.side_effect = SteamError('Game details are private')
        async with self.Session() as db:
            job = SyncJob(user_id=self.user.id, connection_id=self.id, source=CollectionSource.steam, job_type='pull', status=SyncStatus.pending)
            db.add(job); await db.commit(); job_id = job.id
        await steam_sync.sync(self.Session, self.user.id, job_id, self.id)
        async with self.Session() as db:
            self.assertEqual((await db.get(SyncJob, job_id)).status, SyncStatus.failed)
            self.assertEqual(len(list(await db.scalars(select(GameEntry).where(GameEntry.user_id == self.user.id)))), 2)
