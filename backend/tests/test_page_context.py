"""Real private page reads against the explicit disposable tracking database."""
import os
import unittest
from datetime import datetime, timedelta

from sqlalchemy import event, select

import test_tracking_api as fixture
from core.security import create_session_token
from dependencies import get_current_user, get_current_user_or_api_key, get_optional_user, get_optional_user_or_api_key
from models import UserSettings
from models.base import CollectionSource
from models.sync import SyncJob, SyncStatus
from models.tracking import SyncReview, TrackingPreferences
from routers.auth import router as auth_router


@unittest.skipUnless(os.getenv('TRACKING_TEST_DATABASE_URL'), 'Requires disposable PostgreSQL')
class PageContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.TrackingApiTests.asyncSetUp(self)
        app = self.client._transport.app
        app.include_router(auth_router, prefix='/auth')
        for dependency in (get_current_user, get_current_user_or_api_key, get_optional_user, get_optional_user_or_api_key):
            app.dependency_overrides.pop(dependency, None)
        self.client.headers['Authorization'] = 'Bearer ' + create_session_token(self.owner)
        self.db.add(UserSettings(user_id=self.owner.id, blur_explicit=False,
            preferences={'minimum_popularity': 19}, trakt_access_token='private-provider-token'))
        self.db.add(TrackingPreferences(user_id=self.owner.id, combine_lists=False))
        await self.db.commit()
        self.statements = []
        def observe(conn, cursor, statement, params, context, executemany):
            self.statements.append(statement)
        self.observe = observe
        event.listen(self.engine.sync_engine, 'before_cursor_execute', observe)

    async def asyncTearDown(self):
        event.remove(self.engine.sync_engine, 'before_cursor_execute', self.observe)
        await fixture.TrackingApiTests.asyncTearDown(self)

    async def get(self, path):
        response = await self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_one_page_context_matches_existing_routes_with_five_reads_and_no_writes(self):
        value = await self.get('/auth/page-context')
        reads = [sql for sql in self.statements if not sql.startswith('SAVEPOINT')]
        self.assertEqual(len(reads), 5, reads)
        self.assertTrue(all(sql.lstrip().startswith('SELECT') for sql in reads))
        self.assertEqual(value['user'], await self.get('/auth/me'))
        self.assertEqual(value['settings'], await self.get('/auth/settings'))
        self.assertEqual(value['preferences'], await self.get('/tracking/preferences'))
        self.assertEqual(value['profile'], await self.get('/profile/me'))
        self.assertEqual(value['notifications'], await self.get('/tracking/notifications'))
        self.assertNotIn('trakt_access_token', value['settings'])
        self.assertEqual((await self.client.get('/auth/page-context')).headers['cache-control'], 'private, no-store')

    async def test_feed_matches_inbox_reviews_retention_priority_and_empty_delivery_boundary(self):
        now = datetime.utcnow()
        for index, (kind, state, priority, age) in enumerate([
            ('conflict', 'pending', 'low', 30), ('rating_needed', 'pending', 'low', 30),
            ('updated', 'confirmed', 'low', 30), ('updated', 'confirmed', 'low', 1),
            ('outbound_pending', 'pending', 'low', 1), ('conflict', 'confirmed', 'low', 30),
            ('updated', 'confirmed', 'high', 30),
        ]):
            self.db.add(SyncReview(user_id=self.owner.id, media_id=self.movie.id, kind=kind, state=state,
                priority=priority, message=f'Notification {index}', created_at=now-timedelta(days=age, seconds=index)))
        await self.db.commit()
        light = await self.get('/tracking/notifications')
        self.assertEqual(len(light['results']), 5)
        self.assertEqual(light['outbound'], [])
        self.assertEqual(light, await self.get('/tracking/recent-events'))
        prefs = await self.db.get(TrackingPreferences, self.owner.id)
        prefs.low_priority_notifications = False
        await self.db.commit()
        self.assertEqual(await self.get('/tracking/notifications'), await self.get('/tracking/recent-events'))

    async def test_feed_uses_sync_job_headers_for_failure_alerts_and_respects_recovery(self):
        for index in range(2):
            self.db.add(SyncJob(user_id=self.owner.id, source=CollectionSource.trakt, status=SyncStatus.failed,
                stats={'large_history': 'x'*30000}, error_message='Provider unavailable',
                updated_at=datetime.utcnow()-timedelta(seconds=index)))
        await self.db.commit()
        self.statements.clear()
        light = await self.get('/tracking/notifications')
        self.assertEqual(light['results'][0]['kind'], 'connection_failure')
        self.assertTrue(all('sync_jobs.stats' not in sql and 'sync_jobs.warnings' not in sql for sql in self.statements))
        self.assertEqual(light, await self.get('/tracking/recent-events'))
        self.db.add(SyncJob(user_id=self.owner.id, source=CollectionSource.trakt, status=SyncStatus.completed))
        await self.db.commit()
        self.assertEqual((await self.get('/tracking/notifications'))['results'], [])

    async def test_preferences_and_revocation_are_fresh_and_scoped_to_viewer(self):
        first = await self.get('/auth/page-context')
        response = await self.client.patch('/tracking/preferences', json={'combine_lists': True, 'show_new_ratings_popup': False})
        self.assertEqual(response.status_code, 200, response.text)
        second = await self.get('/auth/page-context')
        self.assertFalse(first['preferences']['combine_lists'])
        self.assertTrue(second['preferences']['combine_lists'])
        self.assertFalse(second['preferences']['show_new_ratings_popup'])
        self.client.headers['Authorization'] = 'Bearer ' + create_session_token(self.friend)
        other = await self.get('/auth/page-context')
        self.assertEqual(other['user']['id'], self.friend.id)
        self.assertTrue(other['preferences']['combine_lists'])
        self.assertEqual(other['notifications']['results'], [])
        self.friend.session_version += 1
        await self.db.commit()
        self.assertEqual((await self.client.get('/auth/page-context')).status_code, 401)

    async def test_feed_keeps_the_existing_window_before_preference_filtering(self):
        now = datetime.utcnow()
        self.db.add_all([SyncReview(user_id=self.owner.id, kind='outbound_pending' if index < 100 else 'conflict',
            state='pending', priority='low', message='Queue window', created_at=now-timedelta(seconds=index))
            for index in range(101)])
        await self.db.commit()
        light = await self.get('/tracking/notifications')
        self.assertEqual(light['results'], [])
        full = await self.get('/tracking/recent-events')
        self.assertEqual(light['results'], full['results'])
        self.assertEqual(light['pending'], full['pending'])

    async def test_profile_projection_includes_avatar_and_cached_background(self):
        from models import GlobalSettings, UserProfileData
        profile = await self.db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.owner.id))
        profile.avatar_path = 'fixture.webp'
        profile.background_url = 'https://image.tmdb.org/t/p/original/fixture.jpg'
        settings = await self.db.get(GlobalSettings, 1)
        settings.image_cache_enabled = True
        await self.db.commit()
        value = await self.get('/auth/page-context')
        self.assertEqual(value['profile'], await self.get('/profile/me'))
        self.assertEqual(value['profile']['avatar_url'], f'/profile/avatar/{self.owner.id}')

    async def test_extending_retention_does_not_resurrect_expired_notices(self):
        self.db.add(SyncReview(user_id=self.owner.id, kind='updated', state='confirmed', priority='low',
            message='Expired', created_at=datetime.utcnow()-timedelta(days=10)))
        await self.db.commit()
        self.assertEqual((await self.get('/tracking/notifications'))['results'], [])
        response = await self.client.patch('/tracking/preferences', json={'low_priority_retention_days': 30})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.get('/tracking/notifications'))['results'], [])
