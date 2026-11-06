"""Portable private game data and safe, idempotent restore in disposable PG."""
import io
import json
import unittest
import zipfile
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, func
import test_games_pg as fixture
from core.game_backup import export_games, restore_games, Backup
from core.data_export import build_export_zip
from core.scrob_import import parse_scrob_export
from models import UserProfileData
from models.catalogue import CatalogueIdentity
from models.games import GameEntry, GameChildState, GameFavorite, GameAchievementUnlock, GameActivity
from schemas_games import GamePatch, ChildPatch
from core import game_tracking


@unittest.skipUnless(fixture.URL, 'Requires disposable Games PostgreSQL')
class GameBackupTests(unittest.IsolatedAsyncioTestCase):
    cleanup = fixture.GamesDatabaseTests.cleanup

    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            for identifier in (self.base, self.child, self.dlc):
                db.add(CatalogueIdentity(entity_id=identifier, namespace='igdb.game', external_id=str(100+identifier), source='igdb'))
            await db.commit()
            await game_tracking.save_entry(db, self.user.id, self.base, GamePatch(status='completed', score=8.5,
                notes='private backup note', total_seconds=43210, completion_seconds=36000, replay_count=2,
                finish_date=None, achievement_mode='targeted', expected_achievement_total=3, unlocked_ids=self.achievement_ids[:1]))
            await game_tracking.save_child(db, self.user.id, self.child, ChildPatch(status='completed', score=9), listed=True)
            await game_tracking.save_child(db, self.user.id, self.dlc, ChildPatch(status='paused', score=7), listed=False)
            await game_tracking.set_favorite(db, self.user.id, self.base, True)
            profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.user.id))
            profile.game_banners = {str(self.base): 'https://example.test/banner.jpg'}
            await db.commit()

    async def test_zip_roundtrip_restores_private_facts_without_source_primary_keys(self):
        async with self.Session() as db:
            payload = await build_export_zip(db, self.user, None)
            parsed = parse_scrob_export(payload)
            self.assertIsNotNone(parsed.games)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                document = json.loads(archive.read('games.json'))
                self.assertNotIn('user_id', json.dumps(document))
                self.assertNotIn('catalogue_id', json.dumps(document))
                self.assertIn('private backup note', json.dumps(document))
            result = await restore_games(db, self.other.id, parsed.games, cancelled=AsyncMock())
            self.assertEqual(result, {'games':3, 'errors':0})
            row = await db.scalar(select(GameEntry).where(GameEntry.user_id == self.other.id))
            self.assertEqual((row.notes, row.score, row.total_seconds, row.completion_seconds, row.replay_count),
                ('private backup note', 8.5, 43210, 36000, 2))
            self.assertIsNone(row.finish_date)
            self.assertEqual(row.achievement_mode, 'targeted')
            children = list(await db.scalars(select(GameChildState).where(GameChildState.user_id == self.other.id)))
            self.assertEqual(len(children), 2)
            self.assertEqual(next(r for r in children if r.work_id == self.child).base_entry_id, row.id)
            self.assertIsNone(next(r for r in children if r.work_id == self.dlc).base_entry_id)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameAchievementUnlock).where(GameAchievementUnlock.user_id == self.other.id)), 1)
            profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.other.id))
            self.assertEqual(profile.game_banners, {str(self.base): 'https://example.test/banner.jpg'})
            # Re-import neither duplicates activities/favorites nor overwrites current notes.
            row.notes = 'newer local decision'; await db.commit()
            before = await db.scalar(select(func.count()).select_from(GameActivity).where(GameActivity.user_id == self.other.id))
            self.assertEqual((await restore_games(db, self.other.id, parsed.games, cancelled=AsyncMock()))['errors'], 0)
            await db.refresh(row)
            self.assertEqual(row.notes, 'newer local decision')
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameActivity).where(GameActivity.user_id == self.other.id)), before)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameFavorite).where(GameFavorite.user_id == self.other.id)), 1)

    async def test_conflicting_identity_missing_unlock_and_private_scope(self):
        async with self.Session() as db:
            backup = await export_games(db, self.user.id)
            self.assertEqual(await export_games(db, self.other.id), {'version':1,'games':[]})
            invalid = Backup.model_validate(backup)
            base = next(row for row in invalid.games if row.entry)
            base.work.identities['igdb.game'] = str(100+self.child)
            result = await restore_games(db, self.other.id, invalid, cancelled=AsyncMock())
            self.assertGreater(result['errors'], 0)
            self.assertIsNone(await db.scalar(select(GameEntry).where(GameEntry.user_id == self.other.id)))
            invalid = Backup.model_validate(backup)
            base = next(row for row in invalid.games if row.entry)
            base.unlocks[0].api_name = 'missing definition'
            with patch('core.game_achievements.GameAchievements.run', new_callable=AsyncMock):
                result = await restore_games(db, self.other.id, invalid, cancelled=AsyncMock())
            self.assertGreater(result['errors'], 0)
            self.assertIsNone(await db.scalar(select(GameEntry).where(GameEntry.user_id == self.other.id)))
            # The archive opt-out does not include any Games data.
            payload = await build_export_zip(db, self.user, None, include_games=False)
            self.assertNotIn('games.json', zipfile.ZipFile(io.BytesIO(payload)).namelist())

    async def test_restore_resolves_provider_identity_on_a_different_catalogue_id(self):
        async with self.Session() as db:
            from models.catalogue import CatalogueEntity
            backup = Backup.model_validate(await export_games(db, self.user.id))
            base = next(row for row in backup.games if row.entry)
            base.unlocks = []; base.activities = []; base.entry.achievement_mode = 'count'; base.entry.achievement_count = 1
            base.work.identities = {'igdb.game':'999999'}
            other_work = CatalogueEntity(kind='game', name='Different local ID', attributes={'game_class':'game'})
            db.add(other_work); await db.flush()
            db.add(CatalogueIdentity(entity_id=other_work.id, namespace='igdb.game', external_id='999999', source='igdb'))
            await db.commit()
            result = await restore_games(db, self.other.id, Backup(games=[base]), cancelled=AsyncMock())
            self.assertEqual(result, {'games':1,'errors':0})
            row = await db.scalar(select(GameEntry).where(GameEntry.user_id == self.other.id))
            self.assertEqual(row.work_id, other_work.id)
            self.assertNotEqual(row.work_id, self.base)

    async def test_games_only_upload_needs_no_tmdb_and_checks_approval(self):
        from routers import export as export_router
        self.client._transport.app.include_router(export_router.router, prefix='/export')
        async with self.Session() as db:
            payload = await build_export_zip(db, self.user, None, include_watched=False, include_ratings=False,
                include_collection=False, include_lists=False, include_comments=False)
        options = {key:'false' for key in ('watched','ratings','collection','lists','comments',
            'api_keys','media_connections','scrobble_connections','connections')}
        options['games'] = 'true'
        with patch.object(export_router, 'run_scrob_import', new_callable=AsyncMock) as worker, patch.object(
                export_router.settings_store, 'get_server_tmdb_key', return_value=None):
            response = await self.client.post('/export/import', data=options, files={'file':('backup.zip',payload,'application/zip')})
            self.assertEqual(response.status_code, 200, response.text)
            worker.assert_awaited_once()
            self.viewer.games_access = False
            response = await self.client.post('/export/import', data=options, files={'file':('backup.zip',payload,'application/zip')})
            self.assertEqual(response.status_code, 403, response.text)
