"""Focused tracking API tests against an isolated transactional PostgreSQL fixture.

Set TRACKING_TEST_DATABASE_URL to the disposable local database; never production.
"""
import os
import unittest
from unittest.mock import AsyncMock, patch
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

import httpx
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from db import get_db
from dependencies import get_current_user, get_current_user_or_api_key, get_optional_user, get_optional_user_or_api_key
from models import User, UserSettings, UserProfileData, Media, GlobalSettings, Follow, WatchEvent, Collection, CollectionFile, Rating, Show, MediaServerConnection
from models.base import CollectionSource, MediaType, PrivacyLevel
from models.tracking import TrackedEntry, TrackingDeletion, StreamBaseline, StreamAction, SyncReview, TrackingPreferences, TrackingActivity, CloudBaseline, ProviderIgnore, ProviderMatch, CloudAction, WebPushSubscription
from models.streaming_library import StreamingLibraryIntent, StreamingLibraryDelivery
from models.sync import SyncJob, SyncStatus
from routers.tracking import router
from routers.push import router as push_router
from routers.comments import router as comments_router
from routers.profile import router as profile_router
from routers.ratings import router as ratings_router


@unittest.skipUnless(os.getenv('TRACKING_TEST_DATABASE_URL'), 'Requires disposable PostgreSQL database')
class TrackingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ['TRACKING_TEST_DATABASE_URL'])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.db = AsyncSession(bind=self.connection, expire_on_commit=False, join_transaction_mode='create_savepoint')
        self.owner = User(username='tracking-test-owner', email='tracking-owner@example.test', api_key='tracking-test-owner')
        self.friend = User(username='tracking-test-friend', email='tracking-friend@example.test', api_key='tracking-test-friend')
        self.db.add_all([self.owner,self.friend]); await self.db.flush()
        self.db.add(UserProfileData(user_id=self.owner.id, privacy_level=PrivacyLevel.public))
        self.db.add(UserProfileData(user_id=self.friend.id, privacy_level=PrivacyLevel.private))
        self.movie = Media(title='Fixture Film',media_type=MediaType.movie,release_date='2020-01-01')
        self.show = Media(title='Fixture Show',media_type=MediaType.series)
        self.db.add_all([self.movie,self.show]); await self.db.commit()
        settings = await self.db.get(GlobalSettings,1)
        if settings:
            settings.enable_logged_out_navigation = False
        else:
            settings = GlobalSettings(id=1, enable_logged_out_navigation=False)
            self.db.add(settings)
        await self.db.commit()
        self.viewer = self.owner
        app=FastAPI(); app.include_router(router,prefix='/tracking'); app.include_router(push_router,prefix='/push'); app.include_router(comments_router,prefix='/comments'); app.include_router(profile_router,prefix='/profile'); app.include_router(ratings_router,prefix='/ratings')
        async def session(): yield self.db
        app.dependency_overrides[get_db]=session
        app.dependency_overrides[get_current_user]=lambda:self.viewer
        app.dependency_overrides[get_current_user_or_api_key]=lambda:self.viewer
        app.dependency_overrides[get_optional_user]=lambda:self.viewer
        app.dependency_overrides[get_optional_user_or_api_key]=lambda:self.viewer
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')
        from core.streaming_library import dispatch_library_deliveries
        async def deliver_in_fixture(user_id, media_id):
            await dispatch_library_deliveries(self.db, user_id, media_id)
        self.delivery_patch=patch('core.streaming_library.deliver_library_intent', deliver_in_fixture)
        self.delivery_patch.start()
        self.local_outbound = AsyncMock()
        self.local_outbound_patch = patch(
            'core.local_outbound.dispatch_local_tracking_delta', self.local_outbound,
        )
        self.local_outbound_patch.start()
        self.local_rollback = AsyncMock()
        self.local_rollback_patch = patch(
            'core.local_outbound.dispatch_local_watch_rollback', self.local_rollback,
        )
        self.local_rollback_patch.start()

    async def asyncTearDown(self):
        self.delivery_patch.stop()
        self.local_outbound_patch.stop()
        self.local_rollback_patch.stop()
        await self.client.aclose(); await self.db.close()
        await self.transaction.rollback(); await self.connection.close(); await self.engine.dispose()

    async def save(self, media, **body):
        return await self.client.patch(f'/tracking/entry/{media.id}',json=body)

    async def test_planning_rating_and_private_notes(self):
        res=await self.save(self.movie,status='planning',manual_score=7.5,notes='private journal')
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(res.json()['score'],7.5)
        self.assertIsNone(res.json()['start_date'])
        self.viewer=self.friend
        res=await self.client.get(f'/tracking/profile/{self.owner.username}/movie')
        self.assertEqual(res.status_code,200,res.text)
        self.assertNotIn('notes',res.json()['entries'][0])
        self.assertFalse(res.json()['owner'])

    async def test_anonymous_comment_reads_follow_instance_access_switch(self):
        self.viewer = None
        blocked = await self.client.get('/comments', params={'media_type': 'movie', 'tmdb_id': 1})
        self.assertEqual(blocked.status_code, 401, blocked.text)

        settings = await self.db.get(GlobalSettings, 1)
        settings.enable_logged_out_navigation = True
        await self.db.commit()
        allowed = await self.client.get('/comments', params={'media_type': 'movie', 'tmdb_id': 1})
        self.assertEqual(allowed.status_code, 200, allowed.text)

    async def test_public_profile_does_not_name_private_social_connections(self):
        self.db.add(Follow(follower_id=self.friend.id, following_id=self.owner.id))
        settings = await self.db.get(GlobalSettings, 1)
        settings.enable_logged_out_navigation = True
        await self.db.commit()

        self.viewer = None
        public = await self.client.get(f'/profile/{self.owner.id}')
        self.assertEqual(public.status_code, 200, public.text)
        self.assertEqual(public.json()['follower_count'], 1)
        self.assertEqual(public.json()['followers'], [])

        self.viewer = self.owner
        owner = await self.client.get(f'/profile/{self.owner.id}')
        self.assertEqual(owner.status_code, 200, owner.text)
        self.assertEqual(len(owner.json()['followers']), 1)
        self.assertEqual(owner.json()['followers'][0]['id'], self.friend.id)

    async def test_profile_search_and_follow_hide_legacy_private_profiles(self):
        friend_profile = await self.db.scalar(
            select(UserProfileData).where(UserProfileData.user_id == self.friend.id)
        )
        friend_profile.privacy_level = PrivacyLevel.friends_only
        await self.db.commit()

        hidden = await self.client.get('/profile/search', params={'q': self.friend.username})
        self.assertEqual(hidden.status_code, 200, hidden.text)
        self.assertEqual(hidden.json()['results'], [])
        blocked = await self.client.post(f'/profile/{self.friend.id}/follow')
        self.assertEqual(blocked.status_code, 404, blocked.text)

        # Mutual legacy follows must not make a friends-only profile public.
        self.db.add_all([
            Follow(follower_id=self.owner.id, following_id=self.friend.id),
            Follow(follower_id=self.friend.id, following_id=self.owner.id),
        ])
        await self.db.commit()
        profile_blocked = await self.client.get(f'/profile/{self.friend.id}')
        self.assertEqual(profile_blocked.status_code, 403, profile_blocked.text)

        friend_profile.privacy_level = PrivacyLevel.public
        await self.db.commit()
        visible = await self.client.get('/profile/search', params={'q': self.friend.username})
        self.assertEqual(visible.status_code, 200, visible.text)
        self.assertEqual(visible.json()['results'][0]['id'], self.friend.id)
        followed = await self.client.post(f'/profile/{self.friend.id}/follow')
        self.assertEqual(followed.status_code, 200, followed.text)

    async def test_season_average_and_manual_restoration(self):
        self.assertEqual((await self.save(self.show,manual_score=9)).status_code,200)
        res=await self.save(self.show,season_scores={'1':6})
        self.assertEqual(res.status_code,409)
        await self.db.rollback()
        await self.db.refresh(self.show)
        await self.db.refresh(self.owner)
        res=await self.save(self.show,rating_mode='average',season_scores={'0':10,'1':6,'2':8,'3':0})
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(res.json()['score'],7)
        self.assertEqual(res.json()['manual_score'],9)
        res=await self.save(self.show,season_scores={'1':0,'2':0})
        self.assertIsNone(res.json()['score'])
        res=await self.save(self.show,rating_mode='manual')
        self.assertEqual(res.json()['score'],9)

    async def test_invalid_half_points_and_movie_seasons_rejected(self):
        self.assertEqual((await self.save(self.movie,manual_score=7.2)).status_code,422)
        self.assertEqual((await self.save(self.movie,season_scores={'1':8})).status_code,422)

    async def test_catalog_search_tolerates_small_title_errors(self):
        response=await self.client.get('/tracking/catalog',params={'media_type':'movie','q':'Fxtur Flm'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['results'][0]['id'],self.movie.id)

    async def test_manual_completed_only_defaults_finish(self):
        res=await self.save(self.movie,status='completed')
        self.assertEqual(res.status_code,200,res.text)
        self.assertIsNone(res.json()['start_date'])
        self.assertEqual(res.json()['finish_date'],datetime.now(timezone.utc).date().isoformat())
        res=await self.save(self.movie,finish_date=None)
        self.assertIsNone(res.json()['finish_date'])
        res=await self.save(self.movie,status='completed')
        self.assertIsNone(res.json()['finish_date'])
        prompts=(await self.db.execute(select(SyncReview).where(SyncReview.kind=='rating_needed'))).scalars().all()
        self.assertEqual(prompts,[])

    async def test_web_push_subscription_is_scoped_to_current_user(self):
        body={'endpoint':'https://push.example.test/subscription/fixture','keys':{'p256dh':'p'*80,'auth':'a'*24}}
        created=await self.client.post('/push/subscriptions',json=body)
        self.assertEqual(created.status_code,200,created.text)
        status=await self.client.get('/push/status')
        self.assertEqual(status.json(),{'enabled':True,'subscriptions':1})
        row=(await self.db.execute(select(WebPushSubscription))).scalar_one()
        self.assertEqual(row.user_id,self.owner.id)
        removed=await self.client.request('DELETE','/push/subscriptions',json=body)
        self.assertEqual(removed.status_code,200,removed.text)
        self.assertFalse((await self.client.get('/push/status')).json()['enabled'])

    async def test_rating_push_dispatches_cover_and_deep_link_once(self):
        from core.web_push import dispatch_rating_pushes
        self.movie.poster_path='/fixture-poster.jpg'
        self.db.add_all([
            TrackedEntry(user_id=self.owner.id,media_id=self.movie.id,status='completed',rating_mode='manual',season_scores={},progress=0,favorite=False,rewatch_count=0),
            WebPushSubscription(user_id=self.owner.id,endpoint='https://push.example.test/fixture',p256dh='p'*80,auth='a'*24),
            SyncReview(user_id=self.owner.id,media_id=self.movie.id,provider='trakt',kind='rating_needed',state='confirmed',priority='low',message='Fixture Film completed. Rate now!',payload={'push_state':'pending','delivered_subscription_ids':[]}),
        ])
        await self.db.commit()
        with patch('core.web_push.asyncio.to_thread',new=AsyncMock()) as deliver:
            stats=await dispatch_rating_pushes(self.db)
            await dispatch_rating_pushes(self.db)
        self.assertEqual(stats['sent'],1)
        deliver.assert_awaited_once()
        payload=deliver.await_args.args[2]
        self.assertEqual(payload['url'],f'/title/{self.movie.id}?rate=1')
        self.assertEqual(payload['icon'],'https://image.tmdb.org/t/p/w500/fixture-poster.jpg')

    async def test_zero_clears_score_and_activity_groups(self):
        await self.save(self.movie,manual_score=8,status='watching')
        res=await self.save(self.movie,manual_score=0,status='paused')
        self.assertIsNone(res.json()['score'])
        profile=await self.client.get(f'/tracking/people/{self.owner.username}')
        self.assertEqual(len(profile.json()['recent_activity']),1)
        self.assertEqual(profile.json()['recent_activity'][0]['status'],'paused')
        self.assertEqual((await self.client.get('/tracking/activity')).json()['results'],[])

    async def test_home_activity_contains_followed_public_profiles_only(self):
        owner_activity=TrackingActivity(user_id=self.owner.id,media_id=self.movie.id,status='watching',score=None)
        friend_activity=TrackingActivity(user_id=self.friend.id,media_id=self.movie.id,status='watching',score=None,
            payload={'episodes_watched':1},created_at=datetime.utcnow()-timedelta(hours=1))
        friend_latest=TrackingActivity(user_id=self.friend.id,media_id=self.movie.id,status='completed',score=8,
            payload={'rating_changed':True},created_at=datetime.utcnow())
        friend_profile=(await self.db.execute(select(UserProfileData).where(UserProfileData.user_id==self.friend.id))).scalar_one()
        friend_profile.privacy_level=PrivacyLevel.public
        self.db.add_all([owner_activity,friend_activity,friend_latest,Follow(follower_id=self.owner.id,following_id=self.friend.id)])
        await self.db.commit()
        results=(await self.client.get('/tracking/activity')).json()['results']
        self.assertEqual([(row['username'],row['status']) for row in results],[(self.friend.username,'completed')])
        self.assertEqual(results[0]['payload']['episodes_watched'],1)
        self.assertTrue(results[0]['payload']['rating_changed'])

    async def test_activity_never_claims_rated_without_a_score(self):
        friend_profile=(await self.db.execute(select(UserProfileData).where(UserProfileData.user_id==self.friend.id))).scalar_one()
        friend_profile.privacy_level=PrivacyLevel.public
        self.db.add_all([
            Follow(follower_id=self.owner.id,following_id=self.friend.id),
            TrackingActivity(user_id=self.friend.id,media_id=self.movie.id,status='completed',score=None,
                payload={'rating_changed':True}),
            TrackingActivity(user_id=self.friend.id,media_id=self.show.id,status='watching',score=7.5,
                payload={'rating_changed':True}),
        ])
        await self.db.commit()

        results=(await self.client.get('/tracking/activity')).json()['results']
        by_media={row['media']['id']:row for row in results}
        self.assertIsNone(by_media[self.movie.id]['score'])
        self.assertFalse(by_media[self.movie.id]['payload']['rating_changed'])
        self.assertEqual(by_media[self.show.id]['score'],7.5)
        self.assertTrue(by_media[self.show.id]['payload']['rating_changed'])

    async def test_daily_activity_merges_interleaved_progress_rating_and_finished_seasons(self):
        self.show.tmdb_id = 456789
        self.show.tmdb_data = {
            'tracking_catalogue_refreshed_at': '2026-01-01T00:00:00',
            'tracking_episode_ids': [9101, 9102, 9201, 9202],
            'seasons': [
                {'season_number': 1, 'episode_count': 2},
                {'season_number': 2, 'episode_count': 3},
            ],
        }
        canonical = Show(title='Fixture Show', tmdb_id=self.show.tmdb_id)
        self.db.add(canonical); await self.db.flush()
        episodes = [
            Media(title='S1E1', media_type=MediaType.episode, tmdb_id=9101, show_id=canonical.id, season_number=1, episode_number=1, release_date='2025-01-01'),
            Media(title='S1E2', media_type=MediaType.episode, tmdb_id=9102, show_id=canonical.id, season_number=1, episode_number=2, release_date='2025-01-02'),
            Media(title='S2E1', media_type=MediaType.episode, tmdb_id=9201, show_id=canonical.id, season_number=2, episode_number=1, release_date='2026-01-01'),
            Media(title='S2E2', media_type=MediaType.episode, tmdb_id=9202, show_id=canonical.id, season_number=2, episode_number=2, release_date='2026-01-02'),
        ]
        self.db.add_all(episodes); await self.db.commit()

        self.assertEqual((await self.save(self.show, status='watching', progress=1)).status_code, 200)
        self.assertEqual((await self.save(self.movie, status='watching')).status_code, 200)
        self.assertEqual((await self.save(self.show, manual_score=8)).status_code, 200)
        self.assertEqual((await self.save(self.show, progress=4)).status_code, 200)

        rows = (await self.db.execute(select(TrackingActivity).where(
            TrackingActivity.user_id == self.owner.id, TrackingActivity.media_id == self.show.id))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].score, 8)
        self.assertEqual(rows[0].payload['episodes_watched'], 4)
        self.assertEqual(rows[0].payload['position'], 'S2E2')
        self.assertEqual(rows[0].payload['finished_seasons'], [1])
        self.assertTrue(rows[0].payload['rating_changed'])
        self.assertTrue(rows[0].payload['status_changed'])

        # Rating first and a later status update still produce one daily card.
        await self.db.execute(delete(TrackingActivity).where(TrackingActivity.media_id == self.movie.id))
        await self.db.commit()
        self.assertEqual((await self.save(self.movie, manual_score=7.5)).status_code, 200)
        self.assertEqual((await self.save(self.movie, status='paused')).status_code, 200)
        movie_rows = (await self.db.execute(select(TrackingActivity).where(TrackingActivity.media_id == self.movie.id))).scalars().all()
        self.assertEqual(len(movie_rows), 1)
        self.assertTrue(movie_rows[0].payload['rating_changed'])
        self.assertTrue(movie_rows[0].payload['status_changed'])

    async def test_profile_stats_separate_current_totals_from_dated_viewing(self):
        self.movie.runtime = 100
        self.movie.tmdb_data = {'genres': [{'name': 'Drama'}]}
        self.show.tmdb_data = {'genres': [{'name': 'Mystery'}]}
        canonical_show = Show(title='Fixture Show', tmdb_id=98765)
        self.db.add(canonical_show); await self.db.flush()
        episode = Media(title='Fixture Episode', media_type=MediaType.episode, show_id=canonical_show.id,
                        season_number=1, episode_number=1, runtime=45)
        self.db.add_all([
            episode,
            TrackedEntry(user_id=self.owner.id, media_id=self.movie.id, status='completed', manual_score=8,
                         rating_mode='manual', season_scores={}, progress=0, favorite=False, rewatch_count=9),
            TrackedEntry(user_id=self.owner.id, media_id=self.show.id, status='watching', manual_score=7.5,
                         rating_mode='manual', season_scores={}, progress=1, favorite=False, rewatch_count=5),
        ])
        await self.db.flush()
        self.db.add_all([
            WatchEvent(user_id=self.owner.id, media_id=self.movie.id, completed=True,
                       watched_at=datetime(2025, 5, 4), play_count=2),
            WatchEvent(user_id=self.owner.id, media_id=episode.id, completed=True,
                       watched_at=datetime(2026, 1, 2), play_count=1),
            WatchEvent(user_id=self.owner.id, media_id=episode.id, completed=True,
                       watched_at=None, play_count=1),
        ])
        await self.db.commit()

        result = (await self.client.get(f'/tracking/profile/{self.owner.username}/stats/summary')).json()
        self.assertEqual(result['current']['statuses']['completed'], 1)
        self.assertEqual(result['current']['statuses']['watching'], 1)
        self.assertEqual(result['viewing']['unique_titles'], 2)
        self.assertEqual(result['viewing']['unique_episodes'], 1)
        self.assertEqual(result['viewing']['unique_seasons'], 1)
        self.assertEqual(result['viewing']['repeat_views'], 2)
        self.assertEqual(result['viewing']['estimated_watch_minutes'], 245)
        self.assertEqual(result['scores']['average'], 7.8)
        self.assertEqual([item['genre'] for item in result['genres']], ['Drama', 'Mystery'])
        self.assertEqual([item['month'] for item in result['activity']], ['2025-05', '2026-01'])

        year = (await self.client.get(f'/tracking/profile/{self.owner.username}/stats/summary', params={'year': 2026})).json()
        self.assertEqual(year['current']['total'], 2)
        self.assertEqual(year['viewing']['unique_titles'], 1)
        self.assertEqual(year['viewing']['estimated_watch_minutes'], 45)
        self.assertEqual(year['activity'], [{'month': '2026-01', 'movies': 0, 'episodes': 1}])

    async def test_private_profile_denied_even_to_an_admin_viewer(self):
        self.owner.is_admin=True
        res=await self.client.get(f'/tracking/profile/{self.friend.username}/movie')
        self.assertEqual(res.status_code,403)
        stats=await self.client.get(f'/tracking/profile/{self.friend.username}/stats/summary')
        self.assertEqual(stats.status_code,403)

    async def test_private_followed_scores_excluded(self):
        self.db.add(Follow(follower_id=self.owner.id,following_id=self.friend.id))
        self.db.add(TrackedEntry(user_id=self.friend.id,media_id=self.movie.id,status='completed',manual_score=9))
        await self.db.commit()
        res=await self.client.get(f'/tracking/title/{self.movie.id}')
        self.assertEqual(res.json()['friends'],[])
        self.assertIsNone(res.json()['friends_average'])

    async def test_cloud_first_import_requires_explicit_approval(self):
        from core.cloud_reconciliation import record_cloud_import, require_cloud_reconciliation

        baseline = await record_cloud_import(self.db, self.owner.id, 'trakt', {'ratings': 3})
        await self.db.commit()
        self.assertFalse(baseline.approved)
        reviews = (await self.db.execute(select(SyncReview).where(
            SyncReview.user_id == self.owner.id,
            SyncReview.kind == 'initial_cloud_import',
        ))).scalars().all()
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].provider, 'trakt')
        with self.assertRaises(Exception) as blocked:
            await require_cloud_reconciliation(self.db, self.owner.id, 'trakt')
        self.assertEqual(getattr(blocked.exception, 'status_code', None), 409)

        res = await self.client.post(
            f'/tracking/recent-events/{reviews[0].id}',
            json={'action': 'confirm'},
        )
        self.assertEqual(res.status_code, 200, res.text)
        approved = (await self.db.execute(select(CloudBaseline).where(
            CloudBaseline.user_id == self.owner.id,
            CloudBaseline.provider == 'trakt',
        ))).scalar_one()
        self.assertTrue(approved.approved)
        await require_cloud_reconciliation(self.db, self.owner.id, 'trakt')

        await record_cloud_import(self.db, self.owner.id, 'trakt', {'ratings': 4})
        await self.db.commit()
        reviews = (await self.db.execute(select(SyncReview).where(
            SyncReview.user_id == self.owner.id,
            SyncReview.kind == 'initial_cloud_import',
        ))).scalars().all()
        self.assertEqual(len(reviews), 1)

    async def test_ambiguous_cloud_rating_stays_local_until_resolved(self):
        from core.cloud_rating_reconciliation import reconcile_cloud_rating

        response = await self.save(self.movie, status='completed', manual_score=8.5)
        self.assertEqual(response.status_code, 200, response.text)
        rating = (await self.db.execute(select(Rating).where(
            Rating.user_id == self.owner.id,
            Rating.media_id == self.movie.id,
            Rating.season_number.is_(None),
        ))).scalar_one()
        existing = {(self.movie.id, None): rating}
        changed = {}

        echo = await reconcile_cloud_rating(
            self.db,
            provider='trakt',
            user_id=self.owner.id,
            media=self.movie,
            season_number=None,
            remote_score=9,
            remote_rated_at=datetime(2027, 1, 1),
            existing=existing,
            changed=changed,
        )
        self.assertEqual(echo, 'skipped')
        self.assertEqual(rating.rating, 8.5)

        outcome = await reconcile_cloud_rating(
            self.db,
            provider='trakt',
            user_id=self.owner.id,
            media=self.movie,
            season_number=None,
            remote_score=6,
            remote_rated_at=None,
            existing=existing,
            changed=changed,
        )
        await self.db.commit()
        self.assertEqual(outcome, 'conflict')
        self.assertEqual(rating.rating, 8.5)
        review = (await self.db.execute(select(SyncReview).where(
            SyncReview.user_id == self.owner.id,
            SyncReview.kind == 'rating_conflict',
        ))).scalar_one()
        self.assertEqual(review.previous_score, 8.5)
        self.assertEqual(review.proposed_score, 6)

        recent = await self.client.get('/tracking/recent-events')
        self.assertEqual(recent.status_code, 200, recent.text)
        event = next(item for item in recent.json()['results'] if item['id'] == review.id)
        self.assertEqual(event['previous_score'], 8.5)
        self.assertEqual(event['proposed_score'], 6)

        resolved = await self.client.post(
            f'/tracking/recent-events/{review.id}',
            json={'action': 'confirm'},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.owner.id,
            TrackedEntry.media_id == self.movie.id,
        ))).scalar_one()
        self.assertEqual(entry.manual_score, 6)
        await self.db.refresh(rating)
        self.assertEqual(rating.rating, 6)

    async def test_newer_cloud_rating_applies_but_calculated_show_requires_review(self):
        from core.cloud_rating_reconciliation import reconcile_cloud_rating

        movie_response = await self.save(self.movie, manual_score=8)
        self.assertEqual(movie_response.status_code, 200, movie_response.text)
        movie_rating = (await self.db.execute(select(Rating).where(
            Rating.user_id == self.owner.id,
            Rating.media_id == self.movie.id,
            Rating.season_number.is_(None),
        ))).scalar_one()
        movie_rating.rated_at = datetime(2026, 1, 1)
        changed = {}
        outcome = await reconcile_cloud_rating(
            self.db,
            provider='mdblist',
            user_id=self.owner.id,
            media=self.movie,
            season_number=None,
            remote_score=7,
            remote_rated_at=datetime(2027, 1, 1),
            existing={(self.movie.id, None): movie_rating},
            changed=changed,
        )
        self.assertEqual(outcome, 'applied')
        self.assertEqual(changed, {(self.movie.id, None): 7})
        movie_entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.owner.id,
            TrackedEntry.media_id == self.movie.id,
        ))).scalar_one()
        self.assertEqual(movie_entry.manual_score, 7)

        show_response = await self.save(
            self.show,
            rating_mode='average',
            season_scores={'1': 6, '2': 8},
        )
        self.assertEqual(show_response.status_code, 200, show_response.text)
        show_rating = (await self.db.execute(select(Rating).where(
            Rating.user_id == self.owner.id,
            Rating.media_id == self.show.id,
            Rating.season_number.is_(None),
        ))).scalar_one()
        show_rating.rated_at = datetime(2026, 1, 1)
        outcome = await reconcile_cloud_rating(
            self.db,
            provider='mdblist',
            user_id=self.owner.id,
            media=self.show,
            season_number=None,
            remote_score=9,
            remote_rated_at=datetime(2027, 1, 1),
            existing={(self.show.id, None): show_rating},
            changed={},
        )
        self.assertEqual(outcome, 'conflict')
        show_entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.owner.id,
            TrackedEntry.media_id == self.show.id,
        ))).scalar_one()
        self.assertEqual(show_entry.rating_mode, 'average')
        self.assertEqual(show_rating.rating, 7)
        review = (await self.db.execute(select(SyncReview).where(
            SyncReview.user_id == self.owner.id,
            SyncReview.media_id == self.show.id,
            SyncReview.kind == 'rating_conflict',
        ))).scalar_one()
        resolved = await self.client.post(
            f'/tracking/recent-events/{review.id}',
            json={'action': 'confirm'},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        await self.db.refresh(show_entry)
        self.assertEqual(show_entry.rating_mode, 'manual')
        self.assertEqual(show_entry.manual_score, 9)
        self.assertEqual(show_entry.season_scores, {'1': 6, '2': 8})

    async def test_scheduled_catalogue_refresh_targets_stale_tracked_series_once(self):
        from core.tracking_metadata import refresh_tracked_catalogues

        self.show.tmdb_id = 987654301
        self.show.tmdb_data = {
            'tracking_catalogue_refreshed_at': (
                datetime.now().astimezone() - timedelta(days=2)
            ).isoformat(),
        }
        canonical = Show(
            title='Fixture Show',
            tmdb_id=self.show.tmdb_id,
            canonical_source='tmdb',
            status='Returning Series',
        )
        self.db.add(canonical)
        await self.save(self.show, status='watching')
        # The same shared title tracked by another user must still refresh once.
        self.db.add(TrackedEntry(
            user_id=self.friend.id,
            media_id=self.show.id,
            status='planning',
            rating_mode='manual',
            season_scores={},
            progress=0,
            favorite=False,
            rewatch_count=0,
        ))
        await self.db.commit()

        with patch(
            'core.tracking_metadata.hydrate_tracking_episodes',
            AsyncMock(return_value=12),
        ) as hydrate:
            stats = await refresh_tracked_catalogues(self.db, 'fixture-key')

        hydrate.assert_awaited_once_with(self.db, self.show, 'fixture-key')
        self.assertEqual(stats, {
            'refreshed': 1,
            'episodes': 12,
            'skipped': 0,
            'failed': 0,
        })

    async def test_scheduled_catalogue_refresh_includes_tvdb_native_series(self):
        from core.tracking_metadata import refresh_tracked_catalogues

        self.show.tvdb_id = 7654301
        canonical = Show(
            title='Fixture Show', tvdb_id=self.show.tvdb_id,
            canonical_source='tvdb', status='Continuing',
        )
        self.db.add(canonical)
        await self.save(self.show, status='watching')
        await self.db.commit()

        with patch(
            'core.tracking_metadata.hydrate_tracking_episodes',
            AsyncMock(return_value=8),
        ) as hydrate:
            stats = await refresh_tracked_catalogues(
                self.db, None, tvdb_api_key='tvdb-key',
            )

        hydrate.assert_awaited_once_with(self.db, self.show, None, 'tvdb-key')
        self.assertEqual(stats, {
            'refreshed': 1,
            'episodes': 8,
            'skipped': 0,
            'failed': 0,
        })

    async def test_newer_cloud_watch_completion_updates_existing_entry(self):
        from core.cloud_history_reconciliation import reconcile_cloud_watch_events

        await self.save(self.movie, status='planning')
        self.db.add(CloudBaseline(
            user_id=self.owner.id,
            provider='trakt',
            approved=True,
            snapshot={},
        ))
        watched_at = datetime.now() + timedelta(days=1)
        self.db.add(WatchEvent(
            user_id=self.owner.id,
            media_id=self.movie.id,
            completed=True,
            watched_at=watched_at,
        ))
        await self.db.commit()

        applied_media_ids = set()
        stats = await reconcile_cloud_watch_events(
            self.db,
            user_id=self.owner.id,
            provider='trakt',
            new_media_ids={self.movie.id},
            applied_media_ids=applied_media_ids,
        )

        entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.owner.id,
            TrackedEntry.media_id == self.movie.id,
        ))).scalar_one()
        self.assertEqual(stats['applied'], 1)
        self.assertEqual(applied_media_ids, {self.movie.id})
        self.assertEqual(entry.status, 'completed')
        self.assertEqual(entry.finish_date, watched_at.date())
        notification = (await self.db.execute(select(SyncReview).where(
            SyncReview.user_id == self.owner.id,
            SyncReview.provider == 'trakt',
            SyncReview.kind == 'cloud_update',
        ))).scalar_one()
        self.assertEqual(notification.state, 'confirmed')
        self.assertEqual(notification.priority, 'low')
        self.assertIn({
            'field': 'finish_date',
            'previous': None,
            'proposed': watched_at.date().isoformat(),
        }, notification.payload['changes'])
        prompt = (await self.db.execute(select(SyncReview).where(
            SyncReview.user_id == self.owner.id,
            SyncReview.kind == 'rating_needed',
        ))).scalar_one()
        self.assertEqual(prompt.state, 'confirmed')
        self.assertEqual(prompt.payload['push_state'], 'pending')
        activity = (await self.db.execute(select(TrackingActivity).where(
            TrackingActivity.user_id == self.owner.id,
            TrackingActivity.media_id == self.movie.id,
        ))).scalars().all()
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity[0].status, 'completed')
        self.assertTrue(activity[0].payload['status_changed'])

    async def test_unordered_cloud_watch_preserves_local_status_and_creates_conflict(self):
        from core.cloud_history_reconciliation import reconcile_cloud_watch_events
        from core.cloud_reconciliation import cloud_push_is_approved

        await self.save(self.movie, status='dropped')
        self.db.add(CloudBaseline(
            user_id=self.owner.id,
            provider='simkl',
            approved=True,
            snapshot={},
        ))
        self.db.add(WatchEvent(
            user_id=self.owner.id,
            media_id=self.movie.id,
            completed=True,
            watched_at=None,
        ))
        await self.db.commit()

        applied_media_ids = set()
        stats = await reconcile_cloud_watch_events(
            self.db,
            user_id=self.owner.id,
            provider='simkl',
            new_media_ids={self.movie.id},
            applied_media_ids=applied_media_ids,
        )

        entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.owner.id,
            TrackedEntry.media_id == self.movie.id,
        ))).scalar_one()
        review = (await self.db.execute(select(SyncReview).where(
            SyncReview.user_id == self.owner.id,
            SyncReview.provider == 'simkl',
            SyncReview.kind == 'cloud_conflict',
        ))).scalar_one()
        self.assertEqual(stats['conflicts'], 1)
        self.assertEqual(applied_media_ids, set())
        self.assertEqual(entry.status, 'dropped')
        self.assertEqual(review.previous_status, 'dropped')
        self.assertEqual(review.proposed_status, 'completed')
        self.assertFalse(await cloud_push_is_approved(self.db, self.owner.id, 'simkl'))

    async def test_first_cloud_import_preserves_ambiguous_existing_history(self):
        from core.cloud_history_reconciliation import reconcile_cloud_watch_events

        await self.save(self.movie, status='dropped', progress=1)
        self.db.add(WatchEvent(
            user_id=self.owner.id,
            media_id=self.movie.id,
            completed=True,
            watched_at=None,
        ))
        await self.db.commit()

        stats = await reconcile_cloud_watch_events(
            self.db,
            user_id=self.owner.id,
            provider='trakt',
            new_media_ids={self.movie.id},
        )

        entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.owner.id,
            TrackedEntry.media_id == self.movie.id,
        ))).scalar_one()
        review = (await self.db.execute(select(SyncReview).where(
            SyncReview.user_id == self.owner.id,
            SyncReview.provider == 'trakt',
            SyncReview.kind == 'cloud_conflict',
        ))).scalar_one()
        self.assertEqual(stats['conflicts'], 1)
        self.assertEqual(entry.status, 'dropped')
        self.assertEqual(entry.progress, 1)
        self.assertIn({'field': 'status', 'previous': 'dropped', 'proposed': 'completed'}, review.payload['changes'])

    async def test_cloud_history_resolution_applies_reviewed_fields_together(self):
        await self.save(self.movie, status='paused', progress=1)
        review = SyncReview(
            user_id=self.owner.id,
            media_id=self.movie.id,
            provider='simkl',
            kind='cloud_conflict',
            previous_status='paused',
            proposed_status='completed',
            message='Fixture field conflict',
            payload={'changes': [
                {'field': 'status', 'previous': 'paused', 'proposed': 'completed'},
                {'field': 'finish_date', 'previous': None, 'proposed': '2026-09-18'},
                {'field': 'progress', 'previous': 1, 'proposed': 8},
            ]},
        )
        self.db.add(review)
        await self.db.commit()

        response = await self.client.post(
            f'/tracking/recent-events/{review.id}',
            json={'action': 'confirm'},
        )

        self.assertEqual(response.status_code, 200, response.text)
        await self.db.refresh(review)
        entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.owner.id,
            TrackedEntry.media_id == self.movie.id,
        ))).scalar_one()
        self.assertEqual(review.state, 'confirmed')
        self.assertEqual(entry.status, 'completed')
        self.assertEqual(entry.finish_date, date(2026, 9, 18))
        self.assertEqual(entry.progress, 8)

    async def test_title_omits_instance_community_average(self):
        await self.save(self.movie, status='completed', manual_score=8)
        self.db.add(TrackedEntry(
            user_id=self.friend.id,
            media_id=self.movie.id,
            status='completed',
            manual_score=10,
        ))
        await self.db.commit()

        res = await self.client.get(f'/tracking/title/{self.movie.id}')
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn('community_average', res.json())
        self.assertNotIn('community_count', res.json())

        friend_profile = (await self.db.execute(
            select(UserProfileData).where(UserProfileData.user_id == self.friend.id)
        )).scalar_one()
        friend_profile.privacy_level = PrivacyLevel.public
        await self.db.commit()
        res = await self.client.get(f'/tracking/title/{self.movie.id}')
        self.assertNotIn('community_average', res.json())

    async def test_release_preferences_default_and_patch(self):
        initial=await self.client.get('/tracking/preferences')
        self.assertEqual(initial.status_code,200,initial.text)
        self.assertEqual(initial.json(),{
            'auto_confirm':False,'combine_lists':True,
            'default_sort':'title',
            'low_priority_notifications':True,'low_priority_retention_days':7,
        })
        changed=await self.client.patch('/tracking/preferences',json={
            'combine_lists':False,'default_sort':'updated','low_priority_notifications':False,'low_priority_retention_days':14,
        })
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertFalse(changed.json()['combine_lists'])
        self.assertEqual(changed.json()['default_sort'],'updated')
        self.assertEqual(changed.json()['low_priority_retention_days'],14)
        invalid=await self.client.patch('/tracking/preferences',json={'default_sort':'random'})
        self.assertEqual(invalid.status_code,422,invalid.text)

    async def test_resolved_low_priority_notification_disappears_after_seen(self):
        review=SyncReview(user_id=self.owner.id,media_id=self.movie.id,kind='playback_removed',state='confirmed',message='Applied automatically')
        self.db.add(review);await self.db.commit()
        recent=await self.client.get('/tracking/recent-events')
        self.assertEqual([row['id'] for row in recent.json()['results']],[review.id])
        seen=await self.client.post('/tracking/recent-events/seen',json={'ids':[review.id]})
        self.assertEqual(seen.status_code,200,seen.text)
        recent=await self.client.get('/tracking/recent-events')
        self.assertEqual(recent.json()['results'],[])

    async def test_repeated_connection_failure_has_one_alert_until_success(self):
        connection=MediaServerConnection(user_id=self.owner.id,type='jellyfin',name='Living Room',url='https://example.test',token='fixture')
        self.db.add(connection);await self.db.flush()
        self.db.add(SyncJob(user_id=self.owner.id,source=CollectionSource.jellyfin,
            connection_id=connection.id,status=SyncStatus.failed,error_message='Timed out'))
        await self.db.commit()
        first=(await self.client.get('/tracking/recent-events')).json()
        self.assertEqual(first['pending'],0)
        self.assertEqual(first['results'],[])

        self.db.add(SyncJob(user_id=self.owner.id,source=CollectionSource.jellyfin,
            connection_id=connection.id,status=SyncStatus.failed,error_message='Timed out again'))
        await self.db.commit()
        repeated=(await self.client.get('/tracking/recent-events')).json()
        self.assertEqual(repeated['pending'],1)
        self.assertEqual(len(repeated['results']),1)
        self.assertEqual(repeated['results'][0]['kind'],'connection_failure')
        self.assertEqual(repeated['results'][0]['payload']['title'],'Living Room')
        self.assertEqual(repeated['results'][0]['payload']['reason'],'repeated_failure')
        self.assertEqual(repeated['results'][0]['payload']['resolve_url'],f'/connections#conn-body-{connection.id}')

        self.db.add(SyncJob(user_id=self.owner.id,source=CollectionSource.jellyfin,
            connection_id=connection.id,status=SyncStatus.completed))
        await self.db.commit()
        recovered=(await self.client.get('/tracking/recent-events')).json()
        self.assertEqual(recovered['pending'],0)
        self.assertEqual(recovered['results'],[])

    async def test_authentication_failure_alerts_immediately_for_cloud_provider(self):
        self.db.add(SyncJob(user_id=self.owner.id,source=CollectionSource.trakt,
            status=SyncStatus.failed,error_message='401 Unauthorized'))
        await self.db.commit()
        payload=(await self.client.get('/tracking/recent-events')).json()
        self.assertEqual(payload['pending'],1)
        self.assertEqual(len(payload['results']),1)
        alert=payload['results'][0]
        self.assertEqual(alert['provider'],'trakt')
        self.assertEqual(alert['payload']['reason'],'authorization')
        self.assertEqual(alert['payload']['resolve_url'],'/connections#trakt-body')

    async def test_local_delivery_state_stays_out_of_provider_review_inbox(self):
        self.db.add(TrackingDeletion(user_id=self.owner.id,media_id=self.movie.id,pending_connections=['trakt']))
        review=SyncReview(
            user_id=self.owner.id,
            media_id=self.movie.id,
            kind='outbound_pending',
            state='pending',
            message='Local tracking data was deleted.',
        )
        self.db.add(review);await self.db.commit()

        recent=await self.client.get('/tracking/recent-events')
        self.assertEqual(recent.status_code,200,recent.text)
        payload=recent.json()
        self.assertEqual(payload['pending'],0)
        self.assertEqual(payload['results'],[])
        self.assertEqual(len(payload['outbound']),1)
        self.assertEqual(payload['outbound'][0]['title'],self.movie.title)
        self.assertEqual(payload['outbound'][0]['deliveries'][0]['connection'],'Trakt')

    async def test_connection_delivery_groups_by_title_until_every_service_finishes(self):
        stremio=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Stremio',url='https://example.test',token='fixture')
        nuvio=MediaServerConnection(user_id=self.owner.id,type='nuvio',name='Nuvio',url='https://example.test',token='fixture')
        self.db.add_all([stremio,nuvio]);await self.db.flush()
        marker=TrackingDeletion(user_id=self.owner.id,media_id=self.movie.id,
            pending_connections=[f'connection:{stremio.id}',f'connection:{nuvio.id}','trakt'])
        review=SyncReview(user_id=self.owner.id,media_id=self.movie.id,kind='outbound_pending',state='pending',message='Pending resets')
        stream_a=StreamAction(user_id=self.owner.id,connection_id=stremio.id,media_id=self.movie.id,action='reset',payload={},state='pending')
        stream_b=StreamAction(user_id=self.owner.id,connection_id=nuvio.id,media_id=self.movie.id,action='reset',payload={},state='pending')
        cloud=CloudAction(user_id=self.owner.id,provider='trakt',media_id=self.movie.id,action='reset',payload={},state='pending')
        self.db.add_all([marker,review,stream_a,stream_b,cloud]);await self.db.commit()

        initial=(await self.client.get('/tracking/recent-events')).json()['outbound']
        self.assertEqual(len(initial),1)
        self.assertEqual({item['connection'] for item in initial[0]['deliveries']},{'Stremio','Nuvio','Trakt'})

        stream_a.state='applied';stream_b.last_error='TimeoutError'
        marker.pending_connections=[f'connection:{nuvio.id}','trakt']
        await self.db.commit()
        partial=(await self.client.get('/tracking/recent-events')).json()['outbound']
        self.assertEqual(len(partial),1)
        self.assertEqual({item['connection'] for item in partial[0]['deliveries']},{'Nuvio','Trakt'})
        self.assertEqual(next(item for item in partial[0]['deliveries'] if item['connection']=='Nuvio')['error'],'TimeoutError')

        cloud.state='applied';stream_b.attempts=2;stream_b.last_error=None
        marker.pending_connections=[f'connection:{nuvio.id}']
        await self.db.commit()
        retry=(await self.client.get('/tracking/recent-events')).json()['outbound']
        self.assertEqual(len(retry),1)
        self.assertEqual([item['connection'] for item in retry[0]['deliveries']],['Nuvio'])

        stream_b.state='applied';marker.pending_connections=[];review.state='confirmed'
        await self.db.commit()
        self.assertEqual((await self.client.get('/tracking/recent-events')).json()['outbound'],[])

    async def test_library_action_saves_without_a_connection_and_survives_refresh(self):
        response=await self.client.put(f'/tracking/library/{self.movie.id}',json={'in_library':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['desired'])
        self.assertFalse(response.json()['pending'])
        self.assertTrue((await self.client.get(f'/tracking/library/{self.movie.id}')).json()['desired'])
        self.assertEqual([row['id'] for row in (await self.client.get('/tracking/library')).json()['results']],
                         [self.movie.id])
        self.assertIsNotNone((await self.db.execute(select(StreamingLibraryIntent).where(
            StreamingLibraryIntent.user_id==self.owner.id,
            StreamingLibraryIntent.media_id==self.movie.id))).scalar_one_or_none())
        removed=await self.client.put(f'/tracking/library/{self.movie.id}',json={'in_library':False})
        self.assertEqual(removed.status_code,200,removed.text)
        self.assertEqual((await self.client.get('/tracking/library')).json()['results'],[])

    async def test_library_action_waits_for_first_connection_review(self):
        from core import stremio
        self.movie.tmdb_data={'external_ids':{'imdb_id':'tt1234567'}}
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Stremio',
            url='https://example.test',token='fixture',push_collection=True)
        self.db.add(conn);await self.db.commit()
        with patch.object(stremio,'datastore_put',AsyncMock()) as write:
            response=await self.client.put(f'/tracking/library/{self.movie.id}',json={'in_library':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['pending'])
        state=(await self.client.get(f'/tracking/library/{self.movie.id}')).json()
        self.assertIn('Run a full import',state['connections'][0]['error'])
        write.assert_not_awaited()
        self.assertEqual((await self.client.get('/tracking/recent-events')).json()['outbound'][0]['title'],self.movie.title)

    async def test_later_connection_receives_saved_library_choice_after_review(self):
        from core.streaming_library import retry_pending_library_deliveries
        from core import stremio

        self.movie.tmdb_data={'external_ids':{'imdb_id':'tt1234567'}}
        saved=await self.client.put(f'/tracking/library/{self.movie.id}',json={'in_library':True})
        self.assertEqual(saved.status_code,200,saved.text)
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Later Stremio',
            url='https://example.test',token='fixture',push_collection=True)
        self.db.add(conn);await self.db.flush()
        self.db.add(StreamBaseline(connection_id=conn.id,user_id=self.owner.id,snapshot={},approved=True))
        await self.db.commit()
        with (patch.object(stremio,'datastore_get',AsyncMock(return_value=[])),
              patch.object(stremio,'datastore_put',AsyncMock()) as write):
            await retry_pending_library_deliveries(self.db,self.owner.id,conn.id)
        write.assert_awaited_once()
        self.assertEqual((await self.client.get(f'/tracking/library/{self.movie.id}')).json()['connections'][0]['state'],'applied')

    async def test_library_action_tracks_partial_delivery_and_prevents_readding_removed_title(self):
        from routers.sync import _build_nuvio_library_items
        from core import stremio, nuvio

        self.movie.tmdb_data={'external_ids':{'imdb_id':'tt1234567'}}
        stremio_conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Stremio',
            url='https://example.test',token='fixture',push_collection=True)
        nuvio_conn=MediaServerConnection(user_id=self.owner.id,type='nuvio',name='Nuvio',
            url='https://example.test',token='fixture',server_user_id='1',push_collection=True)
        self.db.add_all([stremio_conn,nuvio_conn]);await self.db.flush()
        self.db.add_all([
            StreamBaseline(connection_id=stremio_conn.id,user_id=self.owner.id,snapshot={},approved=True),
            StreamBaseline(connection_id=nuvio_conn.id,user_id=self.owner.id,snapshot={},approved=True),
        ]);await self.db.commit()

        remote={'_id':'tt1234567','name':self.movie.title,'type':'movie','removed':False,
            'temp':False,'_ctime':'2026-01-01T00:00:00Z','_mtime':'2026-01-01T00:00:00Z',
            'state':{'timesWatched':1,'custom':'preserve'}}
        with (patch.object(stremio,'datastore_get',AsyncMock(return_value=[])),
              patch.object(stremio,'datastore_put',AsyncMock()) as stremio_put,
              patch.object(nuvio,'merge_library',AsyncMock(side_effect=RuntimeError('provider unavailable')))):
            added=await self.client.put(f'/tracking/library/{self.movie.id}',json={'in_library':True})
        self.assertEqual(added.status_code,200,added.text)
        self.assertTrue(added.json()['desired'])
        self.assertTrue(added.json()['pending'])
        state=(await self.client.get(f'/tracking/library/{self.movie.id}')).json()
        self.assertEqual({row['name']:row['state'] for row in state['connections']},
            {'Stremio':'applied','Nuvio':'pending'})
        self.assertEqual(next(row for row in state['connections'] if row['name']=='Nuvio')['error'],'RuntimeError')
        self.assertFalse(stremio_put.await_args.args[1][0]['removed'])
        grouped=(await self.client.get('/tracking/recent-events')).json()['outbound']
        self.assertEqual(len(grouped),1)
        self.assertEqual([row['connection'] for row in grouped[0]['deliveries']],['Nuvio · Library'])

        with patch.object(nuvio,'merge_library',AsyncMock()) as nuvio_merge:
            retried=await self.client.post(f'/tracking/library/{self.movie.id}/retry')
        self.assertEqual(retried.status_code,200,retried.text)
        self.assertFalse(retried.json()['pending'])
        self.assertEqual({row['state'] for row in retried.json()['connections']},{'applied'})
        self.assertEqual(nuvio_merge.await_args.kwargs['additions'][0]['content_id'],'tt1234567')
        self.assertEqual((await self.client.get('/tracking/recent-events')).json()['outbound'],[])
        collection=(await self.db.execute(select(Collection).where(Collection.user_id==self.owner.id,
            Collection.media_id==self.movie.id))).scalar_one()
        self.db.add(CollectionFile(collection_id=collection.id,source=CollectionSource.plex,
            source_id='plex-fixture'))
        await self.db.commit()

        with (patch.object(stremio,'datastore_get',AsyncMock(return_value=[remote])),
              patch.object(stremio,'datastore_put',AsyncMock()) as stremio_remove,
              patch.object(nuvio,'merge_library',AsyncMock()) as nuvio_remove):
            removed=await self.client.put(f'/tracking/library/{self.movie.id}',json={'in_library':False})
        self.assertEqual(removed.status_code,200,removed.text)
        self.assertFalse(removed.json()['desired'])
        self.assertFalse((await self.client.get(f'/tracking/library/{self.movie.id}')).json()['pending'])
        self.assertTrue(stremio_remove.await_args.args[1][0]['removed'])
        self.assertEqual(stremio_remove.await_args.args[1][0]['state']['custom'],'preserve')
        self.assertEqual(nuvio_remove.await_args.kwargs['removed_content_ids'],{'tt1234567'})
        self.assertEqual([item['content_id'] for item in await _build_nuvio_library_items(
            self.db,self.owner.id)],[])
        sources=(await self.db.execute(select(CollectionFile.source).join(Collection,
            Collection.id==CollectionFile.collection_id).where(Collection.user_id==self.owner.id,
            Collection.media_id==self.movie.id))).scalars().all()
        self.assertEqual(sources,[CollectionSource.plex])
        self.assertIsNotNone((await self.db.execute(select(StreamingLibraryDelivery).where(
            StreamingLibraryDelivery.connection_id==nuvio_conn.id))).scalar_one_or_none())

    async def test_unmatched_provider_item_can_be_matched_or_ignored(self):
        from core.provider_matching import record_unmatched_import, provider_override
        from core.cloud_reconciliation import require_cloud_reconciliation

        entry={'movie':{'title':'Provider Film','ids':{'imdb':'tt1234567'}}}
        self.db.add(CloudBaseline(user_id=self.owner.id,provider='mdblist',approved=True,snapshot={}))
        self.assertTrue(await record_unmatched_import(
            self.db,user_id=self.owner.id,provider='mdblist',kind='movies',entry=entry
        ))
        await self.db.commit()
        review=(await self.db.execute(select(SyncReview).where(
            SyncReview.user_id==self.owner.id,SyncReview.kind=='unmatched_import'
        ))).scalar_one()
        with self.assertRaises(Exception):
            await require_cloud_reconciliation(self.db,self.owner.id,'mdblist')
        matched=await self.client.post(f'/tracking/recent-events/{review.id}',json={
            'action':'match','media_id':self.movie.id,
        })
        self.assertEqual(matched.status_code,200,matched.text)
        media,ignored,external_key,_=await provider_override(
            self.db,user_id=self.owner.id,provider='mdblist',kind='movies',entry=entry
        )
        self.assertEqual(media.id,self.movie.id);self.assertFalse(ignored)
        self.assertTrue(external_key.startswith('movies:imdb:'))
        await require_cloud_reconciliation(self.db,self.owner.id,'mdblist')

        second={'show':{'title':'Ignore Me','ids':{'simkl':42}}}
        await record_unmatched_import(self.db,user_id=self.owner.id,provider='simkl',kind='shows',entry=second)
        await self.db.commit()
        review=(await self.db.execute(select(SyncReview).where(
            SyncReview.user_id==self.owner.id,SyncReview.provider=='simkl',SyncReview.kind=='unmatched_import'
        ))).scalar_one()
        ignored_response=await self.client.post(f'/tracking/recent-events/{review.id}',json={'action':'ignore'})
        self.assertEqual(ignored_response.status_code,200,ignored_response.text)
        self.assertIsNotNone((await self.db.execute(select(ProviderIgnore).where(
            ProviderIgnore.user_id==self.owner.id,ProviderIgnore.provider=='simkl'
        ))).scalar_one_or_none())

    async def test_combined_list_and_anime_visibility_are_presentation_only(self):
        anime=Media(title='Fixture Anime',media_type=MediaType.series,tmdb_data={
            'genres':['Animation'],'original_language':'ja','origin_country':['JP'],
        })
        self.db.add(anime);await self.db.flush()
        self.db.add_all([
            TrackedEntry(user_id=self.owner.id,media_id=self.movie.id,status='completed'),
            TrackedEntry(user_id=self.owner.id,media_id=self.show.id,status='watching'),
            TrackedEntry(user_id=self.owner.id,media_id=anime.id,status='watching'),
        ]);await self.db.commit()
        combined=await self.client.get(f'/tracking/profile/{self.owner.username}/all')
        self.assertEqual({row['title'] for row in combined.json()['entries']},{'Fixture Film','Fixture Show'})
        self.assertTrue(combined.json()['combine_lists'])
        self.assertIsNotNone(await self.db.get(TrackedEntry,(await self.db.execute(select(TrackedEntry.id).where(TrackedEntry.media_id==anime.id))).scalar_one()))
        settings=await self.db.get(GlobalSettings,1)
        if settings is None:
            settings=GlobalSettings(id=1)
            self.db.add(settings)
        settings.show_anime=True;await self.db.commit()
        combined=await self.client.get(f'/tracking/profile/{self.owner.username}/all')
        self.assertIn('Fixture Anime',{row['title'] for row in combined.json()['entries']})

    async def test_public_people_search_and_one_way_follow(self):
        profile=(await self.db.execute(select(UserProfileData).where(UserProfileData.user_id==self.friend.id))).scalar_one()
        profile.privacy_level=PrivacyLevel.public;profile.display_name='Public Friend';await self.db.commit()
        search=await self.client.get('/tracking/people-search?q=Public')
        self.assertEqual(search.status_code,200,search.text)
        self.assertEqual(search.json()['results'][0]['username'],self.friend.username)
        follow=await self.client.post(f'/tracking/people/{self.friend.username}/follow')
        self.assertEqual(follow.status_code,200,follow.text)
        person=await self.client.get(f'/tracking/people/{self.friend.username}')
        self.assertTrue(person.json()['following'])
        unfollow=await self.client.delete(f'/tracking/people/{self.friend.username}/follow')
        self.assertEqual(unfollow.status_code,200,unfollow.text)

    async def test_stream_library_removal_is_scoped_to_the_observed_connection(self):
        from routers.sync import _remove_stream_collection_sources

        nuvio_connection = MediaServerConnection(
            user_id=self.owner.id,
            type='nuvio',
            name='Nuvio source',
            url='https://nuvio.invalid',
            token='nuvio-test-token',
            server_user_id='1',
        )
        stremio_connection = MediaServerConnection(
            user_id=self.owner.id,
            type='stremio',
            name='Stremio source',
            url='https://api.strem.io',
            token='stremio-test-token',
        )
        self.db.add_all([nuvio_connection, stremio_connection])
        await self.db.flush()
        collection = Collection(user_id=self.owner.id, media_id=self.movie.id)
        self.db.add(collection)
        await self.db.flush()
        self.db.add_all([
            CollectionFile(
                collection_id=collection.id,
                connection_id=nuvio_connection.id,
                source=CollectionSource.nuvio,
                source_id='1:tt-fixture',
            ),
            CollectionFile(
                collection_id=collection.id,
                connection_id=stremio_connection.id,
                source=CollectionSource.stremio,
                source_id=f'{stremio_connection.id}:tt-fixture',
            ),
        ])
        await self.db.commit()

        removed = await _remove_stream_collection_sources(
            self.db,
            self.owner.id,
            nuvio_connection.id,
            source=CollectionSource.nuvio,
            removed_ids=set(),
            complete_snapshot_source_ids=set(),
        )
        await self.db.flush()
        self.assertEqual(removed, set())
        self.assertIsNotNone(await self.db.get(Collection, collection.id))
        files = (await self.db.execute(
            select(CollectionFile).where(CollectionFile.collection_id == collection.id)
        )).scalars().all()
        self.assertEqual([row.source for row in files], [CollectionSource.stremio])

        removed = await _remove_stream_collection_sources(
            self.db,
            self.owner.id,
            stremio_connection.id,
            source=CollectionSource.stremio,
            removed_ids=set(),
            complete_snapshot_source_ids=set(),
        )
        await self.db.flush()
        self.assertEqual(removed, {self.movie.id})
        self.assertIsNone(await self.db.get(Collection, collection.id))

    async def test_stream_library_fanout_requires_approved_source_and_target(self):
        from routers.sync import _fan_out_streaming_library_changes

        source = MediaServerConnection(
            user_id=self.owner.id,
            type='nuvio',
            name='Nuvio source',
            url='https://nuvio.invalid',
            token='source-token',
            server_user_id='1',
        )
        target = MediaServerConnection(
            user_id=self.owner.id,
            type='stremio',
            name='Stremio target',
            url='https://api.strem.io',
            token='target-token',
            push_collection=True,
        )
        self.db.add_all([source, target])
        await self.db.flush()
        source_baseline = StreamBaseline(
            connection_id=source.id,
            user_id=self.owner.id,
            approved=False,
            snapshot={},
        )
        target_baseline = StreamBaseline(
            connection_id=target.id,
            user_id=self.owner.id,
            approved=True,
            snapshot={},
        )
        self.db.add_all([source_baseline, target_baseline])
        await self.db.commit()

        with patch('routers.sync._push_stremio_connection', AsyncMock()) as push:
            await _fan_out_streaming_library_changes(
                self.db,
                self.owner.id,
                source.id,
                new_collected_ids={self.movie.id},
                removed_collected_ids=set(),
                api_key=None,
            )
            push.assert_not_awaited()

            source_baseline.approved = True
            target_baseline.approved = False
            await self.db.commit()
            await _fan_out_streaming_library_changes(
                self.db,
                self.owner.id,
                source.id,
                new_collected_ids={self.movie.id},
                removed_collected_ids=set(),
                api_key=None,
            )
            push.assert_not_awaited()

            target_baseline.approved = True
            await self.db.commit()
            await _fan_out_streaming_library_changes(
                self.db,
                self.owner.id,
                source.id,
                new_collected_ids={self.movie.id},
                removed_collected_ids=set(),
                api_key=None,
            )
            push.assert_awaited_once()

    async def test_title_exposes_available_catalogue_details(self):
        self.movie.original_title = 'Original Fixture Film'
        self.movie.runtime = 125
        self.movie.tmdb_rating = 7.25
        self.movie.tagline = 'A fixture worth tracking.'
        self.movie.imdb_id = 'tt1234567'
        self.movie.tmdb_data = {
            'original_language': 'en',
            'production_companies': [{'id': 7, 'name': 'Fixture Studio'}],
        }
        await self.db.commit()

        res = await self.client.get(f'/tracking/title/{self.movie.id}')
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual(payload['original_title'], 'Original Fixture Film')
        self.assertEqual(payload['runtime'], 125)
        self.assertEqual(payload['tmdb_score'], 7.25)
        self.assertEqual(payload['tagline'], 'A fixture worth tracking.')
        self.assertEqual(payload['original_language'], 'en')
        self.assertEqual(payload['studios'][0]['name'], 'Fixture Studio')
        self.assertEqual(payload['imdb_id'], 'tt1234567')

    async def test_anonymous_requires_instance_switch(self):
        self.viewer=None
        res=await self.client.get('/tracking/catalog')
        self.assertEqual(res.status_code,401)

    async def test_delete_clears_tracking_but_preserves_library_and_catalogue(self):
        await self.save(self.movie,status='completed',manual_score=8,notes='delete this')
        self.db.add(Collection(user_id=self.owner.id,media_id=self.movie.id))
        self.db.add(WatchEvent(user_id=self.owner.id,media_id=self.movie.id,completed=True))
        await self.db.commit()
        denied=await self.client.delete(f'/tracking/entry/{self.movie.id}')
        self.assertEqual(denied.status_code,409)
        res=await self.client.delete(f'/tracking/entry/{self.movie.id}?confirmed=true')
        self.assertEqual(res.status_code,200,res.text)
        self.assertTrue(res.json()['library_preserved'])
        for model in (TrackedEntry,Rating,WatchEvent):
            self.assertEqual((await self.db.execute(select(model).where(model.user_id==self.owner.id,model.media_id==self.movie.id))).scalars().all(),[])
        self.assertIsNotNone((await self.db.execute(select(Collection).where(Collection.user_id==self.owner.id,Collection.media_id==self.movie.id))).scalar_one_or_none())
        self.assertIsNotNone((await self.db.execute(select(TrackingDeletion).where(TrackingDeletion.user_id==self.owner.id,TrackingDeletion.media_id==self.movie.id))).scalar_one_or_none())
        self.assertIsNotNone(await self.db.get(Media,self.movie.id))

    async def test_history_import_ignores_library_only_items_and_deleted_items(self):
        self.db.add(Collection(user_id=self.owner.id,media_id=self.movie.id))
        await self.db.commit()
        res=await self.client.post('/tracking/import-history')
        self.assertEqual(res.json()['added'],0)
        resurrected=(await self.db.execute(select(WatchEvent.id).where(WatchEvent.user_id==self.owner.id,WatchEvent.media_id==self.movie.id))).scalars().all()
        self.assertEqual(resurrected,[])
        self.db.add(WatchEvent(user_id=self.owner.id,media_id=self.movie.id,completed=True,watched_at=None))
        await self.db.commit()
        res=await self.client.post('/tracking/import-history')
        self.assertEqual(res.json()['added'],1)
        entry=(await self.db.execute(select(TrackedEntry).where(TrackedEntry.user_id==self.owner.id))).scalar_one()
        self.assertEqual(entry.status,'completed')
        self.assertIsNone(entry.start_date);self.assertIsNone(entry.finish_date)
        await self.client.delete(f'/tracking/entry/{self.movie.id}?confirmed=true')
        self.db.add(WatchEvent(user_id=self.owner.id,media_id=self.movie.id,completed=True))
        await self.db.commit()
        res=await self.client.post('/tracking/import-history')
        self.assertEqual(res.json()['added'],0)

    async def test_season_progress_is_cumulative_and_rollback_preserves_completed(self):
        self.show.tmdb_id=987654321
        self.show.tmdb_data={'tracking_catalogue_refreshed_at':datetime.now().isoformat()}
        series=Show(title='Fixture Show',tmdb_id=self.show.tmdb_id)
        self.db.add(series);await self.db.flush()
        episodes=[]
        for season,number,released in [(0,1,'2020-01-01'),(1,1,'2020-01-01'),(1,2,'2020-01-02'),(2,1,'2021-01-01'),(2,2,'2999-01-01')]:
            episode=Media(title=f'{season}/{number}',media_type=MediaType.episode,show_id=series.id,season_number=season,episode_number=number,release_date=released)
            self.db.add(episode);episodes.append(episode)
        await self.db.commit()
        url=f'/tracking/entry/{self.show.id}/season/'
        res=await self.client.patch(url+'2',json={'watched':True})
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(res.json()['progress'],3)
        self.assertEqual(res.json()['status'],'completed')
        ids=set((await self.db.execute(select(WatchEvent.media_id).where(WatchEvent.user_id==self.owner.id))).scalars())
        self.assertEqual(ids,{e.id for e in episodes[1:4]})
        res=await self.client.patch(url+'1',json={'watched':False})
        self.assertEqual(res.status_code,409,res.text)
        res=await self.client.patch(url+'1',json={'watched':False,'confirm_rollback':True})
        self.assertEqual(res.status_code,200,res.text)
        self.local_rollback.assert_awaited_once_with(
            self.owner.id, {e.id for e in episodes[1:4]},
        )
        self.assertEqual(res.json()['progress'],0)
        self.assertEqual(res.json()['status'],'completed')
        ids=(await self.db.execute(select(WatchEvent.id).where(WatchEvent.user_id==self.owner.id))).scalars().all()
        self.assertEqual(ids,[])

    async def test_movie_progress_works_without_release_metadata_and_stops_at_one(self):
        self.movie.release_date=None
        await self.db.commit()
        added=await self.save(self.movie,status='paused')
        self.assertEqual(added.status_code,200,added.text)
        initial=(await self.client.get(f'/tracking/profile/{self.owner.username}/movie')).json()['entries'][0]
        self.assertEqual(initial['progress'],0)
        watched=await self.save(self.movie,progress=1)
        self.assertEqual(watched.status_code,200,watched.text)
        self.assertEqual(watched.json()['status'],'completed')
        result=(await self.client.get(f'/tracking/profile/{self.owner.username}/movie')).json()['entries'][0]
        self.assertEqual(result['progress'],1)
        self.assertEqual((await self.db.execute(select(WatchEvent.media_id).where(
            WatchEvent.user_id==self.owner.id))).scalars().all(),[self.movie.id])
        over_limit=await self.save(self.movie,progress=2)
        self.assertEqual(over_limit.status_code,409,over_limit.text)

    async def test_local_tracking_delivery_uses_only_changed_fields_after_save(self):
        response = await self.save(self.movie, progress=1, manual_score=8)
        self.assertEqual(response.status_code, 200, response.text)
        self.local_outbound.assert_awaited_once()
        self.assertEqual(self.local_outbound.await_args.args, (
            self.owner.id, {self.movie.id}, {(self.movie.id, None): 8}, set(),
        ))
        rating = (await self.db.execute(select(Rating).where(
            Rating.user_id == self.owner.id, Rating.media_id == self.movie.id,
        ))).scalar_one()
        rated_at = rating.rated_at
        self.local_outbound.reset_mock()
        response = await self.save(self.movie, notes='Updated privately')
        self.assertEqual(response.status_code, 200, response.text)
        self.local_outbound.assert_not_awaited()
        await self.db.refresh(rating)
        self.assertEqual(rating.rated_at, rated_at)

    async def test_direct_rating_changes_dispatch_after_local_save(self):
        payload = {'media_id': self.movie.id, 'media_type': 'movie', 'rating': 7, 'review': 'First'}
        response = await self.client.post('/ratings', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.local_outbound.assert_awaited_once_with(
            self.owner.id, set(), {(self.movie.id, None): 7}, set(),
        )
        self.local_outbound.reset_mock()
        response = await self.client.post('/ratings', json={**payload, 'review': 'Updated'})
        self.assertEqual(response.status_code, 200, response.text)
        self.local_outbound.assert_not_awaited()
        response = await self.client.delete('/ratings', params={
            'media_id': self.movie.id, 'media_type': 'movie',
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.local_outbound.assert_awaited_once_with(
            self.owner.id, set(), {}, {(self.movie.id, None)},
        )

    async def test_series_progress_advances_from_paused_status_and_ignores_future_episodes(self):
        self.show.tmdb_id=987654322
        self.show.tmdb_data={'tracking_catalogue_refreshed_at':datetime.now().isoformat(),
                             'tracking_episode_ids':[987654323,987654324]}
        series=Show(title='Fixture Show',tmdb_id=self.show.tmdb_id)
        self.db.add(series);await self.db.flush()
        self.db.add_all([
            Media(title='Released',media_type=MediaType.episode,tmdb_id=987654323,show_id=series.id,
                  season_number=1,episode_number=1,release_date='2020-01-01'),
            Media(title='Future',media_type=MediaType.episode,tmdb_id=987654324,show_id=series.id,
                  season_number=1,episode_number=2,release_date='2999-01-01'),
        ]);await self.db.commit()
        await self.save(self.show,status='paused')
        advanced=await self.save(self.show,progress=1)
        self.assertEqual(advanced.status_code,200,advanced.text)
        self.assertEqual(advanced.json()['status'],'completed')
        result=(await self.client.get(f'/tracking/profile/{self.owner.username}/series')).json()['entries'][0]
        self.assertEqual(result['season_position'],'S1E1')
        self.assertEqual(result['released_episodes'],1)
        self.assertEqual((await self.save(self.show,progress=2)).status_code,409)

    async def test_snapshot_first_empty_and_partial_pull_cannot_remove_tracking(self):
        from core.tracking_snapshot import observe_stream_snapshot
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Test only',url='https://example.test',token='fixture')
        self.db.add(conn);await self.db.commit()
        await self.save(self.movie,status='watching')
        await observe_stream_snapshot(self.db,conn,[],[],[],{},complete=False)
        self.assertIsNone(await self.db.get(StreamBaseline,conn.id))
        await observe_stream_snapshot(self.db,conn,[],[],[],{})
        entry=(await self.db.execute(select(TrackedEntry).where(TrackedEntry.user_id==self.owner.id))).scalar_one()
        self.assertEqual(entry.status,'watching')
        baseline=await self.db.get(StreamBaseline,conn.id)
        self.assertFalse(baseline.approved)
        reviews=(await self.db.execute(select(SyncReview).where(SyncReview.user_id==self.owner.id))).scalars().all()
        self.assertEqual([(r.kind,r.state) for r in reviews],[('initial_import','pending')])

    async def test_verified_removal_auto_confirms_only_after_initial_approval(self):
        from core.tracking_snapshot import observe_stream_snapshot
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Test only',url='https://example.test',token='fixture')
        self.movie.tmdb_id=987654320
        self.db.add_all([conn,TrackingPreferences(user_id=self.owner.id,auto_confirm=True)])
        await self.db.commit()
        await self.save(self.movie,status='watching')
        row={'content_id':'tt-fixture','content_type':'movie','position':30,'duration':100}
        await observe_stream_snapshot(self.db,conn,[],[],[row],{'tt-fixture':self.movie.tmdb_id})
        baseline=await self.db.get(StreamBaseline,conn.id)
        baseline.approved=True
        baseline.observed_at=datetime.now()+timedelta(seconds=1)
        await self.db.commit()
        await observe_stream_snapshot(self.db,conn,[],[],[],{},complete=False)
        self.assertIn('tt-fixture',baseline.snapshot['progress'])
        await observe_stream_snapshot(self.db,conn,[],[],[],{})
        entry=(await self.db.execute(select(TrackedEntry).where(TrackedEntry.user_id==self.owner.id))).scalar_one()
        self.assertEqual(entry.status,'dropped')
        review=(await self.db.execute(select(SyncReview).where(SyncReview.kind=='playback_removed',SyncReview.user_id==self.owner.id))).scalar_one()
        self.assertEqual(review.state,'confirmed')
        activity=(await self.db.execute(select(TrackingActivity).where(TrackingActivity.user_id==self.owner.id,TrackingActivity.status=='dropped'))).scalars().all()
        self.assertEqual(len(activity),1)

    async def test_metadata_refresh_enables_released_progress_without_future_history(self):
        self.show.tmdb_id=987654319
        await self.db.commit()
        details={'seasons':[{'season_number':1,'episode_count':2,'name':'Season 1'}]}
        season={'episodes':[{'id':987654310,'episode_number':1,'name':'First','air_date':'2020-01-01'},
                            {'id':987654311,'episode_number':2,'name':'Future','air_date':'2999-01-01'}]}
        with patch('routers.media.get_user_tmdb_key',new=AsyncMock(return_value='fixture-key')), \
             patch('core.tmdb.get_show',new=AsyncMock(return_value=details)), \
             patch('core.tmdb.get_season',new=AsyncMock(return_value=season)):
            res=await self.client.post(f'/tracking/title/{self.show.id}/refresh-episodes')
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(res.json()['episodes'],2)
        res=await self.save(self.show,status='completed')
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(res.json()['progress'],1)
        result=(await self.client.get(f'/tracking/profile/{self.owner.username}/series')).json()['entries'][0]
        self.assertEqual(result['season_position'],'S1E1')
        rows=(await self.db.execute(select(Media).join(WatchEvent,WatchEvent.media_id==Media.id).where(WatchEvent.user_id==self.owner.id))).scalars().all()
        self.assertEqual([row.tmdb_id for row in rows],[987654310])
        # A newly released episode appears as unwatched without moving the
        # completed entry back into Watching.
        future=(await self.db.execute(select(Media).where(Media.tmdb_id==987654311))).scalar_one()
        future.season_number=2
        future.release_date=date.today().isoformat()
        await self.db.commit()
        res=await self.client.get(f'/tracking/profile/{self.owner.username}/series')
        result=res.json()['entries'][0]
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['progress'],1)
        self.assertEqual(result['released_episodes'],2)
        self.assertEqual(result['season_position'],'S1E1')
        self.assertEqual(result['new_seasons'],1)
        entry=(await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id==self.owner.id,TrackedEntry.media_id==self.show.id))).scalar_one()
        entry.progress=8
        await self.db.commit()
        res=await self.save(self.show,progress=2)
        self.assertEqual(res.status_code,200,res.text)
        result=(await self.client.get(f'/tracking/profile/{self.owner.username}/series')).json()['entries'][0]
        self.assertEqual(result['season_position'],'S2E2')
        res=await self.save(self.show,status='watching')
        self.assertEqual(res.status_code,200,res.text)
        result=(await self.client.get(f'/tracking/profile/{self.owner.username}/series')).json()['entries'][0]
        self.assertEqual(result['new_seasons'],0)

    async def test_incomplete_metadata_does_not_enable_progress(self):
        self.show.tmdb_id=987654318
        await self.db.commit()
        media_id=self.show.id
        with patch('routers.media.get_user_tmdb_key',new=AsyncMock(return_value='fixture-key')), \
             patch('core.tmdb.get_show',new=AsyncMock(return_value={'seasons':[{'season_number':1,'episode_count':2}]})), \
             patch('core.tmdb.get_season',new=AsyncMock(return_value={'episodes':[]})):
            res=await self.client.post(f'/tracking/title/{media_id}/refresh-episodes')
        self.assertEqual(res.status_code,409,res.text)
        await self.db.refresh(self.show);await self.db.refresh(self.owner)
        self.assertNotIn('tracking_catalogue_refreshed_at',self.show.tmdb_data or {})
        res=await self.save(self.show,progress=1)
        self.assertEqual(res.status_code,409,res.text)

    async def test_tvdb_native_refresh_preserves_original_episode_positions(self):
        self.show.tvdb_id = 7654321
        canonical = Show(title='Fixture Show', tvdb_id=7654321, canonical_source='tvdb')
        self.db.add(canonical)
        await self.db.commit()
        series = {'id': 7654321, 'name': 'Fixture Show', 'seasons': [
            {'number': 1, 'type': {'type': 'official'}, 'episodeCount': 2},
        ]}
        episodes = [
            {'id': 7101, 'seasonNumber': 1, 'number': 1, 'name': 'One', 'aired': '2020-01-01'},
            {'id': 7102, 'seasonNumber': 1, 'number': 2, 'name': 'Two', 'aired': '2020-01-08'},
        ]
        with patch('routers.media.get_user_tmdb_key', new=AsyncMock(return_value=None)), \
             patch('routers.shows.get_user_tvdb_key', new=AsyncMock(return_value='tvdb-key')), \
             patch('core.tvdb.get_series', new=AsyncMock(return_value=series)), \
             patch('core.tvdb.get_series_episodes', new=AsyncMock(return_value=episodes)):
            response = await self.client.post(f'/tracking/title/{self.show.id}/refresh-episodes')
        self.assertEqual(response.status_code, 200, response.text)
        rows = (await self.db.execute(select(Media).where(
            Media.show_id == canonical.id, Media.media_type == MediaType.episode,
        ).order_by(Media.episode_number))).scalars().all()
        self.assertEqual([(row.tvdb_id, row.season_number, row.episode_number) for row in rows], [
            (7101, 1, 1), (7102, 1, 2),
        ])
        response = await self.save(self.show, status='completed')
        self.assertEqual(response.status_code, 200, response.text)
        result = (await self.client.get(
            f'/tracking/profile/{self.owner.username}/series'
        )).json()['entries'][0]
        self.assertEqual(result['season_position'], 'S1E2')

        rows[0].season_number = 2
        await self.db.commit()
        with patch('routers.media.get_user_tmdb_key', new=AsyncMock(return_value=None)), \
             patch('routers.shows.get_user_tvdb_key', new=AsyncMock(return_value='tvdb-key')), \
             patch('core.tvdb.get_series', new=AsyncMock(return_value=series)), \
             patch('core.tvdb.get_series_episodes', new=AsyncMock(return_value=episodes)):
            rejected = await self.client.post(f'/tracking/title/{self.show.id}/refresh-episodes')
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn('history was preserved', rejected.json()['detail'])

    async def test_stream_push_requires_review_and_blocks_unresolved_conflicts(self):
        from core.tracking_snapshot import require_stream_reconciliation
        from fastapi import HTTPException
        conn=MediaServerConnection(user_id=self.owner.id,type='nuvio',name='Fixture',url='https://example.test',token='fixture')
        self.db.add(conn);await self.db.commit()
        with self.assertRaises(HTTPException) as error:
            await require_stream_reconciliation(self.db,conn)
        self.assertEqual(error.exception.status_code,409)
        baseline=StreamBaseline(user_id=self.owner.id,connection_id=conn.id,approved=False,snapshot={})
        self.db.add(baseline);await self.db.commit()
        with self.assertRaises(HTTPException):await require_stream_reconciliation(self.db,conn)
        baseline.approved=True;await self.db.commit()
        await require_stream_reconciliation(self.db,conn)
        review=SyncReview(user_id=self.owner.id,connection_id=conn.id,media_id=self.movie.id,kind='conflict',state='pending',message='Fixture conflict')
        self.db.add(review);await self.db.commit()
        with self.assertRaises(HTTPException):await require_stream_reconciliation(self.db,conn)
        review.state='confirmed';await self.db.commit()
        await require_stream_reconciliation(self.db,conn)

    async def test_media_server_first_import_requires_approval(self):
        from core.media_server_reconciliation import record_media_server_import
        from core.tracking_snapshot import require_stream_reconciliation
        from fastapi import HTTPException

        conn = MediaServerConnection(
            user_id=self.owner.id, type='jellyfin', name='Fixture Jellyfin',
            url='https://example.test', token='fixture', push_watched=True,
        )
        self.db.add(conn)
        await self.db.commit()
        self.assertFalse(await record_media_server_import(
            self.db, conn, {'movies': 1, 'errors': 1}, complete=False,
        ))
        self.assertIsNone(await self.db.get(StreamBaseline, conn.id))
        self.assertFalse(await record_media_server_import(
            self.db, conn, {'movies': 1, 'errors': 0}, complete=True,
        ))
        await self.db.commit()
        with self.assertRaises(HTTPException):
            await require_stream_reconciliation(self.db, conn)
        reviews = (await self.db.execute(select(SyncReview).where(
            SyncReview.connection_id == conn.id, SyncReview.kind == 'initial_import',
        ))).scalars().all()
        self.assertEqual(len(reviews), 1)
        response = await self.client.post(
            f'/tracking/recent-events/{reviews[0].id}', json={'action': 'confirm'},
        )
        self.assertEqual(response.status_code, 200, response.text)
        await require_stream_reconciliation(self.db, conn)
        self.assertTrue(await record_media_server_import(
            self.db, conn, {'movies': 2, 'errors': 0}, complete=True,
        ))
        await self.db.commit()
        self.assertEqual(len((await self.db.execute(select(SyncReview).where(
            SyncReview.connection_id == conn.id, SyncReview.kind == 'initial_import',
        ))).scalars().all()), 1)

    async def test_approved_media_server_exports_only_accepted_watch_change(self):
        from core.media_server_reconciliation import reconcile_media_server_pull
        from core.tracking_snapshot import require_stream_reconciliation
        from fastapi import HTTPException

        conn = MediaServerConnection(
            user_id=self.owner.id, type='jellyfin', name='Fixture Jellyfin',
            url='https://example.test', token='fixture',
        )
        self.db.add(conn)
        await self.db.commit()
        await self.save(self.movie, status='planning')
        self.db.add(StreamBaseline(
            user_id=self.owner.id, connection_id=conn.id, approved=True,
            snapshot={'kind': 'media_server'},
            observed_at=datetime.now() - timedelta(days=1),
        ))
        watched_at = datetime.now() + timedelta(days=1)
        self.db.add(WatchEvent(
            user_id=self.owner.id, media_id=self.movie.id,
            watched_at=watched_at, completed=True,
        ))
        await self.db.commit()
        stats = {'movies': 1, 'errors': 0}
        accepted, ratings = await reconcile_media_server_pull(
            self.db, conn, stats, {self.movie.id}, {}, complete=True,
        )
        self.assertEqual(accepted, {self.movie.id})
        self.assertEqual(ratings, {})
        self.assertEqual(stats['tracking_updates'], 1)

        # A later undated observation cannot overrule the explicit local edit.
        await self.save(self.movie, status='dropped')
        self.db.add(WatchEvent(
            user_id=self.owner.id, media_id=self.movie.id,
            watched_at=None, completed=True,
            created_at=datetime.now() + timedelta(seconds=2),
        ))
        await self.db.commit()
        rejected, ratings = await reconcile_media_server_pull(
            self.db, conn, {'movies': 1, 'errors': 0}, {self.movie.id}, {},
            complete=True,
        )
        self.assertEqual(rejected, set())
        self.assertEqual(ratings, {})
        conflicts = (await self.db.execute(select(SyncReview).where(
            SyncReview.connection_id == conn.id, SyncReview.kind == 'conflict',
            SyncReview.state == 'pending',
        ))).scalars().all()
        self.assertEqual(len(conflicts), 1)
        with self.assertRaises(HTTPException):
            await require_stream_reconciliation(self.db, conn)

    async def test_media_server_rating_uses_source_baseline_and_preserves_local_edit(self):
        from core.media_server_reconciliation import reconcile_media_server_pull

        conn = MediaServerConnection(
            user_id=self.owner.id, type='emby', name='Fixture Emby',
            url='https://example.test', token='fixture',
        )
        self.db.add(conn)
        await self.db.commit()
        await self.save(self.movie, status='planning', manual_score=8)
        row = (await self.db.execute(select(Rating).where(
            Rating.user_id == self.owner.id, Rating.media_id == self.movie.id,
        ))).scalar_one()
        row.rated_at = datetime.now() - timedelta(days=2)
        self.db.add(StreamBaseline(
            user_id=self.owner.id, connection_id=conn.id, approved=True,
            observed_at=datetime.now() - timedelta(days=1),
            snapshot={'kind': 'media_server', 'ratings': {f'{self.movie.id}:': 8}},
        ))
        await self.db.commit()

        watched, ratings = await reconcile_media_server_pull(
            self.db, conn, {'movies': 1, 'errors': 0}, set(),
            {(self.movie.id, None): 9}, complete=True,
        )
        self.assertEqual(watched, set())
        self.assertEqual(ratings, {(self.movie.id, None): 9})
        entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.owner.id,
            TrackedEntry.media_id == self.movie.id,
        ))).scalar_one()
        self.assertEqual(entry.manual_score, 9)

        await self.save(self.movie, manual_score=7)
        watched, ratings = await reconcile_media_server_pull(
            self.db, conn, {'movies': 1, 'errors': 0}, set(),
            {(self.movie.id, None): 9}, complete=True,
        )
        self.assertEqual(ratings, {})
        await self.db.refresh(entry)
        self.assertEqual(entry.manual_score, 7)
        watched, ratings = await reconcile_media_server_pull(
            self.db, conn, {'movies': 1, 'errors': 0}, set(),
            {(self.movie.id, None): 10}, complete=True,
        )
        self.assertEqual(ratings, {})
        await self.db.refresh(entry)
        self.assertEqual(entry.manual_score, 7)
        conflict = (await self.db.execute(select(SyncReview).where(
            SyncReview.connection_id == conn.id,
            SyncReview.kind == 'rating_conflict',
        ))).scalar_one()
        self.assertEqual(conflict.previous_score, 7)
        self.assertEqual(conflict.proposed_score, 10)

    async def test_first_media_server_rating_does_not_replace_existing_local_score(self):
        from core.media_server_reconciliation import reconcile_media_server_pull

        conn = MediaServerConnection(
            user_id=self.owner.id, type='plex', name='Fixture Plex',
            url='https://example.test', token='fixture',
        )
        self.db.add(conn)
        await self.db.commit()
        await self.save(self.movie, status='planning', manual_score=7)
        watched, ratings = await reconcile_media_server_pull(
            self.db, conn, {'movies': 1, 'errors': 0}, set(),
            {(self.movie.id, None): 9}, complete=True,
        )
        self.assertEqual((watched, ratings), (set(), {}))
        entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.owner.id,
            TrackedEntry.media_id == self.movie.id,
        ))).scalar_one()
        self.assertEqual(entry.manual_score, 7)
        reviews = (await self.db.execute(select(SyncReview).where(
            SyncReview.connection_id == conn.id,
            SyncReview.state == 'pending',
        ))).scalars().all()
        self.assertEqual({review.kind for review in reviews}, {'initial_import', 'rating_conflict'})

    async def test_completion_threshold_does_not_infer_removal(self):
        from core.tracking_snapshot import observe_stream_snapshot
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Fixture',url='https://example.test',token='fixture')
        self.movie.tmdb_id=987654317
        self.db.add(conn);await self.db.commit()
        await self.save(self.movie,status='watching')
        row={'content_id':'tt-fixture','content_type':'movie','position':30,'duration':100}
        await observe_stream_snapshot(self.db,conn,[],[],[row],{'tt-fixture':self.movie.tmdb_id})
        baseline=await self.db.get(StreamBaseline,conn.id)
        baseline.observed_at=datetime.now()+timedelta(seconds=1)
        await self.db.commit()
        await observe_stream_snapshot(self.db,conn,[],[],[{**row,'position':95}],{'tt-fixture':self.movie.tmdb_id})
        entry=(await self.db.execute(select(TrackedEntry).where(TrackedEntry.user_id==self.owner.id))).scalar_one()
        self.assertEqual(entry.status,'completed')
        review=(await self.db.execute(select(SyncReview).where(SyncReview.user_id==self.owner.id,SyncReview.kind=='playback_removed'))).scalars().all()
        self.assertEqual(review,[])

    async def test_established_stream_completion_import_creates_activity_and_rating_prompt_once(self):
        from core.tracking_snapshot import observe_stream_snapshot

        self.movie.title='The Death of Robin Hood'
        self.movie.tmdb_id=987654302
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Fixture',url='https://example.test',token='fixture')
        self.db.add(conn);await self.db.commit()

        # Establish and approve the connection before the title appears. Initial
        # imports remain quiet; this is a later provider change.
        await observe_stream_snapshot(self.db,conn,[],[],[],{})
        baseline=await self.db.get(StreamBaseline,conn.id)
        baseline.approved=True
        baseline.observed_at=datetime.now()+timedelta(seconds=1)
        await self.db.commit()

        watched_at=datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.add(WatchEvent(user_id=self.owner.id,media_id=self.movie.id,completed=True,watched_at=watched_at))
        await self.db.commit()
        row={'content_id':'tt-death-robin-hood','content_type':'movie'}
        snapshot_args=([],[row],[],{'tt-death-robin-hood':self.movie.tmdb_id})

        await observe_stream_snapshot(self.db,conn,*snapshot_args)
        await observe_stream_snapshot(self.db,conn,*snapshot_args)

        entry=(await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id==self.owner.id,TrackedEntry.media_id==self.movie.id))).scalar_one()
        prompts=(await self.db.execute(select(SyncReview).where(
            SyncReview.user_id==self.owner.id,SyncReview.media_id==self.movie.id,
            SyncReview.kind=='rating_needed',SyncReview.dismissed_at.is_(None)))).scalars().all()
        activity=(await self.db.execute(select(TrackingActivity).where(
            TrackingActivity.user_id==self.owner.id,TrackingActivity.media_id==self.movie.id))).scalars().all()
        self.assertEqual(entry.status,'completed')
        self.assertEqual(len(prompts),1)
        self.assertEqual(prompts[0].provider,'stremio')
        self.assertEqual(len(activity),1)
        self.assertEqual(activity[0].status,'completed')
        self.assertTrue(activity[0].payload['status_changed'])

    async def test_first_import_preserves_conflicting_local_status(self):
        from core.tracking_snapshot import observe_stream_snapshot, require_stream_reconciliation
        from fastapi import HTTPException
        conn=MediaServerConnection(user_id=self.owner.id,type='nuvio',name='Fixture',url='https://example.test',token='fixture')
        self.movie.tmdb_id=987654316
        self.db.add(conn);await self.db.commit()
        await self.save(self.movie,status='paused')
        row={'content_id':'tt-conflict','content_type':'movie','position':30,'duration':100}
        await observe_stream_snapshot(self.db,conn,[],[],[row],{'tt-conflict':self.movie.tmdb_id})
        entry=(await self.db.execute(select(TrackedEntry).where(TrackedEntry.user_id==self.owner.id))).scalar_one()
        self.assertEqual(entry.status,'paused')
        review=(await self.db.execute(select(SyncReview).where(SyncReview.user_id==self.owner.id,SyncReview.kind=='conflict'))).scalar_one()
        self.assertEqual(review.proposed_status,'watching')
        baseline=await self.db.get(StreamBaseline,conn.id)
        baseline.approved=True;await self.db.commit()
        with self.assertRaises(HTTPException):await require_stream_reconciliation(self.db,conn)

    async def test_empty_first_connection_cannot_override_or_fan_out_local_status(self):
        from core.tracking_snapshot import observe_stream_snapshot, require_stream_reconciliation
        from models.tracking import StreamAction
        from fastapi import HTTPException

        self.movie.tmdb_id=987654301
        await self.save(self.movie,status='watching')
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Fresh empty account',
            url='https://example.test',token='fixture',push_playback=True)
        self.db.add(conn);await self.db.commit()

        await observe_stream_snapshot(self.db,conn,[],[],[],{})

        entry=(await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id==self.owner.id,TrackedEntry.media_id==self.movie.id))).scalar_one()
        baseline=await self.db.get(StreamBaseline,conn.id)
        actions=(await self.db.execute(select(StreamAction).where(
            StreamAction.user_id==self.owner.id))).scalars().all()
        self.assertEqual(entry.status,'watching')
        self.assertEqual(entry.status_source,'local')
        self.assertFalse(baseline.approved)
        self.assertEqual(actions,[])
        with self.assertRaises(HTTPException):await require_stream_reconciliation(self.db,conn)

    async def test_explicit_local_pause_queues_playback_removal_for_every_stream_account(self):
        from models.tracking import StreamAction

        self.movie.tmdb_id=987654302
        await self.save(self.movie,status='watching')
        connections=[]
        for index,kind in enumerate(('stremio','nuvio'),start=1):
            conn=MediaServerConnection(user_id=self.owner.id,type=kind,name=f'{kind} fixture',
                url='https://example.test',token='fixture',server_user_id='1',push_playback=True)
            self.db.add(conn);connections.append(conn)
        await self.db.flush()
        for index,conn in enumerate(connections,start=1):
            key=f'tt-local-{index}'
            record={'content_id':key,'content_type':'movie','position':30,'duration':100}
            self.db.add(StreamBaseline(user_id=self.owner.id,connection_id=conn.id,approved=True,
                snapshot={'progress':{key:record},'mappings':{key:self.movie.tmdb_id},'library':[]}))
        await self.db.commit()

        result=await self.save(self.movie,status='paused')
        self.assertEqual(result.status_code,200,result.text)

        entry=(await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id==self.owner.id,TrackedEntry.media_id==self.movie.id))).scalar_one()
        actions=(await self.db.execute(select(StreamAction).where(
            StreamAction.user_id==self.owner.id,StreamAction.action=='dismiss'))).scalars().all()
        self.assertEqual(entry.status_source,'local')
        self.assertIsNotNone(entry.status_changed_at)
        self.assertEqual({action.connection_id for action in actions},{conn.id for conn in connections})

    async def test_non_status_local_edit_does_not_rewrite_status_provenance(self):
        await self.save(self.movie,status='paused')
        entry=(await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id==self.owner.id,TrackedEntry.media_id==self.movie.id))).scalar_one()
        changed_at=entry.status_changed_at

        result=await self.save(self.movie,notes='Private note')
        self.assertEqual(result.status_code,200,result.text)
        await self.db.refresh(entry)
        self.assertEqual(entry.status_source,'local')
        self.assertEqual(entry.status_changed_at,changed_at)

    async def test_incremental_snapshot_preserves_untouched_titles(self):
        from core.tracking_snapshot import observe_stream_snapshot
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Fixture',url='https://example.test',token='fixture')
        self.movie.tmdb_id=987654315
        self.db.add(conn);await self.db.commit()
        await self.save(self.movie,status='watching')
        row={'content_id':'tt-keep','content_type':'movie','position':30,'duration':100}
        await observe_stream_snapshot(self.db,conn,[],[],[row],{'tt-keep':self.movie.tmdb_id})
        await observe_stream_snapshot(self.db,conn,[],[],[],{},complete=False,touched={'tt-other'})
        baseline=await self.db.get(StreamBaseline,conn.id)
        self.assertIn('tt-keep',baseline.snapshot['progress'])
        entry=(await self.db.execute(select(TrackedEntry).where(TrackedEntry.user_id==self.owner.id))).scalar_one()
        self.assertEqual(entry.status,'watching')
        baseline.observed_at=datetime.now()+timedelta(seconds=1)
        await self.db.commit()
        await observe_stream_snapshot(self.db,conn,[],[],[],{},complete=False,touched={'tt-keep'})
        self.assertEqual(entry.status,'dropped')
        self.assertNotIn('tt-keep',baseline.snapshot['progress'])

    async def test_queued_dismissal_waits_for_approval_retries_and_acknowledges(self):
        from core.stream_actions import queue_dismissals, dispatch_stream_actions
        from models.tracking import StreamAction
        self.movie.tmdb_id=987654314
        source=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Source',url='https://example.test',token='fixture')
        target=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Target',url='https://example.test',token='fixture',push_playback=True)
        self.db.add_all([source,target]);await self.db.commit()
        await self.save(self.movie,status='dropped')
        record={'content_id':'tt-target','content_type':'movie','position':30,'duration':100}
        baseline=StreamBaseline(user_id=self.owner.id,connection_id=target.id,approved=False,
            snapshot={'progress':{'tt-target':record},'mappings':{'tt-target':self.movie.tmdb_id},'library':['tt-target']})
        self.db.add(baseline);await self.db.commit()
        await queue_dismissals(self.db,source,self.movie)
        await queue_dismissals(self.db,source,self.movie)
        await self.db.commit()
        actions=(await self.db.execute(select(StreamAction).where(StreamAction.user_id==self.owner.id))).scalars().all()
        self.assertEqual(len(actions),1)
        action=actions[0]
        with patch('core.stream_actions.dismiss_stremio',AsyncMock()) as write:
            await dispatch_stream_actions(self.db,self.owner.id)
            write.assert_not_awaited()
        self.assertEqual(action.state,'pending')
        baseline.approved=True;await self.db.commit()
        with patch('core.stream_actions.dismiss_stremio',AsyncMock(side_effect=TimeoutError('sensitive response'))):
            await dispatch_stream_actions(self.db,self.owner.id)
        self.assertEqual(action.state,'pending')
        self.assertEqual(action.last_error,'TimeoutError')
        with patch('core.stream_actions.dismiss_stremio',AsyncMock()) as write:
            await dispatch_stream_actions(self.db,self.owner.id)
            await dispatch_stream_actions(self.db,self.owner.id)
            write.assert_awaited_once()
        self.assertEqual(action.state,'applied')
        self.assertEqual(action.attempts,2)
        self.assertEqual(action.payload,{})
        self.assertEqual(baseline.snapshot['progress'],{})
        self.assertEqual(baseline.snapshot['library'],['tt-target'])

    async def test_ordered_resume_delta_coalesces_for_an_eligible_peer(self):
        from core.tracking_snapshot import observe_stream_snapshot
        from core.stream_actions import dispatch_stream_actions

        self.movie.tmdb_id=987654319
        self.movie.imdb_id='tt-source-resume'
        await self.save(self.movie,status='watching')
        source=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Source',
            url='https://example.test',token='source',push_playback=True)
        target=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Target',
            url='https://example.test',token='target',push_playback=True)
        self.db.add_all([source,target]);await self.db.flush()
        target_record={'content_id':'tt-target-resume','content_type':'movie','position':10,'duration':100}
        self.db.add(StreamBaseline(user_id=self.owner.id,connection_id=target.id,approved=True,
            snapshot={'progress':{'tt-target-resume':target_record},
                'mappings':{'tt-target-resume':self.movie.tmdb_id},'library':[]}))
        await self.db.commit()
        await observe_stream_snapshot(self.db,source,[],[],[],{})
        source_baseline=await self.db.get(StreamBaseline,source.id)
        base=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(seconds=2)
        source_baseline.approved=True
        source_baseline.observed_at=base-timedelta(minutes=10)
        await self.db.commit()

        first={'content_id':'tt-source-resume','content_type':'movie','position':70,'duration':100,
            'modified_at':base.isoformat()+'Z','last_watched':1700000000000}
        await observe_stream_snapshot(self.db,source,[],[],[first],{'tt-source-resume':self.movie.tmdb_id})
        actions=(await self.db.execute(select(StreamAction).where(
            StreamAction.user_id==self.owner.id,StreamAction.action=='upsert'))).scalars().all()
        self.assertEqual(len(actions),1)
        self.assertEqual(actions[0].connection_id,target.id)
        self.assertEqual(actions[0].payload['content_id'],'tt-target-resume')
        self.assertEqual(actions[0].payload['position'],70)
        self.assertEqual(actions[0].payload['observed_at'],base.isoformat()+'Z')

        # A trustworthy newer correction may move the position backwards and
        # replaces the still-pending destination write instead of duplicating it.
        corrected={**first,'position':25,'modified_at':(base+timedelta(minutes=5)).isoformat()+'Z'}
        await observe_stream_snapshot(self.db,source,[],[],[corrected],{'tt-source-resume':self.movie.tmdb_id})
        await self.db.refresh(actions[0])
        self.assertEqual(actions[0].payload['position'],25)
        self.assertEqual(len((await self.db.execute(select(StreamAction).where(
            StreamAction.user_id==self.owner.id,StreamAction.action=='upsert'))).scalars().all()),1)

        with patch('core.stream_actions.push_stremio_progress',AsyncMock()) as write:
            await dispatch_stream_actions(self.db,self.owner.id)
            await dispatch_stream_actions(self.db,self.owner.id)
            write.assert_awaited_once()
        self.assertEqual(actions[0].state,'applied')
        target_baseline=await self.db.get(StreamBaseline,target.id)
        self.assertEqual(target_baseline.snapshot['progress']['tt-target-resume']['position'],25)
        self.assertIn('tt-target-resume',target_baseline.snapshot['outbound'])

        # The matching destination pull acknowledges our write and must not
        # fan the same value back to the original source connection.
        echoed={**corrected,'content_id':'tt-target-resume','modified_at':(base+timedelta(minutes=6)).isoformat()+'Z'}
        await observe_stream_snapshot(self.db,target,[],[],[echoed],{'tt-target-resume':self.movie.tmdb_id})
        self.assertNotIn('tt-target-resume',target_baseline.snapshot['outbound'])
        pending=(await self.db.execute(select(StreamAction).where(
            StreamAction.user_id==self.owner.id,StreamAction.state=='pending',
            StreamAction.action=='upsert'))).scalars().all()
        self.assertEqual(pending,[])

    async def test_newer_cross_provider_resume_can_move_backward_and_stale_delta_cannot_fan_out(self):
        from core.tracking_snapshot import observe_stream_snapshot

        self.movie.tmdb_id=987654320
        await self.save(self.movie,status='watching')
        source_a=MediaServerConnection(user_id=self.owner.id,type='stremio',name='A',
            url='https://example.test',token='a',push_playback=True)
        source_b=MediaServerConnection(user_id=self.owner.id,type='stremio',name='B',
            url='https://example.test',token='b',push_playback=True)
        self.db.add_all([source_a,source_b]);await self.db.commit()
        await observe_stream_snapshot(self.db,source_a,[],[],[],{})
        await observe_stream_snapshot(self.db,source_b,[],[],[],{})
        baseline_a=await self.db.get(StreamBaseline,source_a.id)
        baseline_b=await self.db.get(StreamBaseline,source_b.id)
        base=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(seconds=2)
        for baseline,key in ((baseline_a,'tt-a'),(baseline_b,'tt-b')):
            baseline.approved=True
            baseline.observed_at=base-timedelta(minutes=10)
            baseline.snapshot={**baseline.snapshot,'mappings':{key:self.movie.tmdb_id},
                'progress':{key:{'content_id':key,'content_type':'movie','position':10,'duration':100}}}
        await self.db.commit()

        first={'content_id':'tt-a','content_type':'movie','position':80,'duration':100,
            'modified_at':base.isoformat()+'Z'}
        await observe_stream_snapshot(self.db,source_a,[],[],[first],{'tt-a':self.movie.tmdb_id})
        await self.db.execute(delete(StreamAction).where(StreamAction.user_id==self.owner.id))
        await self.db.commit()

        # The newer B observation is authoritative even though its playback
        # position is lower than A's previous value.
        correction={'content_id':'tt-b','content_type':'movie','position':20,'duration':100,
            'modified_at':(base+timedelta(minutes=1)).isoformat()+'Z'}
        await observe_stream_snapshot(self.db,source_b,[],[],[correction],{'tt-b':self.movie.tmdb_id})
        entry=(await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id==self.owner.id,TrackedEntry.media_id==self.movie.id))).scalar_one()
        self.assertEqual(entry.status_source,f'stremio:{source_b.id}')
        self.assertEqual(entry.status_changed_at,base+timedelta(minutes=1))
        actions=(await self.db.execute(select(StreamAction).where(
            StreamAction.user_id==self.owner.id,StreamAction.state=='pending',
            StreamAction.action=='upsert'))).scalars().all()
        self.assertEqual([(action.connection_id,action.payload['position']) for action in actions],[(source_a.id,20)])

        await self.db.execute(delete(StreamAction).where(StreamAction.user_id==self.owner.id))
        await self.db.commit()
        simultaneous={**first,'position':85,
            'modified_at':(base+timedelta(minutes=1)).isoformat()+'Z'}
        await observe_stream_snapshot(self.db,source_a,[],[],[simultaneous],{'tt-a':self.movie.tmdb_id})
        await self.db.refresh(entry)
        self.assertEqual(entry.status_source,f'stremio:{source_a.id}')
        simultaneous_actions=(await self.db.execute(select(StreamAction).where(
            StreamAction.user_id==self.owner.id,StreamAction.state=='pending',
            StreamAction.action=='upsert'))).scalars().all()
        self.assertEqual([(action.connection_id,action.payload['position']) for action in simultaneous_actions],[(source_b.id,85)])

        await self.db.execute(delete(StreamAction).where(StreamAction.user_id==self.owner.id))
        await self.db.commit()
        stale={**first,'position':15,'modified_at':(base+timedelta(seconds=30)).isoformat()+'Z'}
        await observe_stream_snapshot(self.db,source_a,[],[],[stale],{'tt-a':self.movie.tmdb_id})
        await self.db.refresh(entry)
        self.assertEqual(entry.status_source,f'stremio:{source_a.id}')
        self.assertEqual((await self.db.execute(select(StreamAction).where(
            StreamAction.user_id==self.owner.id,StreamAction.state=='pending',
            StreamAction.action=='upsert'))).scalars().all(),[])

    async def test_nuvio_last_watched_alone_does_not_order_resume_fanout(self):
        from core.stream_actions import queue_progress_update

        self.movie.tmdb_id=987654318
        source=MediaServerConnection(user_id=self.owner.id,type='nuvio',name='Source',
            url='https://example.test',token='source',server_user_id='1')
        target=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Target',
            url='https://example.test',token='target',push_playback=True)
        self.db.add_all([source,target]);await self.db.flush()
        self.db.add(StreamBaseline(user_id=self.owner.id,connection_id=target.id,approved=True,
            snapshot={'mappings':{'tt-target':self.movie.tmdb_id},'progress':{},'library':[]}))
        await self.db.commit()
        await queue_progress_update(self.db,source,self.movie,{
            'content_id':'tt-source','content_type':'movie','position':40,'duration':100,
            'last_watched':1760000000000,
        })
        await self.db.commit()
        actions=(await self.db.execute(select(StreamAction).where(
            StreamAction.user_id==self.owner.id,StreamAction.action=='upsert'))).scalars().all()
        self.assertEqual(actions,[])

    async def test_stremio_resume_push_uses_position_identity_and_ordering(self):
        from core.stream_actions import push_stremio_progress, RemotePlaybackChanged

        remote={'_id':'tt-target','type':'series','_mtime':'2026-01-02T12:00:00Z',
            'state':{'timeOffset':80,'duration':100,'video_id':'tt-target:1:2'}}
        record={'content_id':'tt-target','content_type':'series','season':1,'episode':2,
            'position':20,'duration':100,'observed_at':'2026-01-02T12:05:00Z',
            'last_watched':1760000000000}
        with patch('core.stream_actions.stremio.datastore_get',AsyncMock(return_value=[remote])), \
             patch('core.stream_actions.stremio.datastore_put',AsyncMock()) as put:
            await push_stremio_progress('token',record)
        candidate=put.await_args.args[1][0]
        self.assertEqual(candidate['state']['timeOffset'],20)
        self.assertEqual(candidate['state']['duration'],100)
        self.assertEqual(candidate['state']['video_id'],'tt-target:1:2')
        self.assertEqual(candidate['state']['lastWatched'],1760000000000)

        newer_remote={**remote,'_mtime':'2026-01-02T12:06:00Z'}
        with patch('core.stream_actions.stremio.datastore_get',AsyncMock(return_value=[newer_remote])):
            with self.assertRaises(RemotePlaybackChanged):
                await push_stremio_progress('token',record)



    async def test_new_series_observation_advances_cumulatively_and_completed_stays_completed(self):
        from core.tracking_snapshot import observe_stream_snapshot
        self.show.tmdb_id=987654311
        self.show.tmdb_data={'tracking_catalogue_refreshed_at':'fixture'}
        show=Show(title='Cumulative fixture',tmdb_id=self.show.tmdb_id,canonical_source='tmdb')
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Fixture',url='https://example.test',token='fixture')
        self.db.add_all([show,conn]);await self.db.flush()
        episodes=[]
        for season,number,released in [(0,1,'2020-01-01'),(1,1,'2020-01-01'),(1,2,'2020-01-01'),(2,1,'2020-01-01'),(2,2,'2999-01-01')]:
            episode=Media(title='Episode',media_type=MediaType.episode,show_id=show.id,season_number=season,episode_number=number,release_date=released)
            self.db.add(episode);episodes.append(episode)
        await self.db.commit()
        await self.save(self.show,status='watching')
        await observe_stream_snapshot(self.db,conn,[],[],[],{})
        baseline=await self.db.get(StreamBaseline,conn.id)
        baseline.observed_at=datetime.now()+timedelta(seconds=1);await self.db.commit()
        row={'content_id':'tt-series','content_type':'series','season':2,'episode':1,'position':30,'duration':100}
        await observe_stream_snapshot(self.db,conn,[],[],[row],{'tt-series':self.show.tmdb_id})
        entry=(await self.db.execute(select(TrackedEntry).where(TrackedEntry.user_id==self.owner.id))).scalar_one()
        self.assertEqual(entry.progress,2);self.assertEqual(entry.status,'watching')
        baseline.observed_at=datetime.now()+timedelta(seconds=1);await self.db.commit()
        await observe_stream_snapshot(self.db,conn,[],[],[{**row,'position':95}],{'tt-series':self.show.tmdb_id})
        self.assertEqual(entry.progress,3);self.assertEqual(entry.status,'completed')
        self.assertEqual(entry.finish_date,date.today())
        watched=set((await self.db.execute(select(WatchEvent.media_id).where(WatchEvent.user_id==self.owner.id))).scalars())
        self.assertEqual(watched,{e.id for e in episodes[1:4]})
        baseline.observed_at=datetime.now()+timedelta(seconds=1);await self.db.commit()
        await observe_stream_snapshot(self.db,conn,[],[],[row],{'tt-series':self.show.tmdb_id})
        self.assertEqual(entry.status,'completed')
        activity=(await self.db.execute(select(TrackingActivity).where(
            TrackingActivity.user_id==self.owner.id,TrackingActivity.media_id==self.show.id))).scalars().all()
        self.assertEqual(len(activity),1)
        self.assertEqual(activity[0].payload['episodes_watched'],3)
        self.assertEqual(activity[0].payload['position'],'S2E1')

    async def test_tvdb_native_series_observation_uses_canonical_positions(self):
        from core.tracking_snapshot import observe_stream_snapshot
        self.show.tmdb_id=987654300
        self.show.tvdb_id=7654300
        self.show.tmdb_data={'tracking_catalogue_refreshed_at':'fixture','tracking_catalogue_provider':'tvdb'}
        show=Show(title='TVDB fixture',tmdb_id=self.show.tmdb_id,tvdb_id=self.show.tvdb_id,canonical_source='tvdb')
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Fixture',url='https://example.test',token='fixture')
        self.db.add_all([show,conn]);await self.db.flush()
        episodes=[]
        for number in (1,2,3):
            episode=Media(title=f'Episode {number}',media_type=MediaType.episode,show_id=show.id,
                tvdb_id=7200+number,season_number=1,episode_number=number,release_date='2020-01-01')
            self.db.add(episode);episodes.append(episode)
        await self.db.commit()
        await self.save(self.show,status='watching')
        await observe_stream_snapshot(self.db,conn,[],[],[],{})
        baseline=await self.db.get(StreamBaseline,conn.id)
        baseline.observed_at=datetime.now()+timedelta(seconds=1);await self.db.commit()
        row={'content_id':'tt-tvdb-series','content_type':'series','season':1,'episode':3,'position':95,'duration':100}
        await observe_stream_snapshot(self.db,conn,[],[],[row],{'tt-tvdb-series':self.show.tmdb_id})
        entry=(await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id==self.owner.id,TrackedEntry.media_id==self.show.id))).scalar_one()
        self.assertEqual(entry.progress,3)
        self.assertEqual(entry.status,'completed')
        watched=set((await self.db.execute(select(WatchEvent.media_id).where(
            WatchEvent.user_id==self.owner.id,WatchEvent.completed.is_(True)))).scalars())
        self.assertEqual(watched,{episode.id for episode in episodes})

    async def test_correcting_to_watching_restores_saved_progress_once(self):
        from core.stream_actions import dispatch_stream_actions
        from models.tracking import StreamAction
        self.movie.tmdb_id=987654313
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Fixture',url='https://example.test',token='fixture',push_playback=True)
        self.db.add(conn);await self.db.commit()
        await self.save(self.movie,status='dropped')
        record={'content_id':'tt-restore','content_type':'movie','position':30,'duration':100}
        baseline=StreamBaseline(user_id=self.owner.id,connection_id=conn.id,approved=True,
            snapshot={'resume':{'tt-restore':record},'mappings':{'tt-restore':self.movie.tmdb_id},'library':[]})
        self.db.add(baseline);await self.db.commit()
        result=await self.save(self.movie,status='watching')
        self.assertEqual(result.status_code,200,result.text)
        with patch('core.stream_actions.dismiss_stremio',AsyncMock()) as write:
            await dispatch_stream_actions(self.db,self.owner.id)
            await dispatch_stream_actions(self.db,self.owner.id)
            write.assert_awaited_once_with('fixture',record,restore=True)
        self.assertEqual(baseline.snapshot['progress']['tt-restore'],record)
        self.assertEqual(baseline.snapshot['resume'],{})
        self.assertEqual(baseline.snapshot['library'],[])

    async def test_deletion_reset_retries_conflict_and_acknowledges_without_library_removal(self):
        from core.stream_actions import dispatch_stream_actions,RemotePlaybackChanged
        from models.tracking import StreamAction
        self.movie.tmdb_id=987654312
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Fixture',url='https://example.test',token='fixture',push_playback=False,push_watched=False)
        self.db.add(conn);await self.db.commit()
        await self.save(self.movie,status='completed',manual_score=8)
        baseline=StreamBaseline(user_id=self.owner.id,connection_id=conn.id,approved=True,
            snapshot={'mappings':{'tt-delete':self.movie.tmdb_id},'library':['tt-delete']})
        self.db.add(baseline);await self.db.commit()
        result=await self.client.delete(f'/tracking/entry/{self.movie.id}?confirmed=true')
        self.assertEqual(result.status_code,200,result.text)
        marker=(await self.db.execute(select(TrackingDeletion).where(TrackingDeletion.user_id==self.owner.id))).scalar_one()
        action=(await self.db.execute(select(StreamAction).where(StreamAction.user_id==self.owner.id))).scalar_one()
        self.assertEqual(set(action.payload),{'content_id','content_type','deleted_at'})
        with patch('core.stream_actions.dismiss_stremio',AsyncMock(side_effect=RemotePlaybackChanged)):
            await dispatch_stream_actions(self.db,self.owner.id)
        self.assertEqual(action.state,'conflict')
        review=(await self.db.execute(select(SyncReview).where(SyncReview.user_id==self.owner.id,SyncReview.kind=='deletion_conflict'))).scalar_one()
        result=await self.client.post(f'/tracking/recent-events/{review.id}',json={'action':'confirm'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(action.state,'pending')
        with patch('core.stream_actions.dismiss_stremio',AsyncMock()) as write:
            await dispatch_stream_actions(self.db,self.owner.id)
            await dispatch_stream_actions(self.db,self.owner.id)
            write.assert_awaited_once()
            self.assertTrue(write.await_args.kwargs['reset'])
        self.assertEqual(marker.pending_connections,[])
        self.assertEqual(action.payload,{})
        self.assertEqual(baseline.snapshot['library'],['tt-delete'])

    async def test_local_deletion_cloud_reset_waits_for_first_import_approval(self):
        from core.cloud_actions import dispatch_cloud_actions

        self.movie.tmdb_id=987654299
        self.db.add(UserSettings(user_id=self.owner.id,mdblist_api_key='fixture-key'))
        self.db.add(CloudBaseline(user_id=self.owner.id,provider='mdblist',approved=False,snapshot={}))
        await self.db.commit()
        await self.save(self.movie,status='completed',manual_score=8)

        result=await self.client.delete(f'/tracking/entry/{self.movie.id}?confirmed=true')
        self.assertEqual(result.status_code,200,result.text)
        action=(await self.db.execute(select(CloudAction).where(
            CloudAction.user_id==self.owner.id))).scalar_one()
        marker=(await self.db.execute(select(TrackingDeletion).where(
            TrackingDeletion.user_id==self.owner.id))).scalar_one()
        self.assertEqual(marker.pending_connections,['mdblist'])

        operations=(
            patch('core.cloud_actions.mdblist.remove_watched',AsyncMock()),
            patch('core.cloud_actions.mdblist.remove_ratings',AsyncMock()),
            patch('core.cloud_actions.mdblist.remove_watchlist',AsyncMock()),
        )
        with operations[0] as watched,operations[1] as ratings,operations[2] as watchlist:
            await dispatch_cloud_actions(self.db,self.owner.id)
            watched.assert_not_awaited();ratings.assert_not_awaited();watchlist.assert_not_awaited()
            baseline=(await self.db.execute(select(CloudBaseline).where(
                CloudBaseline.user_id==self.owner.id,CloudBaseline.provider=='mdblist'))).scalar_one()
            baseline.approved=True;await self.db.commit()
            await dispatch_cloud_actions(self.db,self.owner.id)
            watched.assert_awaited_once();ratings.assert_awaited_once();watchlist.assert_awaited_once()
        self.assertEqual(action.state,'applied')
        self.assertEqual(action.payload,{})
        self.assertEqual(marker.pending_connections,[])

if __name__=='__main__': unittest.main()
