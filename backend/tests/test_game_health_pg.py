"""Manual game health controls and all-path Steam admission in disposable PostgreSQL."""
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import delete, func, select

import test_game_identity_reconciliation_pg as fixture
from core import catalogue, game_health, steam_sync, steam_targets
from core.catalogue_providers import ProviderError
from core.data_health_repairs import run_job
from core.statistics_snapshots import utcnow
from db import get_db
from dependencies import get_current_user
from models import GameEntry, SteamConnection, SyncJob, SyncStatus
from models.catalogue import CatalogueEntity, CatalogueIdentity
from routers.admin_data_health import router


def reference(native=113112):
    return {'uid': '1145360', 'game': {'id': native, 'name': 'Hades', 'game_type': 0, 'platforms': [{'id': 6}]}}


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable PostgreSQL')
class GameHealthTests(unittest.IsolatedAsyncioTestCase):
    duplicate = fixture.GameBridgeTests.duplicate
    assert_private_preserved = fixture.GameBridgeTests.assert_private_preserved

    async def asyncSetUp(self):
        await fixture.GameBridgeTests.asyncSetUp(self)
        self.source = SimpleNamespace(steam_games=AsyncMock(return_value=[reference()]))
        self.viewer = self.user
        self.viewer.is_admin = True
        app = FastAPI(); app.include_router(router)
        async def database():
            async with self.Session() as db:
                yield db
        def viewer():
            if self.viewer is None:
                raise HTTPException(401)
            return self.viewer
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = viewer
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await fixture.GameBridgeTests.asyncTearDown(self)

    async def stored_scan(self, scan):
        async with self.Session() as db:
            job = SyncJob(user_id=self.user.id, source='steam', job_type='data_health', status=SyncStatus.completed,
                stats={'action': 'game_scan', 'game_scan': scan, 'results': {}})
            db.add(job); await db.commit(); return job.id

    async def test_scan_is_read_only_and_report_has_local_orphan_links(self):
        async with self.Session() as db:
            duplicate_id = (await self.duplicate(db)).id; await db.commit()
        scan = await game_health.scan_games(self.Session, source=self.source)
        self.assertEqual(scan['candidates'][0]['status'], 'match')
        self.assertEqual(scan['candidates'][0]['duplicate_id'], duplicate_id)
        async with self.Session() as db:
            self.assertIsNotNone(await db.get(CatalogueEntity, duplicate_id))
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity)), 2)
            await self.assert_private_preserved(db)
        with patch('core.game_browse_sources.GameBrowseSources', side_effect=AssertionError('GET must be local')):
            response = await self.client.get('/admin/data-health')
        self.assertEqual(response.status_code, 200, response.text)
        check = next(item for item in response.json()['checks'] if item['code'] == 'orphaned_games')
        self.assertEqual(check['count'], 1)
        self.assertEqual(check['examples'][0]['href'], f'/games/{self.root}')

    async def test_confirmed_admin_job_reconciles_only_the_stored_scan(self):
        async with self.Session() as db:
            duplicate_id = (await self.duplicate(db)).id; await db.commit()
        scan_id = await self.stored_scan(await game_health.scan_games(self.Session, source=self.source))
        with patch('routers.admin_data_health.run_job', new_callable=AsyncMock):
            response = await self.client.post('/admin/data-health/repair', json={
                'action': 'game_reconcile', 'scan_job_id': scan_id, 'confirm': 'reconcile'})
        self.assertEqual(response.status_code, 202, response.text)
        await run_job(response.json()['id'], factory=self.Session, bind=self.engine)
        result = (await self.client.get('/admin/data-health/job')).json()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['game_scan']['candidates'][0]['status'], 'reconciled')
        self.source.steam_games.assert_awaited_once()
        async with self.Session() as db:
            self.assertIsNone(await db.get(CatalogueEntity, duplicate_id))
            await self.assert_private_preserved(db)

    async def test_admin_confirmation_freshness_and_server_owned_proof_are_required(self):
        for admin, status in ((None, 401), (False, 403)):
            self.viewer = None if admin is None else self.user
            if self.viewer: self.viewer.is_admin = False
            self.assertEqual((await self.client.get('/admin/data-health')).status_code, status)
            self.assertEqual((await self.client.post('/admin/data-health/repair', json={'action': 'game_scan'})).status_code, status)
        self.viewer = self.user; self.viewer.is_admin = True
        self.assertEqual((await self.client.post('/admin/data-health/repair', json={'action': 'game_reconcile'})).status_code, 422)
        self.assertEqual((await self.client.post('/admin/data-health/repair', json={
            'action': 'game_reconcile', 'scan_job_id': 999, 'confirm': 'reconcile'})).status_code, 409)
        scan = await game_health.scan_games(self.Session, source=self.source)
        scan['checked_at'] = (utcnow() - timedelta(hours=2)).isoformat()
        scan_id = await self.stored_scan(scan)
        self.assertEqual((await self.client.post('/admin/data-health/repair', json={
            'action': 'game_reconcile', 'scan_job_id': scan_id, 'confirm': 'reconcile'})).status_code, 409)
        self.assertEqual((await self.client.post('/admin/data-health/repair', json={
            'action': 'game_scan', 'candidates': [{'igdb_id': '1'}]})).status_code, 422)

    async def test_private_conflicts_and_changed_identity_are_not_combined(self):
        async with self.Session() as db:
            duplicate_id = (await self.duplicate(db)).id; await db.commit()
        scan = await game_health.scan_games(self.Session, source=self.source)
        async with self.Session() as db:
            db.add(GameEntry(user_id=self.user.id, work_id=duplicate_id, status='completed')); await db.commit()
        result = await game_health.reconcile_scan(self.Session, scan)
        self.assertEqual(result['candidates'][0]['status'], 'conflict')
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)), 2)
            await self.assert_private_preserved(db)
            await db.execute(delete(CatalogueIdentity).where(CatalogueIdentity.namespace == 'igdb.game'))
            await db.commit()
        result = await game_health.reconcile_scan(self.Session, scan)
        self.assertEqual(result['candidates'][0]['status'], 'conflict')
        self.assertIn('changed', result['candidates'][0]['detail'])

    async def test_unknown_ambiguous_failed_and_capped_evidence_is_not_a_match(self):
        for rows, expected in (([], 'unmatched'), ([reference(), reference(99)], 'ambiguous'),
                ([{'uid': '1145360', 'game': None}], 'unknown'), ([reference()] * 500, 'unknown')):
            self.source.steam_games.return_value = rows
            scan = await game_health.scan_games(self.Session, source=self.source)
            self.assertEqual(scan['candidates'][0]['status'], expected)
        self.source.steam_games.side_effect = ProviderError('unavailable')
        scan = await game_health.scan_games(self.Session, source=self.source)
        self.assertEqual(scan['candidates'][0]['status'], 'failed')
        async with self.Session() as db:
            await self.assert_private_preserved(db)

    async def test_scan_paginates_and_includes_tracked_games_without_steam(self):
        async with self.Session() as db:
            work = CatalogueEntity(kind='game', name='No identity', attributes={'game_class': 'game'})
            db.add(work); await db.flush()
            db.add(GameEntry(user_id=self.user.id, work_id=work.id)); await db.commit()
            second_id = work.id
        with patch.object(game_health, 'SCAN_LIMIT', 1):
            first = await game_health.scan_games(self.Session, source=self.source)
            second = await game_health.scan_games(self.Session, source=self.source, after_id=first['next_after_id'])
        self.assertEqual(second['candidates'][0]['id'], second_id)
        self.assertEqual(second['candidates'][0]['status'], 'unknown')
        self.assertIsNone(second['next_after_id'])
        self.source.steam_games.assert_awaited_once()

    async def test_scan_job_runs_only_when_explicitly_enqueued(self):
        from core.data_health import enqueue
        async with self.Session() as db:
            job_id = (await enqueue(db, self.user.id, 'game_scan')).id
        with patch('core.game_browse_sources.GameBrowseSources', return_value=self.source):
            await run_job(job_id, factory=self.Session, bind=self.engine)
        async with self.Session() as db:
            job = await db.get(SyncJob, job_id)
            self.assertEqual(job.status, SyncStatus.completed)
            self.assertEqual(job.stats['game_scan']['candidates'][0]['status'], 'match')
            self.assertEqual(job.total_items, 1)

    async def test_migration_retires_only_legacy_identity_jobs(self):
        import importlib
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from models.catalogue import MetadataSnapshot
        now = utcnow().replace(microsecond=0)
        async with self.Session() as db:
            for kind in ('game_steam_identity', 'game'):
                db.add(MetadataSnapshot(provider='igdb', kind=kind, external_id='1145360',
                    entity_id=self.root, next_attempt_at=now, lease_until=now,
                    lease_token='test', error_code='retry', coverage={'keep': True}))
            await db.commit()
        migration = importlib.import_module('migrations.versions.mt078_manual_game_health')
        def upgrade(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        async with self.engine.begin() as connection:
            await connection.run_sync(upgrade)
        async with self.Session() as db:
            rows = {row.kind: row for row in (await db.scalars(select(MetadataSnapshot))).all()}
            old = rows['game_steam_identity']
            self.assertEqual(old.coverage, {'keep': True, 'state': 'manual_review', 'complete': True})
            self.assertEqual((old.next_attempt_at, old.lease_token, old.lease_until, old.error_code), (None,) * 4)
            self.assertEqual(rows['game'].next_attempt_at, now)
            self.assertEqual(rows['game'].error_code, 'retry')

    async def test_component_import_and_auto_add_cannot_admit_legacy_only_target(self):
        async with self.Session() as db:
            await steam_targets.assign(db, '20', '1145360', evidence={'review': 'component'})
            await db.execute(delete(GameEntry).where(GameEntry.user_id == self.user.id))
            await db.commit()
            state = await db.get(SteamConnection, self.connection_id)
            game = {'app_id': '20', 'name': 'Component', 'playtime_minutes': 60, 'last_played': 0}
            for options in ({'initial': True}, {'add_apps': ['20']}):
                await steam_sync.apply_games(db, state, [game], ['20'], **options); await db.commit()
                self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)), 0)
                self.assertNotIn('20', state.checkpoints)
            db.add(CatalogueIdentity(entity_id=self.root, namespace='igdb.game', external_id='113112', source='igdb'))
            await db.commit()
            await steam_sync.apply_games(db, state, [game], ['20'], initial=True); await db.commit()
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)), 1)
            self.assertEqual(state.checkpoints['20']['work_id'], self.root)
        # A capped response cannot establish a unique main-game target for a
        # combined Steam component either.
        source = SimpleNamespace(steam_games=AsyncMock(return_value=[{
            'uid': '202970', 'game': {'id': 1122, 'name': 'Main game', 'game_type': 0}}] * 500))
        await steam_targets.classify(self.Session, [{'app_id': '212910'}], source=source)
        async with self.Session() as db:
            self.assertIsNone(await catalogue.resolve_identity(db, 'steam.app', '202970'))
            self.assertEqual(await steam_targets.targets(db, ['212910']), {})

    async def test_component_retarget_cannot_move_existing_membership_to_orphan(self):
        from models.games import GameFavorite
        async with self.Session() as db:
            source = CatalogueEntity(kind='game', name='Existing component', attributes={'game_class': 'game'})
            db.add(source); await db.flush()
            db.add(CatalogueIdentity(entity_id=source.id, namespace='steam.app', external_id='20', source='steam'))
            entry = GameEntry(user_id=self.user.id, work_id=source.id, status='playing', notes='Keep component', revision=4)
            db.add(entry)
            db.add(GameFavorite(user_id=self.user.id, work_id=source.id, position=1))
            await steam_targets.assign(db, '20', '1145360', evidence={'review': 'component'})
            state = await db.get(SteamConnection, self.connection_id)
            checkpoints = {'20': {'work_id': source.id, 'playtime_minutes': 30}}
            state.checkpoints = checkpoints
            await db.commit()
            game = {'app_id': '20', 'name': 'Component', 'playtime_minutes': 60, 'last_played': 0}
            for options in ({'initial': True}, {'add_apps': ['20']}, {}):
                await steam_sync.apply_games(db, state, [game], ['20'], **options)
                await db.commit(); await db.refresh(entry)
                self.assertEqual((entry.work_id, entry.notes, entry.revision), (source.id, 'Keep component', 4))
                self.assertEqual(state.checkpoints, checkpoints)
                self.assertIsNotNone(await db.get(GameFavorite, (self.user.id, source.id)))
