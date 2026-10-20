"""Actual mt058 schema, API/privacy and race checks in disposable PostgreSQL."""
import asyncio
import importlib
import os
import unittest
import uuid
from datetime import date

os.environ.setdefault('SECRET_KEY', 'games-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

import httpx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core import game_tracking
from db import get_db
from dependencies import get_current_user, get_current_user_or_api_key, get_optional_user, get_tracking_write_user
from models import Base, GlobalSettings, User, UserProfileData
from models.base import PrivacyLevel
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueRelationship
from models.games import GameAchievement, GameChildState, GameEntry
from routers.games import router
from routers.tracking import router as preferences_router
from schemas_games import ChildPatch, GamePatch

URL = os.getenv('GAMES_TEST_DATABASE_URL')


@unittest.skipUnless(URL, 'Requires explicit disposable games PostgreSQL')
class GamesDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not (':55449/catalogue_test' in URL or '/anylist_test' in URL):
            raise RuntimeError('Refusing non-disposable games database')
        self.schema = 'games_test_' + uuid.uuid4().hex
        self.admin = create_async_engine(URL)
        async with self.admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(URL, connect_args={'server_settings': {'search_path': f'{self.schema},public'}})
        self.addAsyncCleanup(self.cleanup)

        def setup(conn):
            conn.execute(text(f'SET LOCAL search_path TO "{self.schema}"'))
            Base.metadata.create_all(conn, tables=[t for t in Base.metadata.sorted_tables if not t.name.startswith('game_')])
            for name in ('show_games', 'show_movies_series', 'show_game_achievements'):
                conn.execute(text(f'ALTER TABLE tracking_preferences DROP COLUMN {name}'))
            with Operations.context(MigrationContext.configure(conn)):
                importlib.import_module('migrations.versions.mt058_games_tracking').upgrade()

        async with self.engine.begin() as conn:
            await conn.run_sync(setup)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            user = User(username='player', email='player@example.test', api_key='player-key')
            other = User(username='other', email='other@example.test', api_key='other-key')
            base = CatalogueEntity(kind='game', name='Elden fixture', attributes={'game_class': 'game',
                'achievement_catalogue': {'status': 'complete', 'total': 3, 'definitions_complete': True}})
            child = CatalogueEntity(kind='game', name='Expansion fixture', attributes={'game_class': 'expansion'})
            dlc = CatalogueEntity(kind='game', name='Playable DLC', attributes={'game_class': 'dlc'})
            pack = CatalogueEntity(kind='game', name='Weapon pack', attributes={'game_class': 'pack'})
            unknown = CatalogueEntity(kind='game', name='No Steam fixture', attributes={'game_class': 'game'})
            db.add_all([user, other, base, child, dlc, pack, unknown,
                        GlobalSettings(id=1, enable_logged_out_navigation=True)])
            await db.flush()
            db.add_all([UserProfileData(user_id=u.id, privacy_level=PrivacyLevel.public) for u in (user, other)])
            db.add(CatalogueIdentity(entity_id=base.id, namespace='steam.app', external_id='1245620', source='igdb'))
            db.add_all([CatalogueRelationship(source_id=c.id, target_id=base.id, relation=r, provider='igdb')
                        for c, r in ((child, 'expansion'), (dlc, 'dlc'))])
            await db.flush()
            db.add_all([GameAchievement(work_id=base.id, app_id='1245620', api_name=f'A{i}', name=f'Achievement {i}') for i in range(3)])
            await db.commit()
            self.user, self.other = user, other
            self.base, self.child, self.dlc, self.pack, self.unknown = [e.id for e in (base, child, dlc, pack, unknown)]
            self.achievement_ids = list((await db.scalars(select(GameAchievement.id).order_by(GameAchievement.id))).all())
        self.viewer = user
        app = FastAPI()
        app.include_router(router)
        app.include_router(preferences_router, prefix='/tracking')

        async def database():
            async with self.Session() as db:
                yield db

        def authenticated():
            if not self.viewer:
                raise HTTPException(401, 'Sign in')
            return self.viewer

        app.dependency_overrides[get_db] = database
        for dep in (get_current_user, get_current_user_or_api_key, get_tracking_write_user):
            app.dependency_overrides[dep] = authenticated
        app.dependency_overrides[get_optional_user] = lambda: self.viewer
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def cleanup(self):
        if hasattr(self, 'client'):
            await self.client.aclose()
        await self.engine.dispose()
        async with self.admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin.dispose()

    async def save(self, work_id=None, **body):
        response = await self.client.patch(f'/tracking/games/entries/{work_id or self.base}', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_completion_snapshot_cumulative_time_replays_and_unknown_dates(self):
        row = await self.save(status='completed', total_seconds=36000, replay_count=3)
        self.assertEqual(row['finish_date'], date.today().isoformat())
        self.assertEqual(row['completion_seconds'], 36000)
        self.assertTrue(row['completion_comparison_eligible'])
        updated = await self.save(revision=row['revision'], total_seconds=90000, replay_count=7)
        self.assertEqual(updated['status'], 'completed')
        self.assertEqual(updated['completion_seconds'], 36000)
        invalid = await self.save(revision=updated['revision'], total_seconds=100)
        self.assertEqual(invalid['completion_seconds'], 36000)
        self.assertFalse(invalid['completion_comparison_eligible'])
        historical = await self.save(self.unknown, status='completed', finish_date=None, total_seconds=8000)
        self.assertIsNone(historical['finish_date'])
        self.assertIsNone(historical['completion_seconds'])
        known = await self.save(self.unknown, revision=historical['revision'], completion_seconds=7000)
        self.assertFalse(known['completion_comparison_eligible'])
        changed = await self.save(self.unknown, revision=known['revision'], status='playing')
        self.assertEqual(changed['completion_seconds'], 7000)
        refused = await self.client.patch(f'/tracking/games/entries/{self.unknown}', json={
            'revision': changed['revision'], 'completion_seconds': 9000})
        self.assertEqual(refused.status_code, 422)

    async def test_first_completion_prefills_empty_total_and_stale_edits_rejected(self):
        row = await self.save(status='completed', completion_seconds=0)
        self.assertEqual(row['total_seconds'], 0)
        self.assertEqual(row['completion_seconds'], 0)
        stale = await self.client.patch(f'/tracking/games/entries/{self.base}', json={'revision': 10, 'score': 9})
        self.assertEqual(stale.status_code, 409)
        missing = await self.client.patch(f'/tracking/games/entries/{self.base}', json={'score': 9})
        self.assertEqual(missing.status_code, 409)

    async def test_favorite_does_not_require_membership_and_editor_save_is_atomic(self):
        favorite = await self.client.put(f'/tracking/games/favorites/{self.base}', json={'favorite': True})
        self.assertEqual(favorite.status_code, 200, favorite.text)
        self.assertFalse(favorite.json()['listed'])
        self.assertTrue(favorite.json()['favorite'])
        row = await self.save(status='playing', favorite=False)
        self.assertFalse(row['favorite'])
        row = await self.save(revision=row['revision'], favorite=True, score=8)
        self.assertTrue(row['favorite'])
        self.assertEqual(row['score'], 8)
        await self.client.delete(f'/tracking/games/entries/{self.base}?revision={row["revision"]}&confirmed=true')
        state = (await self.client.get(f'/tracking/games/works/{self.base}')).json()
        self.assertFalse(state['favorite'])
        self.assertFalse(state['listed'])

    async def test_expansion_requires_base_and_retains_unlisted_private_state(self):
        path = f'/tracking/games/children/{self.child}'
        blocked = await self.client.patch(path, json={'status': 'completed'})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['detail']['code'], 'base_game_required')
        empty = await self.client.get('/tracking/games/users/player/list')
        self.assertEqual(empty.json()['total'], 0)
        base = await self.save(status='playing', score=7)
        child = await self.client.patch(path, json={'status': 'completed', 'score': 9})
        self.assertEqual(child.status_code, 200, child.text)
        self.assertTrue(child.json()['listed'])
        self.assertIsNone(child.json()['total_seconds'])
        rows = (await self.client.get('/tracking/games/users/player/list?sort=score')).json()
        self.assertEqual([r['score'] for r in rows['results']], [9, 7])
        removed = await self.client.delete(f'/tracking/games/entries/{self.base}?revision={base["revision"]}&confirmed=true')
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual((await self.client.get('/tracking/games/users/player/list')).json()['total'], 0)
        retained = (await self.client.get(f'/tracking/games/works/{self.child}')).json()
        self.assertEqual(retained['score'], 9)
        self.assertFalse(retained['listed'])
        await self.save(status='planning')
        self.assertEqual((await self.client.get('/tracking/games/users/player/list')).json()['total'], 1)
        restored = await self.client.patch(path, json={'revision': retained['revision']})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['score'], 9)
        self.assertEqual(restored.json()['status'], 'completed')

    async def test_child_cannot_write_hours_or_change_parent_rating(self):
        row = await self.save(score=6)
        bad = await self.client.patch(f'/tracking/games/children/{self.child}', json={'total_seconds': 100})
        self.assertEqual(bad.status_code, 422)
        dlc = await self.client.patch(f'/tracking/games/children/{self.dlc}?listed=false', json={'status': 'completed', 'score': 10})
        self.assertEqual(dlc.status_code, 200, dlc.text)
        self.assertFalse(dlc.json()['listed'])
        self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}')).json()['score'], row['score'])
        for work_id in (self.child, self.dlc, self.pack):
            bad = await self.client.patch(f'/tracking/games/entries/{work_id}', json={})
            self.assertEqual(bad.status_code, 422)

    async def test_optional_achievements_unknown_explicit_zero_and_mode_switch_evidence(self):
        row = await self.save()
        self.assertIsNone(row['achievements']['percentage'])
        row = await self.save(revision=row['revision'], achievement_mode='targeted', expected_achievement_total=3,
                              unlocked_ids=self.achievement_ids[:2])
        self.assertEqual(row['achievements']['count'], 2)
        response = await self.client.patch(f'/tracking/games/entries/{self.base}', json={
            'revision': row['revision'], 'achievement_mode': 'count', 'achievement_count': 1, 'expected_achievement_total': 3})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'confirm_simplified_tracking')
        row = await self.save(revision=row['revision'], achievement_mode='count', achievement_count=0,
                              expected_achievement_total=3, confirm_simplified=True)
        self.assertEqual(row['achievements']['percentage'], 0)
        self.assertEqual(row['achievements']['exact_count'], 2)
        exact = (await self.client.get(f'/tracking/games/works/{self.base}/achievements')).json()
        self.assertEqual(sum(a['unlocked'] for a in exact['results']), 2)
        row = await self.save(revision=row['revision'], achievement_mode='targeted', expected_achievement_total=3)
        self.assertEqual(row['achievements']['count'], 2)
        unknown = await self.save(self.unknown, achievement_mode='percentage', achievement_percentage=0)
        self.assertEqual(unknown['achievements']['percentage'], 0)
        self.assertIsNone(unknown['achievements']['count'])
        bad = await self.client.patch(f'/tracking/games/entries/{self.unknown}', json={
            'revision': unknown['revision'], 'achievement_mode': 'count', 'achievement_count': 1})
        self.assertEqual(bad.status_code, 409)

    async def test_achievement_bounds_stale_catalogue_and_cross_work_selection(self):
        row = await self.save()
        for payload, code in (({'achievement_mode': 'count', 'achievement_count': 4, 'expected_achievement_total': 3}, 422),
                              ({'achievement_mode': 'count', 'achievement_count': 1, 'expected_achievement_total': 2}, 409),
                              ({'achievement_mode': 'targeted', 'unlocked_ids': [999999], 'expected_achievement_total': 3}, 422)):
            r = await self.client.patch(f'/tracking/games/entries/{self.base}', json={'revision': row['revision'], **payload})
            self.assertEqual(r.status_code, code, r.text)
        unchanged = (await self.client.get(f'/tracking/games/works/{self.base}')).json()
        self.assertEqual(unchanged['revision'], row['revision'])
        self.assertIsNone(unchanged['achievements']['mode'])

    async def test_visibility_does_not_block_direct_links_or_public_profiles_and_notes_stay_private(self):
        row = await self.save(notes='Private note', status='playing')
        prefs = await self.client.patch('/tracking/preferences', json={'show_games': False, 'show_movies_series': False})
        self.assertEqual(prefs.status_code, 200, prefs.text)
        self.assertFalse(prefs.json()['show_games'])
        self.assertTrue(prefs.json()['combine_lists'])
        self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}')).json()['notes'], 'Private note')
        self.viewer = self.other
        public = await self.client.get('/tracking/games/users/player/list')
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()['total'], 1)
        self.assertIsNone(public.json()['results'][0]['notes'])
        self.assertIsNone(public.json()['results'][0]['revision'])
        async with self.Session() as db:
            profile = await db.get(UserProfileData, self.user.id)
            # The profile key is not user_id in all schema generations.
            if profile is None:
                profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.user.id))
            profile.privacy_level = PrivacyLevel.private
            await db.commit()
        self.assertEqual((await self.client.get('/tracking/games/users/player/list')).status_code, 403)
        self.viewer = None
        self.assertEqual((await self.client.get('/tracking/games/users/player/list')).status_code, 403)

    async def test_database_rejects_cross_owner_or_wrong_work_children_and_independent_packs(self):
        base = await self.save()
        async with self.Session() as db:
            for values in ({'user_id': self.other.id, 'work_id': self.child, 'base_work_id': self.base, 'base_entry_id': 1},
                           {'user_id': self.user.id, 'work_id': self.child, 'base_work_id': self.unknown, 'base_entry_id': 1}):
                actual_id = await db.scalar(select(GameEntry.id).where(GameEntry.work_id == self.base))
                values['base_entry_id'] = actual_id
                db.add(GameChildState(**values))
                with self.assertRaises(IntegrityError):
                    await db.commit()
                await db.rollback()
            db.add(GameEntry(user_id=self.user.id, work_id=self.pack))
            with self.assertRaises(IntegrityError):
                await db.commit()
            await db.rollback()

    async def test_concurrent_base_removal_and_child_save_cannot_leave_orphan_membership(self):
        base = await self.save()

        async def save_child():
            async with self.Session() as db:
                try:
                    return await game_tracking.save_child(db, self.user.id, self.child, ChildPatch(status='completed'), listed=True)
                except HTTPException as error:
                    self.assertEqual(error.status_code, 409)

        async def remove_base():
            async with self.Session() as db:
                await game_tracking.remove_entry(db, self.user.id, self.base, base['revision'])

        await asyncio.gather(save_child(), remove_base())
        async with self.Session() as db:
            self.assertIsNone(await db.scalar(select(GameEntry).where(GameEntry.work_id == self.base)))
            child = await db.scalar(select(GameChildState).where(GameChildState.work_id == self.child))
            if child:
                self.assertIsNone(child.base_entry_id)

    async def test_migration_round_trip(self):
        migration = importlib.import_module('migrations.versions.mt058_games_tracking')
        def round_trip(conn):
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
                migration.upgrade()
        async with self.engine.begin() as conn:
            await conn.run_sync(round_trip)
        self.assertEqual((await self.client.get('/tracking/preferences')).status_code, 200)
