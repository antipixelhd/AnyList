"""Focused tracking API tests against an isolated transactional PostgreSQL fixture.

Set TRACKING_TEST_DATABASE_URL to the disposable local database; never production.
"""
import os
import unittest
from unittest.mock import AsyncMock, patch
from datetime import date, datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from db import get_db
from dependencies import get_current_user, get_optional_user
from models import User, UserProfileData, Media, GlobalSettings, Follow, WatchEvent, Collection, CollectionFile, Rating, Show, MediaServerConnection
from models.base import CollectionSource, MediaType, PrivacyLevel
from models.tracking import TrackedEntry, TrackingDeletion, StreamBaseline, SyncReview, TrackingPreferences, TrackingActivity, CloudBaseline
from routers.tracking import router


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
            await self.db.commit()
        self.viewer = self.owner
        app=FastAPI(); app.include_router(router,prefix='/tracking')
        async def session(): yield self.db
        app.dependency_overrides[get_db]=session
        app.dependency_overrides[get_current_user]=lambda:self.viewer
        app.dependency_overrides[get_optional_user]=lambda:self.viewer
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')

    async def asyncTearDown(self):
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

    async def test_manual_completed_only_defaults_finish(self):
        res=await self.save(self.movie,status='completed')
        self.assertEqual(res.status_code,200,res.text)
        self.assertIsNone(res.json()['start_date'])
        self.assertEqual(res.json()['finish_date'],date.today().isoformat())
        res=await self.save(self.movie,finish_date=None)
        self.assertIsNone(res.json()['finish_date'])
        res=await self.save(self.movie,status='completed')
        self.assertIsNone(res.json()['finish_date'])

    async def test_zero_clears_score_and_activity_groups(self):
        await self.save(self.movie,manual_score=8,status='watching')
        res=await self.save(self.movie,manual_score=0,status='paused')
        self.assertIsNone(res.json()['score'])
        res=await self.client.get('/tracking/activity')
        self.assertEqual(len(res.json()['results']),1)
        self.assertEqual(res.json()['results'][0]['status'],'paused')

    async def test_private_profile_denied_even_to_an_admin_viewer(self):
        self.owner.is_admin=True
        res=await self.client.get(f'/tracking/profile/{self.friend.username}/movie')
        self.assertEqual(res.status_code,403)

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

    async def test_title_community_average_excludes_private_profiles(self):
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
        self.assertEqual(res.json()['community_average'], 8)
        self.assertEqual(res.json()['community_count'], 1)

        friend_profile = (await self.db.execute(
            select(UserProfileData).where(UserProfileData.user_id == self.friend.id)
        )).scalar_one()
        friend_profile.privacy_level = PrivacyLevel.public
        await self.db.commit()
        res = await self.client.get(f'/tracking/title/{self.movie.id}')
        self.assertEqual(res.json()['community_average'], 9)
        self.assertEqual(res.json()['community_count'], 2)

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
        self.assertEqual(res.json()['progress'],0)
        self.assertEqual(res.json()['status'],'completed')
        ids=(await self.db.execute(select(WatchEvent.id).where(WatchEvent.user_id==self.owner.id))).scalars().all()
        self.assertEqual(ids,[])

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
        res=await self.save(self.show,mark_released_watched=True)
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(res.json()['progress'],1)
        rows=(await self.db.execute(select(Media).join(WatchEvent,WatchEvent.media_id==Media.id).where(WatchEvent.user_id==self.owner.id))).scalars().all()
        self.assertEqual([row.tmdb_id for row in rows],[987654310])
        # A newly released episode appears as unwatched without moving the
        # completed entry back into Watching.
        future=(await self.db.execute(select(Media).where(Media.tmdb_id==987654311))).scalar_one()
        future.release_date=date.today().isoformat()
        await self.db.commit()
        res=await self.client.get(f'/tracking/profile/{self.owner.username}/series')
        result=res.json()['entries'][0]
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['progress'],1)
        self.assertEqual(result['released_episodes'],2)
        self.assertEqual(result['unwatched_episodes'],1)

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
        conn=MediaServerConnection(user_id=self.owner.id,type='stremio',name='Fixture',url='https://example.test',token='fixture',push_playback=True,push_watched=True)
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

if __name__=='__main__': unittest.main()
