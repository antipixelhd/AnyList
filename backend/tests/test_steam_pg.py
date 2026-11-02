"""End-to-end Steam review and sync against an isolated PostgreSQL schema."""
import unittest
import importlib
import asyncio
from datetime import datetime, timedelta, timezone
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
from models.steam import SteamPlayerGame
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
        await self.progress()
        response = await self.client.post(f'/connections/steam/{self.id}/import', json={'generation': 1, 'overrides': overrides or {}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def progress(self, apps=None):
        response = await self.client.post(f'/connections/steam/{self.id}/achievements',
            json={'generation': 1, 'apps': apps or ['1245620', '20', '30']})
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
        self.source.achievements.assert_not_awaited()
        await self.progress()
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
            await conn.execute(text('DROP TABLE steam_player_games'))
            await conn.execute(text('ALTER TABLE media_server_connections DROP CONSTRAINT ck_msc_type'))
            await conn.execute(text("ALTER TABLE media_server_connections ADD CONSTRAINT ck_msc_type CHECK(type IN ('plex','jellyfin','emby','nuvio','stremio','arvio'))"))
            await conn.commit()
            def migrate(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    importlib.import_module('migrations.versions.mt074_steam_connections').upgrade()
                    importlib.import_module('migrations.versions.mt075_steam_progress').upgrade()
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

    async def test_picker_is_ready_without_achievement_calls_and_one_failure_is_not_fatal(self):
        from core.steam import SteamError
        await self.connect()
        self.source.achievements.assert_not_awaited()
        review = (await self.client.get(f'/connections/steam/{self.id}')).json()
        self.assertEqual(review['job']['status'], 'completed')
        self.assertTrue(all(row['achievement_state'] == 'loading' for row in review['library']))
        def progress(native, app):
            if app == '20':
                raise SteamError('Steam returned HTTP 500')
            return {'achievement_count': 1, 'achievement_total': 3}
        self.source.achievements.side_effect = progress
        result = await self.progress()
        self.assertEqual(result['provider_requests'], 3)
        rows = {row['app_id']: row for row in result['games']}
        self.assertEqual(rows['20']['achievement_state'], 'unavailable')
        self.assertEqual(rows['30']['achievement_count'], 1)
        self.assertEqual((await self.progress())['provider_requests'], 0)
        self.assertEqual((await self.client.get(f'/connections/steam/{self.id}')).json()['job']['status'], 'completed')

    async def test_cached_progress_survives_reconnect_and_public_evidence_includes_declined_games(self):
        from core.connection_lifecycle import delete_connection
        from models.catalogue import MetadataSnapshot
        from core.steam_cache import PUBLIC_KIND
        await self.connect(); await self.progress()
        async with self.Session() as db:
            await delete_connection(db, await db.get(MediaServerConnection, self.id))
        await self.connect()
        self.assertEqual((await self.progress())['provider_requests'], 0)
        async with self.Session() as db:
            rows = list(await db.scalars(select(MetadataSnapshot).where(MetadataSnapshot.kind == PUBLIC_KIND)))
            self.assertEqual({row.external_id for row in rows}, {'1245620', '20', '30'})
            self.assertTrue(all('playtime_minutes' not in row.payload and 'achievement_count' not in row.payload for row in rows))

    async def test_overlapping_progress_requests_share_the_durable_claim(self):
        await self.connect()
        started, release = asyncio.Event(), asyncio.Event()
        async def slow(native, app):
            started.set(); await release.wait()
            return {'achievement_count': 1, 'achievement_total': 3}
        self.source.achievements.side_effect = slow
        first = asyncio.create_task(self.progress(['20']))
        await asyncio.wait_for(started.wait(), 5)
        second = await self.progress(['20'])
        self.assertEqual(second['provider_requests'], 0)
        release.set(); await first
        self.assertEqual(self.source.achievements.await_count, 1)

    async def test_explicit_import_selection_is_not_expanded_by_late_achievement_results(self):
        await self.connect(); await self.progress()
        response = await self.client.post(f'/connections/steam/{self.id}/import', json={'generation': 1, 'selected_apps': ['1245620']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['selected'], 1)

    async def test_new_game_planning_first_play_threshold_and_manual_status_are_preserved(self):
        from zoneinfo import ZoneInfo
        await self.imported()
        response = await self.client.patch(f'/connections/steam/{self.id}', json={
            'sync_playtime': True, 'sync_achievements': False, 'auto_add_games': True, 'timezone': 'Europe/Berlin'})
        self.assertEqual(response.status_code, 200, response.text)
        self.source.library.return_value = [game('1245620', 60), game('20', 0), game('30', 500), game('40', 0)]
        stats = await self.sync()
        self.assertEqual(stats['added'], 1)
        async def entry(app):
            async with self.Session() as db:
                return await db.scalar(select(GameEntry).join(CatalogueIdentity, CatalogueIdentity.entity_id == GameEntry.work_id)
                    .where(GameEntry.user_id == self.user.id, CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id == app))
        self.assertIsNone(await entry('30'))  # Declined original games are never backfilled.
        self.assertEqual((await entry('40')).status, 'planning')
        self.source.library.return_value[-1] = game('40', 30)
        await self.sync(); self.assertEqual((await entry('40')).status, 'planning')
        self.source.library.return_value[-1] = game('40', 31)
        await self.sync(); row = await entry('40')
        self.assertEqual((row.status, row.start_date), ('playing', datetime.now(ZoneInfo('Europe/Berlin')).date()))
        async with self.Session() as db:
            changed = await db.get(GameEntry, row.id); changed.status = 'paused'; await db.commit()
        self.source.library.return_value[-1] = game('40', 60)
        await self.sync(); self.assertEqual((await entry('40')).status, 'paused')
        async with self.Session() as db:
            await db.execute(delete(GameEntry).where(GameEntry.id == row.id)); await db.commit()
        await self.sync(); self.assertIsNone(await entry('40'))

    async def test_timestamp_change_triggers_refresh_but_missing_timestamp_does_not(self):
        await self.imported()
        self.source.achievements.reset_mock()
        self.source.library.return_value[0]['last_played'] = 1800000000
        self.assertEqual((await self.sync())['achievement_requests'], 1)
        self.source.library.return_value[0]['last_played'] = None
        self.assertEqual((await self.sync())['achievement_requests'], 0)
        self.source.library.return_value[0]['playtime_minutes'] = 61
        self.assertEqual((await self.sync())['achievement_requests'], 1)

    async def test_changed_public_totals_update_percentages_and_expired_counts_refresh_without_playtime(self):
        from core import steam_cache, game_tracking
        await self.imported()
        async with self.Session() as db:
            work = await db.get(CatalogueEntity, self.base)
            work.field_sources = {**(work.field_sources or {}), 'attributes.achievement_catalogue': 'steam'}
            await db.commit()
        await steam_cache.publish_totals(self.Session, {'1245620': 6})
        self.source.achievements.side_effect = None
        self.source.achievements.return_value = {'achievement_count': 2, 'achievement_total': 6}
        self.assertEqual((await self.sync())['achievement_requests'], 1)
        async with self.Session() as db:
            state = (await game_tracking.states(db, self.user.id, [self.base]))[0]
            self.assertAlmostEqual(state.achievements.percentage, 100 / 3)
            cache = await db.get(SteamPlayerGame, (self.user.id, '76561198000000000', '1245620'))
            cache.fetched_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
            await db.commit()
        self.source.achievements.return_value = {'achievement_count': 1, 'achievement_total': 2}
        self.assertEqual((await self.sync())['achievement_requests'], 1)
        async with self.Session() as db:
            state = (await game_tracking.states(db, self.user.id, [self.base]))[0]
            self.assertEqual((state.achievements.count, state.achievements.total, state.achievements.percentage), (1, 2, 50))
