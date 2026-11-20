"""Connection season completions have independently scoped rating requests."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from sqlalchemy import delete, select, text
import test_season_completion as seasons
from core.web_push import dispatch_rating_pushes, queue_sync_completion_rating
from models import Media, Rating, TrackedEntry, TrackingPreferences, UserSettings, WatchEvent, WebPushSubscription, MediaServerConnection, ShowEpisodePosition, UserShowEpisodeOrder
from models.tracking import CloudBaseline, SyncReview, StreamBaseline
from routers.ratings import router as ratings_router


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@unittest.skipUnless(seasons.fixture.URL, 'Requires explicit disposable PostgreSQL')
class SeasonRatingPromptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await seasons.SeasonDatabaseTests.asyncSetUp(self)
        self.app.include_router(ratings_router, prefix='/ratings')
        async with self.Session() as db:
            media = await db.get(Media, self.series_id)
            media.tmdb_data = {**media.tmdb_data, 'seasons': [
                {'season_number': sn, 'episode_count': 2, 'poster_path': f'/season-{sn}.jpg', 'air_date': '2024-01-01'} for sn in (1, 2)]}
            await db.commit()

    async def asyncTearDown(self):
        await seasons.SeasonDatabaseTests.asyncTearDown(self)

    async def prompts(self, db, active=False):
        query = select(SyncReview).where(SyncReview.user_id == self.user_id, SyncReview.kind == 'rating_needed')
        if active:
            query = query.where(SyncReview.dismissed_at.is_(None))
        return list(await db.scalars(query))

    async def queue(self, db):
        media = await db.get(Media, self.series_id)
        entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
        await queue_sync_completion_rating(db, user_id=self.user_id, media=media, entry=entry,
            source='trakt', new_episode_ids=set(self.episode_ids))
        await db.commit()

    async def partial(self):
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids[1:])))
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            entry.status, entry.progress = 'watching', 1
            entry.status_source, entry.status_changed_at = 'local', now()-timedelta(hours=2)
            db.add(CloudBaseline(user_id=self.user_id, provider='trakt', approved=True, snapshot={}, observed_at=now()-timedelta(hours=3)))
            db.add(WatchEvent(user_id=self.user_id, media_id=self.episode_ids[1], completed=True, watched_at=now()))
            await db.commit()

    async def test_seasons_have_independent_artwork_and_persistent_deduplication(self):
        async with self.Session() as db:
            await self.queue(db)
            rows = await self.prompts(db)
            self.assertEqual(sorted(row.season_number or 0 for row in rows), [0, 1, 2])
            for row in rows:
                if row.season_number:
                    self.assertEqual(row.payload['season_poster_path'], f'/season-{row.season_number}.jpg')
                    self.assertEqual(row.payload['rating_metadata'], {'episode_count': 2, 'year': '2024'})
            title = next(row for row in rows if row.season_number is None)
            self.assertEqual(title.payload['rating_metadata']['season_count'], 2)
            self.assertEqual(title.payload['rating_metadata']['episode_count'], 4)
            season = next(row for row in rows if row.season_number == 1)
            season.payload = {**season.payload, 'push_state': 'sent', 'delivered_subscription_ids': [77]}
            media = await db.get(Media, self.series_id)
            media.tmdb_data = {**media.tmdb_data, 'seasons': [{**item, 'poster_path': '/enriched.jpg'} for item in media.tmdb_data['seasons']]}
            await db.commit()
            await self.queue(db)
            await db.refresh(season)
            self.assertEqual(season.payload['season_poster_path'], '/enriched.jpg')
            self.assertEqual(season.payload['push_state'], 'sent')
            self.assertEqual(season.payload['delivered_subscription_ids'], [77])
            rows[0].dismissed_at = now()
            await db.commit()
            await self.queue(db)
            self.assertEqual(len(await self.prompts(db)), 3)

    async def test_overall_and_season_scores_resolve_only_their_own_requests(self):
        async with self.Session() as db:
            await self.queue(db)
            from core.activity import record_synced_rating_activity
            await record_synced_rating_activity(db, user_id=self.user_id, media=await db.get(Media,self.series_id),
                entry=None,season_number=1,previous_score=None,score=0,now=now())
            self.assertEqual(len(await self.prompts(db,active=True)),3)
        with patch('core.local_outbound.dispatch_local_tracking_delta', new=AsyncMock()):
            overall = await self.client.patch(f'/tracking/entry/{self.series_id}', json={'manual_score': 7})
            self.assertEqual(overall.status_code, 200, overall.text)
            season = await self.client.post('/ratings', json={'media_id': self.series_id, 'media_type': 'series',
                'season_number': 1, 'episode_order': 'tmdb:aired', 'rating': 8.5})
            self.assertEqual(season.status_code, 200, season.text)
        async with self.Session() as db:
            self.assertEqual([row.season_number for row in await self.prompts(db, active=True)], [2])
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            self.assertEqual(entry.manual_score, 7)
            self.assertEqual(entry.season_scores, {'1': 8.5})
            season_prompt=next(row for row in await self.prompts(db) if row.season_number==1)
            season_prompt.dismissed_at=None
            await db.commit()
        repeated=await self.client.post('/ratings',json={'media_id':self.series_id,'media_type':'series',
            'season_number':1,'episode_order':'tmdb:aired','rating':8.5})
        self.assertEqual(repeated.status_code,200,repeated.text)
        async with self.Session() as db:
            self.assertEqual([row.season_number for row in await self.prompts(db,active=True)],[2])

    async def test_existing_overall_score_does_not_hide_unrated_seasons_or_reask_rated_seasons(self):
        async with self.Session() as db:
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            entry.manual_score = 7
            entry.season_scores = {'2': 8}
            db.add(Rating(user_id=self.user_id, media_id=self.series_id, season_number=2, rating=8))
            await db.commit()
            await self.queue(db)
            self.assertEqual([row.season_number for row in await self.prompts(db)], [1])

    async def test_native_push_uses_the_season_art_and_independent_delivery(self):
        async with self.Session() as db:
            zero = Rating(user_id=self.user_id, media_id=self.series_id, season_number=1, rating=0)
            db.add(zero)
            await self.queue(db)
            db.add(WebPushSubscription(user_id=self.user_id, endpoint='https://push.example.test/season', p256dh='p'*80, auth='a'*24))
            await db.commit()
            with patch('core.web_push.asyncio.to_thread', new=AsyncMock()) as send:
                result = await dispatch_rating_pushes(db)
                await dispatch_rating_pushes(db)
                self.assertEqual(result['sent'], 3)
                self.assertEqual(send.await_count, 3)
                payloads = [call.args[2] for call in send.await_args_list]
                for sn in (1, 2):
                    payload = next(item for item in payloads if item['title'].endswith(f'Season {sn}'))
                    self.assertEqual(payload['icon'], f'https://image.tmdb.org/t/p/w500/season-{sn}.jpg')
                    self.assertEqual(payload['image'], payload['icon'])
                    self.assertTrue(payload['url'].startswith('/recent-events?rate='))
                zero.rating = 8.5
                await db.commit()
                await dispatch_rating_pushes(db)
                self.assertEqual({row.season_number for row in await self.prompts(db, active=True)}, {None, 2})
                self.assertEqual(send.await_count, 3)

    async def test_ongoing_cloud_completion_prompts_for_a_season_while_still_watching(self):
        from core.cloud_history_reconciliation import reconcile_cloud_watch_events
        await self.partial()
        async with self.Session() as db:
            for _ in range(2):
                await reconcile_cloud_watch_events(db, user_id=self.user_id, provider='trakt',
                    new_media_ids={self.episode_ids[1]}, initial_import_override=False)
            self.assertEqual([row.season_number for row in await self.prompts(db)], [1])
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            self.assertEqual(entry.status, 'watching')

    async def test_first_import_is_quiet_and_popup_preferences_are_independent(self):
        from core.cloud_history_reconciliation import reconcile_cloud_watch_events
        await self.partial()
        async with self.Session() as db:
            await reconcile_cloud_watch_events(db, user_id=self.user_id, provider='trakt',
                new_media_ids={self.episode_ids[1]}, initial_import_override=True)
            self.assertEqual(await self.prompts(db), [])
        defaults = await self.client.get('/tracking/preferences')
        self.assertTrue(defaults.json()['show_season_rating_popup'])
        saved = await self.client.patch('/tracking/preferences', json={'show_season_rating_popup': False})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertFalse(saved.json()['show_season_rating_popup'])
        self.assertTrue(saved.json()['show_new_ratings_popup'])

    async def test_preference_migration_preserves_an_existing_popup_opt_out(self):
        import importlib
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        async with self.Session() as db:
            db.add(TrackingPreferences(user_id=self.user_id, show_new_ratings_popup=False))
            await db.commit()
        def migrate(conn):
            conn.execute(text('ALTER TABLE tracking_preferences DROP COLUMN show_season_rating_popup'))
            with Operations.context(MigrationContext.configure(conn)):
                importlib.import_module('migrations.versions.mt092_season_rating_popup').upgrade()
        async with self.engine.begin() as conn:
            await conn.run_sync(migrate)
        result = await self.client.get('/tracking/preferences')
        self.assertFalse(result.json()['show_new_ratings_popup'])
        self.assertFalse(result.json()['show_season_rating_popup'])

    async def test_approved_stream_snapshot_completion_has_a_season_prompt(self):
        from core.tracking_snapshot import observe_stream_snapshot
        await self.partial()
        async with self.Session() as db:
            db.add(UserSettings(user_id=self.user_id, preferences={'watch_state_sync': 'immediate'}))
            conn = MediaServerConnection(user_id=self.user_id, type='stremio', name='Season prompt test', url='http://invalid', token='fixture', sync_watched=True)
            db.add(conn)
            await db.flush()
            db.add(StreamBaseline(user_id=self.user_id, connection_id=conn.id, approved=True,
                observed_at=now()-timedelta(hours=3), snapshot={'mappings': {'tmdb:923000': 923000}, 'watched': []}))
            await db.commit()
            row = {'content_id': 'tmdb:923000', 'content_type': 'series', 'season': 1, 'episode': 2,
                'watched_at': now().isoformat(), 'last_watched': int(now().replace(tzinfo=timezone.utc).timestamp()*1000)}
            await observe_stream_snapshot(db, conn, [], [row], [], {'tmdb:923000': 923000})
            await db.commit()
            self.assertEqual([row.season_number for row in await self.prompts(db)], [1])

    async def test_alternate_order_requests_and_ratings_keep_their_own_season_identity(self):
        async with self.Session() as db:
            order = 'tmdb:group:abcdef012345678901234567'
            for index, ep_id in enumerate([*self.episode_ids[2:], *self.episode_ids[:2]]):
                ep = await db.get(Media, ep_id)
                db.add(ShowEpisodePosition(series_tmdb_id=923000, order_key=order,
                    display_season=index//2+1, display_episode=index%2+1, tmdb_episode_id=ep.tmdb_id,
                    tmdb_season_number=ep.season_number, tmdb_episode_number=ep.episode_number))
            db.add(UserShowEpisodeOrder(user_id=self.user_id, series_tmdb_id=923000, episode_order=order))
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            entry.manual_score, entry.season_scores = 6, {'2': 9}
            await db.commit()
            await queue_sync_completion_rating(db, user_id=self.user_id, media=await db.get(Media, self.series_id),
                entry=entry, source='stremio', positions={(1, 2)})
            await db.commit()
            rows = await self.prompts(db)
            self.assertEqual([row.season_number for row in rows], [2])
            self.assertEqual(rows[0].payload['episode_order'], order)
        with patch('core.local_outbound.dispatch_local_tracking_delta', new=AsyncMock()):
            response = await self.client.post('/ratings', json={'media_id': self.series_id, 'media_type': 'series',
                'season_number': 2, 'episode_order': order, 'rating': 7.5})
            self.assertEqual(response.status_code, 200, response.text)
        async with self.Session() as db:
            self.assertEqual(await self.prompts(db, active=True), [])
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            self.assertEqual(entry.season_scores, {'2': 9})
            self.assertEqual(entry.manual_score, 6)
