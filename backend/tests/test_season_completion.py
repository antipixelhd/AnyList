"""Season dates, transactional receipts and distinct historical cards."""
import importlib
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from sqlalchemy import delete, select, text, update
from alembic.migration import MigrationContext
from alembic.operations import Operations
import test_statistics_pg as fixture
from core.season_completion import check_release_boundaries, completion_evidence, reconcile, run_batch
from models import Media, Show, TrackedEntry, TrackingActivity, WatchEvent, ShowEpisodePosition, UserShowEpisodeOrder
from models.base import MediaType
from models.seasons import SeriesSeason, SeasonTracking, SeasonReconcileJob
from core.tracking_projection import activity_data
from routers.season_tracking import router
from dependencies import get_current_user, get_tracking_write_user
from unittest.mock import AsyncMock, patch


class EvidenceTests(unittest.TestCase):
    def test_tail_requires_coverage_and_shared_or_import_clocks_cannot_date_a_season(self):
        episodes = [SimpleNamespace(id=n, release_date='2020-01-01', tmdb_data={}) for n in (1, 2, 3)]
        def event(n, **kw):
            return SimpleNamespace(media_id=n, completed=True, watched_at=datetime(2024, 1, n),
                date_inferred=kw.get('inferred', False), date_shared=kw.get('shared', False), provisional=False)
        self.assertFalse(completion_evidence(episodes, [event(3)], 3)[0])
        values = [event(1, inferred=True), event(2), event(3, shared=True)]
        self.assertEqual(completion_evidence(episodes, values, 3)[:4], (True, 3, date(2024, 1, 2), 'inferred'))
        self.assertEqual(completion_evidence(episodes, [event(n, inferred=True) for n in (1,2,3)], 3)[2], None)
        replay = event(1)
        replay.watched_at = datetime(2026, 1, 1)
        self.assertEqual(completion_evidence(episodes, [*values, replay], 3)[2], date(2024,1,2))


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable PostgreSQL')
class SeasonDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)
        self.app.include_router(router)
        self.app.dependency_overrides[get_tracking_write_user] = lambda: self.viewer
        self.app.dependency_overrides[get_current_user] = lambda: self.viewer
        def migration(conn):
            # The fixture creates model tables; exercise the real upgrade too.
            for table in ('season_reconcile_jobs', 'season_tracking', 'series_seasons'):
                conn.execute(text(f'DROP TABLE {table}'))
            conn.execute(text('ALTER TABLE tracked_entries DROP COLUMN finish_date_source, '
                              'DROP COLUMN season_release_check_on, DROP COLUMN start_date_source'))
            with Operations.context(MigrationContext.configure(conn)):
                importlib.import_module('migrations.versions.mt086_season_completion').upgrade()
                importlib.import_module('migrations.versions.mt088_series_editor').upgrade()
                importlib.import_module('migrations.versions.mt089_link_series_start').upgrade()
                importlib.import_module('migrations.versions.mt090_season_activity_context').upgrade()
        async with self.engine.begin() as conn:
            await conn.run_sync(migration)
        async with self.Session() as db:
            show = Show(title='Two seasons', tmdb_id=923000)
            series = Media(title='Two seasons', tmdb_id=923000, media_type=MediaType.series,
                tmdb_data={'seasons': [{'season_number': n, 'episode_count': 2} for n in (1, 2)],
                           'tracking_catalogue_refreshed_at': '2024-01-01'})
            db.add_all([show, series])
            await db.flush()
            self.series_id = series.id
            self.episode_ids = []
            for sn in (1, 2):
                for en in (1, 2):
                    episode = Media(title=f'{sn}.{en}', tmdb_id=923000+sn*10+en,
                        media_type=MediaType.episode, show_id=show.id, season_number=sn,
                        episode_number=en, release_date='2020-01-01')
                    db.add(episode)
                    await db.flush()
                    self.episode_ids.append(episode.id)
                    db.add(WatchEvent(user_id=self.user_id, media_id=episode.id, completed=True,
                        watched_at=datetime(2024,1,1)))
            db.add(TrackedEntry(user_id=self.user_id, media_id=series.id, status='completed', progress=4))
            await db.commit()

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def rows(self, db):
        return list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id == self.series_id,
            TrackingActivity.payload['season_completion_id'].as_integer().is_not(None)).order_by(TrackingActivity.id)))

    async def test_same_day_seasons_are_distinct_and_repeat_imports_and_rewatches_keep_first_date(self):
        self.assertEqual(await run_batch(self.Session), 1)
        async with self.Session() as db:
            cards = await self.rows(db)
            self.assertEqual(len(cards), 2)
            self.assertEqual(len(activity_data([(card, await db.get(Media,self.series_id)) for card in cards])), 2)
            ids = [c.id for c in cards]
            db.add(WatchEvent(user_id=self.user_id, media_id=self.episode_ids[-1], completed=True,
                watched_at=datetime(2026, 1, 1)))
            await db.commit()
        await run_batch(self.Session)
        async with self.Session() as db:
            cards = await self.rows(db)
            self.assertEqual([c.id for c in cards], ids)
            self.assertEqual({c.created_at for c in cards}, {datetime(2024, 1, 1)})

    async def test_bulk_delete_reconciles_surviving_coverage_then_removes_invalid_completion(self):
        await run_batch(self.Session)
        async with self.Session() as db:
            db.add(WatchEvent(user_id=self.user_id, media_id=self.episode_ids[-1], completed=True,
                watched_at=datetime(2025, 2, 1)))
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id == self.episode_ids[-1],
                WatchEvent.watched_at == datetime(2024,1,1)))
            await db.commit()
        await run_batch(self.Session)
        async with self.Session() as db:
            cards = await self.rows(db)
            self.assertEqual(len(cards), 2)
            self.assertEqual(cards[-1].created_at, datetime(2025,2,1))
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id == self.episode_ids[-1]))
            await db.commit()
        await run_batch(self.Session)
        async with self.Session() as db:
            self.assertEqual(len(await self.rows(db)), 1)

    async def test_manual_dates_clear_and_restore_without_rewriting_episode_watches(self):
        await run_batch(self.Session)
        root = f'/tracking/entry/{self.series_id}/seasons'
        rows = (await self.client.get(root)).json()
        season_id = rows[0]['id']
        response = await self.client.patch(f'{root}/{season_id}', json={'completion_date':'2023-06-01'})
        self.assertEqual(response.status_code, 200, response.text)
        async with self.Session() as db:
            await reconcile(db, self.user_id, self.series_id)
            await db.commit()
            self.assertEqual((await db.get(SeasonTracking,(self.user_id,season_id))).completion_date, date(2023,6,1))
            self.assertEqual(set(await db.scalars(select(WatchEvent.watched_at).where(
                WatchEvent.media_id.in_(self.episode_ids)))), {datetime(2024,1,1)})
        response = await self.client.patch(f'{root}/{season_id}', json={'completion_date':None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['completed'])
        self.assertIsNone(response.json()['completion_date'])
        response = await self.client.patch(f'{root}/{season_id}', json={'automatic':True})
        self.assertEqual(response.json()['completion_date'], '2024-01-01')

    async def test_order_changes_do_not_create_cards_or_transfer_manual_dates(self):
        await run_batch(self.Session)
        async with self.Session() as db:
            for index, ep_id in enumerate(self.episode_ids):
                ep = await db.get(Media, ep_id)
                db.add(ShowEpisodePosition(series_tmdb_id=923000, order_key='tmdb:group:test',
                    display_season=1, display_episode=index+1, tmdb_episode_id=ep.tmdb_id,
                    tmdb_season_number=ep.season_number, tmdb_episode_number=ep.episode_number))
            db.add(UserShowEpisodeOrder(user_id=self.user_id, series_tmdb_id=923000, episode_order='tmdb:group:test'))
            await db.commit()
        await run_batch(self.Session)
        async with self.Session() as db:
            for _ in range(2):
                result = await reconcile(db, self.user_id, self.series_id)
                await db.commit()
                self.assertEqual(len(result), 1)
                self.assertTrue(result[0][1].completed)
                self.assertEqual(await self.rows(db), [])

    async def test_rollback_does_not_queue_and_deleting_entry_removes_private_tracking(self):
        await run_batch(self.Session)
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids)))
            await db.rollback()
            self.assertEqual(list(await db.scalars(select(SeasonReconcileJob))), [])
            await db.execute(delete(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            await db.commit()
            self.assertEqual(list(await db.scalars(select(SeasonTracking))), [])

    async def test_unknown_date_and_unreleased_season_are_distinct(self):
        async with self.Session() as db:
            await db.execute(update(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids)).values(date_inferred=True))
            await db.execute(update(Media).where(Media.id == self.episode_ids[-1]).values(release_date='2099-01-01'))
            await db.commit()
        await run_batch(self.Session)
        async with self.Session() as db:
            rows = await reconcile(db, self.user_id, self.series_id)
            self.assertTrue(rows[0][1].completed)
            self.assertIsNone(rows[0][1].completion_date)
            self.assertFalse(rows[1][1].completed)
            self.assertEqual(await self.rows(db), [])

    async def test_ordinary_activity_never_merges_into_season_cards(self):
        from core.activity import record_daily_activity
        from core.streaming_activity import record_streaming_activity
        await run_batch(self.Session)
        async with self.Session() as db:
            originals = [(c.id, c.payload, c.created_at) for c in await self.rows(db)]
            await record_daily_activity(db, user_id=self.user_id, media_id=self.series_id,
                status='watching', score=8, rating_changed=True, now=datetime(2024,1,1,1))
            await record_streaming_activity(db, self.user_id, self.series_id,
                [{'key':'episode:1:1', 'date':'2024-01-01', 'status':'watching', 'episodes_watched':1, 'position':'S1E1'}])
            await db.commit()
            self.assertEqual([(c.id, c.payload, c.created_at) for c in await self.rows(db)], originals)

    async def test_owner_endpoints_do_not_reveal_another_viewers_records(self):
        await run_batch(self.Session)
        root = f'/tracking/entry/{self.series_id}/seasons'
        season_id = (await self.client.get(root)).json()[0]['id']
        self.app.dependency_overrides[get_tracking_write_user] = lambda: SimpleNamespace(id=self.user_id+10000)
        self.assertEqual((await self.client.get(root)).status_code, 404)
        self.assertEqual((await self.client.patch(f'{root}/{season_id}',json={'completion_date':'2024-01-01'})).status_code, 404)

    async def test_retry_receipt_survives_worker_failure(self):
        with self.assertLogs('core.season_completion', level='ERROR'), patch(
                'core.season_completion.reconcile', side_effect=RuntimeError('fixture failure')):
            self.assertEqual(await run_batch(self.Session), 0)
        async with self.Session() as db:
            self.assertEqual(len(list(await db.scalars(select(SeasonReconcileJob)))), 1)
        self.assertEqual(await run_batch(self.Session), 1)

    async def test_legacy_completion_is_replaced_without_duplicate_title_card(self):
        async with self.Session() as db:
            db.add(TrackingActivity(user_id=self.user_id,media_id=self.series_id,status='completed',
                created_at=datetime(2024,1,1),payload={'status_changed':True,'finished_seasons':[1,2]}))
            await db.commit()
        await run_batch(self.Session)
        async with self.Session() as db:
            cards = list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id == self.series_id)))
            self.assertEqual(len(cards),2)
            self.assertEqual({tuple(c.payload['finished_seasons']) for c in cards}, {(1,),(2,)})

    async def test_catalogue_count_corrections_preserve_supported_historical_completion(self):
        await run_batch(self.Session)
        async with self.Session() as db:
            media = await db.get(Media, self.series_id)
            media.tmdb_data = {'seasons':[{'season_number':1,'episode_count':3},{'season_number':2,'episode_count':2}]}
            await db.commit()
        await run_batch(self.Session)
        async with self.Session() as db:
            rows = await reconcile(db, self.user_id, self.series_id)
            self.assertTrue(all(tracking.completed for _, tracking in rows))
            self.assertEqual(len(await self.rows(db)),2)

    async def test_order_change_does_not_suppress_future_first_completion(self):
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id == self.episode_ids[-1]))
            await db.commit()
        await run_batch(self.Session)
        async with self.Session() as db:
            for index, ep_id in enumerate(self.episode_ids):
                ep = await db.get(Media,ep_id)
                db.add(ShowEpisodePosition(series_tmdb_id=923000,order_key='tmdb:group:test',
                    display_season=1,display_episode=index+1,tmdb_episode_id=ep.tmdb_id,
                    tmdb_season_number=ep.season_number,tmdb_episode_number=ep.episode_number))
            db.add(UserShowEpisodeOrder(user_id=self.user_id,series_tmdb_id=923000,episode_order='tmdb:group:test'))
            await db.commit()
        await run_batch(self.Session)
        async with self.Session() as db:
            self.assertEqual(await self.rows(db),[])
            db.add(WatchEvent(user_id=self.user_id,media_id=self.episode_ids[-1],completed=True,
                watched_at=datetime(2024,2,1)))
            await db.commit()
        await run_batch(self.Session)
        async with self.Session() as db:
            cards = await self.rows(db)
            self.assertEqual(len(cards),1)
            self.assertEqual(cards[0].payload['episode_order'],'tmdb:group:test')

    async def test_editor_initial_completion_dates_only_latest_and_preserves_manual_season_dates(self):
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids)))
            await db.execute(update(TrackedEntry).where(TrackedEntry.media_id==self.series_id).values(status='planning',progress=0))
            await db.commit()
        root=f'/tracking/entry/{self.series_id}'
        initial=(await self.client.get(f'{root}/series-editor')).json()
        with patch('core.local_outbound.dispatch_local_tracking_delta', new_callable=AsyncMock):
            response=await self.client.patch(root,json={'status':'completed','finish_date':'2024-06-01',
                'series_editor':{'revision':initial['revision']}})
        self.assertEqual(response.status_code,200,response.text)
        saved=response.json()
        first,last=saved['series_editor']['seasons']
        self.assertIsNone(first['completion_date'])
        self.assertEqual(last['completion_date'],'2024-06-01')
        self.assertEqual(saved['finish_date_source'],'series')
        with patch('core.local_outbound.dispatch_local_tracking_delta', new_callable=AsyncMock):
            response=await self.client.patch(root,json={'rating_mode':'average','series_editor':{
                'order':saved['series_editor']['order'],'seasons':[{
                'season_number':2,'membership':last['membership'],'start_date':'2024-05-01',
                'completion_date':'2024-06-02','score':8.5}]}})
        self.assertEqual(response.status_code,200,response.text)
        edited=response.json()
        last=edited['series_editor']['seasons'][-1]
        self.assertEqual((last['start_date'],last['completion_date'],last['score']),('2024-05-01','2024-06-02',8.5))
        self.assertEqual(edited['finish_date'],'2024-06-02')
        self.assertEqual(edited['score'],8.5)

    async def test_editor_airing_guard_and_catch_up_use_released_episodes(self):
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids[2:])))
            await db.execute(update(TrackedEntry).where(TrackedEntry.media_id==self.series_id).values(status='watching',progress=2))
            await db.execute(update(Media).where(Media.id==self.episode_ids[-1]).values(
                release_date=(date.today()+timedelta(days=7)).isoformat()))
            await db.commit()
        root=f'/tracking/entry/{self.series_id}'
        initial=(await self.client.get(f'{root}/series-editor')).json()
        self.assertEqual(initial['airing']['previous_season'],1)
        self.assertFalse(initial['airing']['watched'])
        revision=initial['revision']
        blocked=await self.client.patch(root,json={'status':'completed','series_editor':{'revision':revision}})
        self.assertEqual(blocked.status_code,409,blocked.text)
        self.assertEqual(blocked.json()['detail']['code'],'series_editor_airing')
        with patch('core.local_outbound.dispatch_local_tracking_delta', new_callable=AsyncMock):
            previous=await self.client.patch(root,json={'status':'completed','series_editor':{
                'revision':revision,'action':'complete_previous'}})
        self.assertEqual(previous.status_code,200,previous.text)
        self.assertEqual((previous.json()['status'],previous.json()['progress']),('completed',2))
        self.assertIsNone(previous.json()['series_editor']['seasons'][-1]['completion_date'])
        with patch('core.local_outbound.dispatch_local_tracking_delta', new_callable=AsyncMock):
            response=await self.client.patch(root,json={'status':'completed','series_editor':{
                'revision':revision,'action':'catch_up','watch_date':date.today().isoformat()}})
        self.assertEqual(response.status_code,200,response.text)
        saved=response.json()
        self.assertEqual((saved['status'],saved['progress']),('watching',3))
        last=saved['series_editor']['seasons'][-1]
        self.assertTrue(saved['series_editor']['airing']['watched'])
        self.assertEqual(last['start_source'],'episode')
        self.assertIsNone(last['completion_date'])
        rejected=await self.client.patch(root,json={'status':'completed','series_editor':{
            'revision':saved['series_editor']['revision'],'action':'complete_previous'}})
        self.assertEqual(rejected.status_code,409,rejected.text)
        self.assertTrue(rejected.json()['detail']['airing']['watched'])

    async def test_series_and_first_season_share_the_start_date(self):
        root=f'/tracking/entry/{self.series_id}'
        await run_batch(self.Session)
        first=(await self.client.get(f'{root}/series-editor')).json()['seasons'][0]
        self.assertEqual(first['start_date'],'2024-01-01')
        response=await self.client.patch(root,json={'start_date':'2024-02-01','series_editor':{}})
        self.assertEqual(response.status_code,200,response.text)
        summary=(await self.client.get(f'{root}/series-editor')).json()
        first,second=summary['seasons']
        self.assertEqual(first['start_date'],'2024-02-01')
        self.assertEqual(second['start_date'],'2024-01-01')
        response=await self.client.patch(root,json={'series_editor':{'order':summary['order'],'seasons':[
            {'season_number':1,'membership':first['membership'],'start_date':None}]}})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()['start_date'])
        self.assertIsNone(response.json()['series_editor']['seasons'][0]['start_date'])
        async with self.Session() as db:
            await reconcile(db,self.user_id,self.series_id)
            entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
            self.assertIsNone(entry.start_date)
            self.assertEqual(entry.start_date_source,'manual')

    async def test_legacy_series_start_survives_reliable_watches_and_repeated_reconciliation(self):
        async with self.Session() as db:
            entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
            entry.start_date=date(2023,12,20)
            entry.start_date_source=None
            await db.commit()
        for _ in range(2):
            async with self.Session() as db:
                await reconcile(db,self.user_id,self.series_id)
                await db.commit()
            summary=(await self.client.get(f'/tracking/entry/{self.series_id}/series-editor')).json()
            self.assertEqual(summary['seasons'][0]['start_date'],'2023-12-20')
        async with self.Session() as db:
            entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
            self.assertEqual(entry.start_date,date(2023,12,20))
        first=summary['seasons'][0]
        changed=await self.client.patch(f'/tracking/entry/{self.series_id}',json={'series_editor':{'order':summary['order'],'seasons':[
            {'season_number':1,'membership':first['membership'],'start_date':'2024-03-01'}]}})
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(changed.json()['start_date'],'2024-03-01')

    async def test_release_boundary_clears_compatibility_date_without_touching_previous_season(self):
        future=date.today()+timedelta(days=7)
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids[2:])))
            await db.execute(update(Media).where(Media.id.in_(self.episode_ids[2:])).values(release_date=future.isoformat()))
            await db.commit()
        await run_batch(self.Session)
        async with self.Session() as db:
            entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
            self.assertEqual(entry.finish_date,date(2024,1,1))
            self.assertEqual(entry.season_release_check_on,future)
            entry.season_release_check_on=date.today()
            await db.execute(update(Media).where(Media.id==self.episode_ids[2]).values(release_date=date.today().isoformat()))
            await db.commit()
        await check_release_boundaries(self.Session)
        async with self.Session() as db:
            entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
            self.assertIsNone(entry.finish_date)
            self.assertEqual(len(await self.rows(db)),1)

    async def test_one_series_completion_downgrades_at_release_and_old_scores_are_snapshots(self):
        async with self.Session() as db:
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            entry.manual_score = 7
            await reconcile(db, self.user_id, self.series_id)
            cards = await self.rows(db)
            self.assertEqual([c.payload['series_completed'] for c in cards], [False, True])
            entry.manual_score = 8
            await reconcile(db, self.user_id, self.series_id)
            self.assertEqual([c.score for c in await self.rows(db)], [7, 7])
            media = await db.get(Media, self.series_id)
            show = await db.scalar(select(Show).where(Show.tmdb_id == media.tmdb_id))
            db.add(Media(title='New season', show_id=show.id, media_type=MediaType.episode,
                season_number=3, episode_number=1, release_date=date.today().isoformat()))
            media.tmdb_data = {**media.tmdb_data, 'seasons': [*media.tmdb_data['seasons'],
                {'season_number':3,'episode_count':1,'air_date':date.today().isoformat()}]}
            await db.flush()
            # Feed compatibility uses joined metadata even before the receipt runs.
            projected = activity_data([(card,media) for card in cards])
            self.assertFalse(any(c['payload'].get('series_completed') for c in projected))
            await reconcile(db, self.user_id, self.series_id)
            self.assertFalse(any(c.payload['series_completed'] for c in await self.rows(db)))
            self.assertEqual([c.score for c in await self.rows(db)], [7,7])

    async def test_explicit_season_status_resume_and_provider_progress_do_not_create_starts(self):
        from core.season_activity import record_season_activity
        from core.series_editor import editor_context
        from core.activity import record_progress_activity
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids[3:])))
            await db.flush()
            media = await db.get(Media,self.series_id)
            now = datetime(2026,11,1,12)
            await record_season_activity(db,user_id=self.user_id,media=media,status='watching',score=None,
                status_changed=True,explicit_status=True,previous_status='paused',now=now)
            card = await db.scalar(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id))
            self.assertEqual(card.payload['season_number'],2)
            self.assertTrue(card.payload['continued_watching'])
            self.assertNotIn('position',card.payload)
            await record_progress_activity(db,user_id=self.user_id,media=media,previous_progress=2,
                progress=3,status='watching',score=None,status_changed=True,previous_status='paused',
                previous_watched_ids=set(self.episode_ids[:2]),watched_ids=set(self.episode_ids[:3]),now=now+timedelta(days=2))
            cards = list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id)))
            self.assertEqual(sum(bool(c.payload.get('started_watching')) for c in cards),1)
            # No partial progress in the next season means "Started", not "Continued".
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id==self.episode_ids[2]))
            await record_season_activity(db,user_id=self.user_id,media=media,status='watching',score=None,
                status_changed=True,explicit_status=True,previous_status='dropped',now=now+timedelta(days=4))
            latest = await db.scalar(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id)
                                    .order_by(TrackingActivity.created_at.desc()).limit(1))
            self.assertEqual(latest.payload['season_number'],2)
            self.assertFalse(latest.payload['continued_watching'])

    async def test_grouped_ratings_correct_in_place_then_change_only_in_that_season(self):
        from core.season_activity import record_season_activity
        async with self.Session() as db:
            media=await db.get(Media,self.series_id)
            now=datetime(2026,11,1,12)
            async def rate(changes,at,status='completed'):
                await record_season_activity(db,user_id=self.user_id,media=media,status=status,score=7,
                    season_changes=changes,now=at)
            await rate({1:(None,6),2:(None,8)},now)
            cards=list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id)))
            self.assertEqual(len(cards),1)
            group=cards[0]
            self.assertEqual(group.payload['season_ratings'],{'1':6,'2':8})
            self.assertEqual(group.score,7)
            await rate({1:(6,8)},now+timedelta(hours=48))
            self.assertEqual(group.created_at,now)
            self.assertEqual(group.score,8)
            await rate({1:(8,9)},now+timedelta(hours=73))
            cards=list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id)
                                       .order_by(TrackingActivity.created_at)))
            self.assertEqual(len(cards),2)
            self.assertEqual(cards[0].payload['season_ratings'],{'1':8,'2':8})
            self.assertEqual(cards[1].payload['previous_score'],8)
            self.assertEqual(cards[1].score,9)

    async def test_series_rating_in_a_new_season_preserves_the_previous_season_score(self):
        from core.season_activity import record_season_activity
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids[2:])))
            media=await db.get(Media,self.series_id)
            now=datetime(2026,11,1,12)
            await record_season_activity(db,user_id=self.user_id,media=media,status='watching',score=7,
                rating_changed=True,previous_score=None,now=now)
            db.add(WatchEvent(user_id=self.user_id,media_id=self.episode_ids[2],completed=True,watched_at=now))
            await db.flush()
            await record_season_activity(db,user_id=self.user_id,media=media,status='watching',score=8,
                rating_changed=True,previous_score=7,now=now+timedelta(days=3))
            cards=list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id)
                                       .order_by(TrackingActivity.created_at)))
            self.assertEqual([c.payload['season_ratings'] for c in cards],[{'1':7},{'2':8}])
            self.assertNotIn('previous_score',cards[-1].payload)

    async def test_editor_season_scores_without_series_score_changes_create_one_group(self):
        await run_batch(self.Session)
        response=await self.client.patch(f'/tracking/entry/{self.series_id}',json={
            'rating_mode':'manual','season_scores':{'1':6,'2':8}})
        self.assertEqual(response.status_code,200,response.text)
        async with self.Session() as db:
            groups=list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id,
                TrackingActivity.payload['season_rating_group'].as_boolean().is_(True))))
            self.assertEqual(len(groups),1)
            self.assertEqual(groups[0].score,7)
            self.assertEqual(groups[0].payload['season_ratings'],{'1':6,'2':8})

    async def test_completion_absorbs_only_its_season_and_manual_start_has_one_card(self):
        async with self.Session() as db:
            await db.execute(update(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids[2:]))
                             .values(watched_at=datetime(2024,1,5)))
            entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
            entry.start_date,entry.start_date_source=date(2023,12,31),'manual'
            db.add_all([
                TrackingActivity(user_id=self.user_id,media_id=self.series_id,status='watching',created_at=datetime(2023,12,31),
                    payload={'status_changed':True,'started_watching':True}),
                TrackingActivity(user_id=self.user_id,media_id=self.series_id,status='watching',created_at=datetime(2024,1,1),
                    payload={'window_key':'status:1','window_started_at':'2024-01-01T00:00:00',
                        'episode_order':'tmdb:aired','season_number':1,'season_status':True,
                        'status_changed':True,'started_watching':True}),
                TrackingActivity(user_id=self.user_id,media_id=self.series_id,status='watching',created_at=datetime(2024,1,1),score=7,
                    payload={'window_key':'rating:1','window_started_at':'2024-01-01T00:00:00',
                        'episode_order':'tmdb:aired','season_rating_group':True,'season_ratings':{'1':6,'2':8},
                        'rating_changed':True,'rating_first':True})])
            await db.flush()
            await reconcile(db,self.user_id,self.series_id)
            cards=list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id)))
            self.assertEqual(sum(bool(c.payload.get('started_watching')) for c in cards),1)
            self.assertTrue(next(c for c in cards if c.payload.get('started_watching')).payload.get('season_start_id'))
            first=next(c for c in cards if c.payload.get('finished_seasons')==[1])
            self.assertEqual(first.score,6)
            group=next(c for c in cards if c.payload.get('season_rating_group'))
            self.assertEqual(group.payload['season_ratings'],{'2':8})
            self.assertEqual(group.score,8)
            second=next(c for c in cards if c.payload.get('finished_seasons')==[2])
            self.assertIsNone(second.score)

    async def test_parallel_rating_requests_share_one_group_and_keep_both_scores(self):
        import asyncio
        from routers.ratings import router as ratings_router
        self.app.include_router(ratings_router,prefix='/ratings')
        with patch('core.local_outbound.dispatch_local_tracking_delta',new=AsyncMock()):
            responses=await asyncio.gather(*[self.client.post('/ratings',json={
                'media_id':self.series_id,'media_type':'series','season_number':sn,'rating':value})
                for sn,value in ((1,6),(2,8))])
        for response in responses:
            self.assertEqual(response.status_code,200,response.text)
        async with self.Session() as db:
            groups=list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id,
                TrackingActivity.payload['season_rating_group'].as_boolean().is_(True))))
            self.assertEqual(len(groups),1)
            self.assertEqual(groups[0].payload['season_ratings'],{'1':6,'2':8})
            self.assertEqual(groups[0].score,7)
            entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
            self.assertEqual(entry.season_scores,{'1':6,'2':8})

    async def test_deleting_recent_season_ratings_corrects_their_cards_in_every_order(self):
        from routers.ratings import router as ratings_router
        self.app.include_router(ratings_router,prefix='/ratings')
        with patch('core.local_outbound.dispatch_local_tracking_delta',new=AsyncMock()):
            for mode,order,tracked in (('manual',None,True),('average',None,True),
                                      ('manual','tvdb:dvd',True),('manual',None,False)):
                async with self.Session() as db:
                    await db.execute(delete(TrackingActivity).where(TrackingActivity.media_id==self.series_id))
                    entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
                    if tracked:
                        entry.rating_mode,entry.manual_score,entry.season_scores=mode,None,{}
                    elif entry:
                        await db.delete(entry)
                    await db.commit()
                body={'media_id':self.series_id,'media_type':'series','season_number':1,'rating':8}
                if order:body['episode_order']=order
                response=await self.client.post('/ratings',json=body)
                self.assertEqual(response.status_code,200,response.text)
                params={'media_id':self.series_id,'media_type':'series','season_number':1}
                if order:params['episode_order']=order
                response=await self.client.delete('/ratings',params=params)
                self.assertEqual(response.status_code,200,response.text)
                async with self.Session() as db:
                    cards=list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id)))
                    self.assertFalse(any((c.payload or {}).get('rating_changed') for c in cards),(mode,order,tracked))

    async def test_manual_start_reuses_explicit_start_before_season_completion(self):
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids)))
            entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
            entry.status,entry.progress,entry.start_date,entry.finish_date='planning',0,None,None
            await db.commit()
        response=await self.client.patch(f'/tracking/entry/{self.series_id}',json={'status':'watching'})
        self.assertEqual(response.status_code,200,response.text)
        async with self.Session() as db:
            original=await db.scalar(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id))
            original_id=original.id
        manual=(date.today()-timedelta(days=2)).isoformat()
        response=await self.client.patch(f'/tracking/entry/{self.series_id}',json={'start_date':manual})
        self.assertEqual(response.status_code,200,response.text)
        async with self.Session() as db:
            cards=list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id)))
            starts=[c for c in cards if c.payload.get('started_watching')]
            self.assertEqual(len(starts),1)
            self.assertEqual(starts[0].id,original_id)
            self.assertEqual(starts[0].created_at.date().isoformat(),manual)
            self.assertTrue(starts[0].payload.get('season_start_id'))
