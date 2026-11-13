import os
import unittest

from sqlalchemy import event

import test_statistics_pg as fixtures
from models import Follow, User, UserProfileData
from models.base import PrivacyLevel


@unittest.skipUnless(os.getenv('STATISTICS_TEST_DATABASE_URL'), 'Requires disposable PostgreSQL')
class ProfileSocialTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.StatisticsDatabaseTests.asyncSetUp
    asyncTearDown = fixtures.StatisticsDatabaseTests.asyncTearDown

    async def test_people_match_existing_visibility_without_reading_tracked_media(self):
        async with self.Session() as db:
            people = [
                User(email=f'person-{i}@example.test', username=f'person-{i}', api_key=f'person-{i}')
                for i in range(4)
            ]
            db.add_all(people)
            await db.flush()
            for i, person in enumerate(people):
                db.add(UserProfileData(user_id=person.id, privacy_level=PrivacyLevel.private if i == 3 else PrivacyLevel.public))
            db.add_all([
                Follow(follower_id=self.user_id, following_id=people[0].id),
                Follow(follower_id=people[1].id, following_id=self.user_id),
                Follow(follower_id=self.user_id, following_id=people[2].id),
                Follow(follower_id=people[2].id, following_id=self.user_id),
                Follow(follower_id=self.user_id, following_id=people[3].id),
            ])
            await db.commit()
        original = (await self.client.get('/tracking/people/statistics-fixture')).json()
        statements = []
        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lower())
        event.listen(self.engine.sync_engine, 'before_cursor_execute', record)
        try:
            response = await self.client.get('/tracking/people/statistics-fixture/social')
        finally:
            event.remove(self.engine.sync_engine, 'before_cursor_execute', record)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        data = response.json()
        self.assertEqual(data['people'], original['people'])
        self.assertEqual(len(data['people']), 3)
        self.assertFalse(any('tracked_entries' in query or 'from media ' in query or 'tracking_activities' in query for query in statements))
        self.assertNotIn('recent_activity', data)
        self.viewer = None
        public = await self.client.get('/tracking/people/statistics-fixture/social')
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()['people'], data['people'])
        async with self.Session() as db:
            profile = await db.get(UserProfileData, self.user_id)
            profile.privacy_level = PrivacyLevel.private
            await db.commit()
        self.assertEqual((await self.client.get('/tracking/people/statistics-fixture/social')).status_code, 403)
        self.assertEqual((await self.client.get('/tracking/people/missing/social')).status_code, 404)
