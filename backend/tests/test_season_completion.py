"""Season dates, transactional receipts and distinct historical cards."""
import importlib
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from sqlalchemy import delete, select, text, update
from alembic.migration import MigrationContext
from alembic.operations import Operations
import test_statistics_pg as fixture
from core.season_completion import check_release_boundaries, clear_unavailable_jobs, completion_evidence, reconcile, run_batch
from models import Media, PlaybackProgress, Rating, Show, TrackedEntry, TrackingActivity, WatchEvent, ShowEpisodePosition, UserShowEpisodeOrder
from models.base import MediaType
from models.seasons import SeasonTracking, SeasonReconcileJob, SeriesSeason
from core.tracking_projection import activity_data
from core.screen_list import season_row_id
from routers.season_tracking import router
from dependencies import get_current_user, get_tracking_write_user
from unittest.mock import AsyncMock, patch


class EvidenceTests(unittest.TestCase):
    def test_older_season_count_preserves_out_of_order_watches_and_later_seasons(self):
        from core.series_editor import SeriesEditorContext, SeriesEditorPatch

        first, second, third, later = [SimpleNamespace(id=n, release_date='2020-01-01', tmdb_data={})
                                      for n in range(1, 5)]
        context = SeriesEditorContext(('tmdb:aired', [(1, [first, second, third], 3), (2, [later], 1)], {}),
                                      {1: (1, 1), 2: (1, 2), 3: (1, 3), 4: (2, 1)}, {3, 4})
        for count, expected, observed in ((0, {4}, None), (1, {3, 4}, None), (2, {1, 3, 4}, 1)):
            with self.subTest(count=count):
                patch = SeriesEditorPatch(action='position', order=context.order, revision=context.revision,
                    season_number=1, progress_season=1, episode_count=count, episode_id=count or None)
                self.assertEqual(context.resolve(patch, 'paused', False), (expected, observed))

    def test_invalid_persisted_cursor_is_ignored(self):
        from core.series_editor import SeriesEditorContext
        episode=SimpleNamespace(id=1,release_date='2020-01-01',tmdb_data={})
        data=('tmdb:aired',[(1,[episode],1)],{})
        for cursor in (None,{},[],['tmdb:aired'],'tmdb:aired',1,{'order':'tmdb:aired'},
                       {'order':'tmdb:aired','season_number':None,'episode_count':None,'episode_id':None}):
            with self.subTest(cursor=cursor):
                context=SeriesEditorContext(data,{1:(1,1)},{1},cursor)
                self.assertIsNone(context.selected_position())

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
        # Post-response delivery owns independent sessions. Keep those real
        # sessions in this disposable schema as well as the route dependency.
        self.enterContext(patch('core.local_outbound.engine', self.engine))
        self.enterContext(patch('db.engine', self.engine))
        self.app.include_router(router)
        self.app.dependency_overrides[get_tracking_write_user] = lambda: self.viewer
        self.app.dependency_overrides[get_current_user] = lambda: self.viewer
        def migration(conn):
            # The fixture creates model tables; exercise the real upgrade too.
            for table in ('season_reconcile_jobs', 'season_tracking', 'series_seasons'):
                conn.execute(text(f'DROP TABLE {table}'))
            conn.execute(text('ALTER TABLE tracked_entries DROP COLUMN finish_date_source, '
                              'DROP COLUMN season_release_check_on, DROP COLUMN start_date_source, DROP COLUMN season_cursor, '
                              'DROP COLUMN series_list_status'))
            with Operations.context(MigrationContext.configure(conn)):
                importlib.import_module('migrations.versions.mt086_season_completion').upgrade()
                importlib.import_module('migrations.versions.mt088_series_editor').upgrade()
                importlib.import_module('migrations.versions.mt089_link_series_start').upgrade()
                importlib.import_module('migrations.versions.mt090_season_activity_context').upgrade()
                importlib.import_module('migrations.versions.mt091_season_cursor').upgrade()
                importlib.import_module('migrations.versions.mt097_series_list_status').upgrade()
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

    async def test_delete_latest_season_keeps_earlier_evidence_and_the_series(self):
        await run_batch(self.Session)
        url = f'/tracking/entry/{self.series_id}'
        async with self.Session() as db:
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id,
                TrackedEntry.user_id == self.user_id))
            entry.notes, entry.season_scores = 'Keep this note', {'1': 8, '2': 9}
            first, latest = await self.rows(db)
            first_card = first.id
            latest_tracking = await db.get(SeasonTracking, (self.user_id, latest.payload['season_completion_id']))
            latest_tracking.start_date = date(2024, 1, 1)
            latest_tracking.start_manual = True
            latest_tracking.date_manual = True
            db.add_all([PlaybackProgress(user_id=self.user_id, media_id=identifier, progress_seconds=60)
                for identifier in (self.episode_ids[0], self.episode_ids[2])])
            db.add(TrackingActivity(user_id=self.user_id, media_id=self.series_id, status='completed', score=8.5,
                payload={'episode_order': 'tmdb:aired', 'season_rating_group': True, 'rating_changed': True,
                         'season_ratings': {'1': 8, '2': 9}, 'season_rating_baselines': {'1': None, '2': None}}))
            await db.commit()
        summary = (await self.client.get(f'{url}/series-editor')).json()
        intent = {'action': 'delete_season', 'season_number': 2, 'revision': summary['revision']}
        self.assertEqual((await self.client.patch(url, json={'series_editor': intent})).status_code, 409)
        stale = await self.client.patch(url, json={'confirm_rollback': True,
            'series_editor': {**intent, 'revision': 'old'}})
        self.assertEqual(stale.status_code, 409)
        deleted = await self.client.patch(url, json={'confirm_rollback': True, 'series_editor': intent})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        saved = deleted.json()
        self.assertEqual((saved['progress'], saved['status'], saved['notes']), (2, 'completed', 'Keep this note'))
        first, latest = saved['series_editor']['seasons']
        self.assertEqual((first['completed'], first['score']), (True, 8))
        self.assertEqual((latest['completed'], latest['score'], latest['start_date'], latest['completion_date']),
                         (False, None, None, None))
        async with self.Session() as db:
            self.assertEqual(set(await db.scalars(select(WatchEvent.media_id).where(
                WatchEvent.media_id.in_(self.episode_ids)))), set(self.episode_ids[:2]))
            self.assertEqual(list(await db.scalars(select(PlaybackProgress.media_id))),
                             [self.episode_ids[0]])
            self.assertEqual([card.id for card in await self.rows(db)], [first_card])
            grouped = await db.scalar(select(TrackingActivity).where(
                TrackingActivity.media_id == self.series_id,
                TrackingActivity.payload['season_rating_group'].as_boolean().is_(True)))
            self.assertEqual(grouped.payload['season_ratings'], {'1': 8})
            self.assertEqual(list(await db.scalars(select(Rating.season_number).where(
                Rating.media_id == self.series_id, Rating.season_number.is_not(None)))), [1])

        all_deleted = await self.client.delete(f'{url}?confirmed=true')
        self.assertEqual(all_deleted.status_code, 200, all_deleted.text)
        async with self.Session() as db:
            self.assertIsNone(await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id)))
            self.assertIsNone(await db.scalar(select(SeasonTracking.season_id).join(SeriesSeason).where(
                SeriesSeason.media_id == self.series_id, SeasonTracking.user_id == self.user_id)))

    async def test_unhydrated_receipts_are_batched_without_changing_private_dates_or_activity(self):
        async with self.Session() as db:
            empty = Media(title='Unhydrated series', tmdb_id=924100, media_type=MediaType.series)
            protected = Media(title='Series with historical activity', tmdb_id=924101, media_type=MediaType.series)
            db.add_all([empty, protected])
            await db.flush()
            db.add_all([TrackedEntry(user_id=self.user_id, media_id=empty.id, status='completed',
                start_date=date(2020, 1, 2), finish_date=date(2020, 1, 3)),
                TrackedEntry(user_id=self.user_id, media_id=protected.id, status='watching')])
            db.add(TrackingActivity(user_id=self.user_id, media_id=protected.id, status='watching',
                payload={'finished_seasons': [1]}))
            await db.commit()
            empty_id, protected_id = empty.id, protected.id
        self.assertEqual(await clear_unavailable_jobs(self.Session, 20), 1)
        async with self.Session() as db:
            self.assertIsNone(await db.get(SeasonReconcileJob, (self.user_id, empty_id)))
            self.assertIsNotNone(await db.get(SeasonReconcileJob, (self.user_id, protected_id)))
            self.assertIsNotNone(await db.get(SeasonReconcileJob, (self.user_id, self.series_id)))
            saved = await db.scalar(select(TrackedEntry).where(
                TrackedEntry.user_id == self.user_id, TrackedEntry.media_id == empty_id))
            self.assertEqual((saved.start_date, saved.finish_date), (date(2020, 1, 2), date(2020, 1, 3)))
            # A later canonical episode publication must create a new receipt.
            show = Show(title='Hydrated series', tmdb_id=924100)
            db.add(show)
            await db.flush()
            db.add(Media(title='Episode', media_type=MediaType.episode, show_id=show.id,
                season_number=1, episode_number=1, release_date='2020-01-01'))
            await db.commit()
            self.assertIsNotNone(await db.get(SeasonReconcileJob, (self.user_id, empty_id)))

    async def test_series_list_projects_one_parent_and_moves_the_active_season(self):
        root = f'/tracking/profile/{self.viewer.username}/series'
        editor = f'/tracking/entry/{self.series_id}'
        async with self.Session() as db:
            media = await db.get(Media, self.series_id)
            media.tmdb_data = {**media.tmdb_data, 'tracking_episode_ids': [923011, 923012, 923021, 923022],
                'seasons': [{'season_number': 1, 'episode_count': 2},
                            {'season_number': 2, 'name': 'The Second Campaign', 'episode_count': 2},
                            {'season_number': 3, 'name': 'Next Campaign', 'episode_count': 3}]}
            await db.commit()
        summary = (await self.client.get(f'{editor}/series-editor')).json()
        response = await self.client.patch(editor, json={'status': 'watching', 'confirm_rollback': True,
            'series_editor': {'revision': summary['revision'], 'action': 'position', 'season_number': 1,
                              'episode_count': 2, 'episode_id': self.episode_ids[1]}})
        self.assertEqual(response.status_code, 200, response.text)
        page = (await self.client.get(f'{root}/list')).json()
        self.assertEqual(page['group_counts'], {'watching': 1, 'completed': 1})
        active = next(row for row in page['results'] if row['status'] == 'watching')
        second_id = season_row_id(self.series_id, 2)
        self.assertEqual((active['list_id'], active['list_season']['number'], active['progress']), (second_id, 2, 0))
        self.assertTrue(response.json()['series_editor']['seasons'][0]['completed'])

        summary = response.json()['series_editor']
        self.assertEqual(summary['next_progress_season'], 2)
        response = await self.client.patch(editor, json={'series_editor': {
            'revision': summary['revision'], 'action': 'position', 'season_number': 2, 'progress_season': 2,
            'episode_count': 1, 'episode_id': self.episode_ids[2]}})
        self.assertEqual(response.status_code, 200, response.text)
        index = (await self.client.get(f'{root}/index')).json()['results']
        self.assertEqual({(row[0], row[2], row[4]) for row in index},
            {(self.series_id, 'completed', 2), (second_id, 'watching', 1)})
        rows = (await self.client.get(f'{root}/rows', params=[('ids', second_id), ('ids', self.series_id)])).json()
        self.assertEqual(rows[0]['title'], 'Two seasons - The Second Campaign')
        self.assertEqual((rows[1]['status'], rows[1]['entry']['status'], rows[1]['entry']['progress']), ('completed', 'watching', 3))
        filtered = (await self.client.get(f'{root}/list', params={'q': 'second campaign', 'limit': 1})).json()
        self.assertEqual([row['list_id'] for row in filtered['results']], [second_id])
        summary = response.json()['series_editor']
        earlier = await self.client.patch(editor, json={'confirm_rollback': True, 'series_editor': {
            'revision': summary['revision'], 'action': 'position', 'season_number': 1, 'progress_season': 1,
            'episode_count': 1, 'episode_id': self.episode_ids[0]}})
        self.assertEqual(earlier.status_code, 200, earlier.text)
        summary = earlier.json()['series_editor']
        self.assertEqual([[episode['watched'] for episode in season['episodes']] for season in summary['seasons']],
                         [[True, False], [True, False], []])
        response = await self.client.patch(editor, json={'series_editor': {
            'order': summary['order'], 'revision': summary['revision'],
            'action': 'position', 'season_number': 1, 'progress_season': 1,
            'episode_count': 2, 'episode_id': self.episode_ids[1], 'season_statuses': {'3': 'paused'}}})
        self.assertEqual(response.status_code, 200, response.text)
        page = (await self.client.get(f'{root}/list')).json()
        self.assertEqual(page['group_counts'], {'completed': 1, 'watching': 1, 'paused': 1})
        self.assertEqual({(row['status'], row.get('list_id', row['id'])) for row in page['results']},
                         {('completed', self.series_id), ('watching', second_id),
                          ('paused', season_row_id(self.series_id, 3))})
        self.assertFalse(next(row for row in page['results'] if row['status'] == 'paused')['list_season']['can_increment'])

    async def test_replaying_earlier_episode_advances_resume_without_adding_watches(self):
        from core.nuvio_projection import build_progress_items
        from core.series_playback import activity_resume, remember_episode
        from core.stremio_payloads import with_progress_state
        from core.tracking_snapshot import apply_series_observation
        async with self.Session() as db:
            root = await db.get(Media, self.series_id)
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            first = datetime(2026, 6, 1, 10)
            second = first + timedelta(minutes=30)
            remember_episode(entry, self.episode_ids[-1], first)
            row = {'season': 1, 'episode': 1, 'watched_at': second.isoformat()}
            complete, moved = await apply_series_observation(db, self.user_id, root, entry, [row], row,
                                                              activity_finished=True)
            self.assertTrue(complete and moved)
            self.assertEqual(entry.progress, 4)
            self.assertFalse(remember_episode(entry, self.episode_ids[-1], first))
            episodes = [await db.get(Media, identifier) for identifier in self.episode_ids]
            show = await db.get(Show, episodes[0].show_id)
            show.tmdb_data = {'external_ids': {'imdb_id': 'tt9230000'}}
            target, offset, at = activity_resume(entry, episodes, {})
            self.assertEqual((target.id, offset, at), (self.episode_ids[1], None, second))
            [nuvio] = await build_progress_items(db, self.user_id, next_up_for_watched_series=True)
            self.assertEqual((nuvio['season'], nuvio['episode'], nuvio['position'], nuvio['synthetic_resume']),
                             (1, 2, 1000, True))
            stremio = with_progress_state({'state': {'watched': 'existing-history'}}, {**nuvio, 'position': 1},
                                          {}, in_library=True)
            self.assertEqual((stremio['state']['video_id'], stremio['state']['timeOffset'], stremio['state']['watched']),
                             ('tt9230000:1:2', 1, 'existing-history'))
            self.assertEqual(len(list(await db.scalars(select(WatchEvent).where(
                WatchEvent.media_id.in_(self.episode_ids))))), 4)

    async def test_routine_stream_snapshot_leaves_earlier_season_gaps(self):
        from core.tracking_snapshot import observe_stream_snapshot
        from models import MediaServerConnection
        from models.tracking import StreamBaseline

        async with self.Session() as db:
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            before = datetime(2026, 6, 1)
            entry.status, entry.status_changed_at, entry.progress = 'watching', before, 1
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids[1:])))
            conn = MediaServerConnection(user_id=self.user_id, type='nuvio', name='Fixture',
                                         url='https://stream.example.test', token='fixture')
            db.add(conn)
            await db.flush()
            content_id = 'tt9230000'
            db.add(StreamBaseline(connection_id=conn.id, user_id=self.user_id, approved=True,
                                  observed_at=before, snapshot={'mappings': {content_id: 923000}}))
            await db.commit()
            done = {'content_id': content_id, 'content_type': 'series', 'season': 2, 'episode': 2,
                    'watched_at': (before + timedelta(hours=1)).isoformat()}
            await observe_stream_snapshot(db, conn, [], [done], [], {content_id: 923000})
            self.assertEqual(entry.progress, 2)
            self.assertEqual(entry.latest_episode_activity['episode_id'], self.episode_ids[-1])
            self.assertEqual(set(await db.scalars(select(WatchEvent.media_id).where(
                WatchEvent.media_id.in_(self.episode_ids)))), {self.episode_ids[0], self.episode_ids[-1]})

    async def test_explicit_season_completion_survives_a_new_release_and_catalogue_membership(self):
        entry_url = f'/tracking/entry/{self.series_id}'
        editor_url = f'{entry_url}/series-editor'
        profile = f'/tracking/profile/{self.viewer.username}/series'
        future = (date.today() + timedelta(days=2)).isoformat()
        async with self.Session() as db:
            series = await db.get(Media, self.series_id)
            series.status = 'Returning Series'
            series.tmdb_data = {**series.tmdb_data, 'tracking_episode_details_version': 1,
                'tracking_catalogue_refreshed_at': datetime.now().astimezone().isoformat()}
            episode = await db.get(Media, self.episode_ids[-1])
            episode.title, episode.release_date = 'Episode 2', future
            tracked = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            tracked.status, tracked.progress = 'watching', 2
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids[-2:])))
            await db.commit()

        summary = (await self.client.get(editor_url)).json()
        response = await self.client.patch(entry_url, json={'series_editor': {
            'action': 'complete_season', 'season_number': 2, 'order': summary['order'],
            'revision': summary['revision'], 'watch_date': date.today().isoformat()}})
        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()
        self.assertEqual((state['status'], state['progress']), ('completed', 3))
        current = state['series_editor']['seasons'][-1]
        self.assertTrue(current['completed'])
        self.assertTrue(current['manual_completion'])
        self.assertEqual([ep['id'] for ep in current['episodes'] if ep['watched']], [self.episode_ids[-2]])

        async with self.Session() as db:
            episode = await db.get(Media, self.episode_ids[-1])
            episode.release_date = date.today().isoformat()
            db.add(Media(title='Episode 3', media_type=MediaType.episode, show_id=episode.show_id,
                         tmdb_id=923023, season_number=2, episode_number=3, release_date=future))
            series = await db.get(Media, self.series_id)
            series.tmdb_data = {**series.tmdb_data, 'seasons': [
                {'season_number': 1, 'episode_count': 2}, {'season_number': 2, 'episode_count': 3}]}
            await db.flush()
            await reconcile(db, self.user_id, self.series_id)
            await db.commit()

        summary = (await self.client.get(editor_url)).json()
        current = summary['seasons'][-1]
        self.assertTrue(current['manual_completion'])
        self.assertTrue(current['completed'])
        self.assertEqual(current['completion_date'], date.today().isoformat())
        self.assertEqual([ep['watched'] for ep in current['episodes']], [True, False])
        seasons = (await self.client.get(f'{profile}/{self.series_id}/seasons')).json()['seasons']
        self.assertEqual((seasons[-1]['watched'], seasons[-1]['total'], seasons[-1]['completed']), (1, 2, True))
        response = await self.client.patch(entry_url, json={'series_editor': {
            'action': 'resume_season', 'season_number': 2, 'order': summary['order'], 'revision': summary['revision']}})
        self.assertEqual(response.status_code, 200, response.text)
        reopened = response.json()['series_editor']['seasons'][-1]
        self.assertFalse(reopened['completed'])
        self.assertEqual([ep['watched'] for ep in reopened['episodes']], [True, False])

    async def test_completed_season_hides_late_progress_until_its_finish_date_moves(self):
        entry_url = f'/tracking/entry/{self.series_id}'
        editor_url = f'{entry_url}/series-editor'
        today = date.today()
        completion_day, watch_day = today - timedelta(days=7), today - timedelta(days=3)
        last_episode = self.episode_ids[-1]
        async with self.Session() as db:
            series = await db.get(Media, self.series_id)
            series.tmdb_data = {**series.tmdb_data, 'tracking_episode_details_version': 1,
                'tracking_catalogue_refreshed_at': datetime.now().astimezone().isoformat()}
            episode = await db.get(Media, last_episode)
            episode.release_date = (today + timedelta(days=1)).isoformat()
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            entry.status, entry.progress = 'watching', 2
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids[-2:])))
            await db.commit()

        summary = (await self.client.get(editor_url)).json()
        response = await self.client.patch(entry_url, json={'series_editor': {
            'action': 'complete_season', 'season_number': 2, 'order': summary['order'],
            'revision': summary['revision'], 'watch_date': completion_day.isoformat()}})
        self.assertEqual(response.status_code, 200, response.text)
        async with self.Session() as db:
            episode = await db.get(Media, last_episode)
            episode.release_date = watch_day.isoformat()
            extra = Media(title='2.3', media_type=MediaType.episode, show_id=episode.show_id,
                          tmdb_id=923023, season_number=2, episode_number=3, release_date=watch_day.isoformat())
            db.add(extra)
            await db.flush()
            extra_id = extra.id
            series = await db.get(Media, self.series_id)
            series.tmdb_data = {**series.tmdb_data, 'seasons': [
                {'season_number': 1, 'episode_count': 2}, {'season_number': 2, 'episode_count': 3}]}
            await db.commit()

        class ProgressClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(watch_day.year, watch_day.month, watch_day.day, 12, tzinfo=tz)

        summary = (await self.client.get(editor_url)).json()
        with patch('core.activity.datetime', ProgressClock):
            response = await self.client.patch(entry_url, json={'series_editor': {
                'action': 'position', 'season_number': 2, 'episode_count': 2, 'episode_id': last_episode,
                'order': summary['order'], 'revision': summary['revision'], 'watch_date': watch_day.isoformat()}})
        self.assertEqual(response.status_code, 200, response.text)
        summary = response.json()['series_editor']
        current = summary['seasons'][-1]
        self.assertEqual([ep['watched'] for ep in current['episodes']], [True, True, False])
        async with self.Session() as db:
            cards = list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id == self.series_id)))
            completion = next(card for card in cards if card.payload.get('season_number') == 2)
            completion_id = completion.id
            self.assertEqual(completion.created_at.date(), completion_day)
            self.assertIn([last_episode], [item['progress_episode_ids'] for item in completion.payload['absorbed_progress']])
            self.assertFalse(any(last_episode in card.payload.get('progress_episode_ids', []) for card in cards))
            watch = await db.scalar(select(WatchEvent).where(WatchEvent.media_id == last_episode))
            original_watch = (watch.watched_at, watch.date_inferred)

        async def move_completion(day):
            result = await self.client.patch(entry_url, json={'series_editor': {
                'order': summary['order'], 'revision': summary['revision'],
                'seasons': [{'season_number': 2, 'membership': current['membership'],
                             'completion_date': day.isoformat()}]}})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['series_editor']['seasons'][-1]['completion_date'], day.isoformat())

        await move_completion(today)
        async with self.Session() as db:
            completion = await db.get(TrackingActivity, completion_id)
            self.assertEqual(completion.created_at.date(), today)
            cards = list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id == self.series_id)))
            progress = next(card for card in cards if card.payload.get('progress_episode_ids') == [last_episode])
            media = await db.get(Media, self.series_id)
            self.assertEqual(activity_data([(progress, media)])[0]['payload']['episodes_watched'], 1)
            self.assertEqual(progress.created_at.date(), watch_day)
            watch = await db.scalar(select(WatchEvent).where(WatchEvent.media_id == last_episode))
            self.assertEqual((watch.watched_at, watch.date_inferred), original_watch)
            self.assertIsNone(await db.scalar(select(WatchEvent).where(WatchEvent.media_id == extra_id)))

        await move_completion(completion_day)
        async with self.Session() as db:
            cards = list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id == self.series_id)))
            self.assertFalse(any(last_episode in card.payload.get('progress_episode_ids', []) for card in cards))

    async def test_editor_displays_released_total_for_placeholder_schedule(self):
        url = f'/tracking/entry/{self.series_id}/series-editor'
        profile = f'/tracking/profile/{self.viewer.username}/series'
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        async with self.Session() as db:
            series = await db.get(Media, self.series_id)
            series.status = 'Returning Series'
            series.tmdb_data = {**series.tmdb_data, 'seasons': [
                {'season_number': 1, 'episode_count': 2}, {'season_number': 2, 'episode_count': 3}],
                'tracking_episode_ids': [923011, 923012, 923021, 923022, 923023]}
            tracked = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series_id))
            tracked.status, tracked.progress = 'watching', 3
            future = await db.get(Media, self.episode_ids[-1])
            future.title, future.release_date = 'Episode 2', tomorrow
            db.add(Media(title='Episode 3', media_type=MediaType.episode, show_id=future.show_id,
                tmdb_id=923023, season_number=2, episode_number=3, release_date=tomorrow))
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id == future.id))
            await db.commit()
        response = await self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        first, current = response.json()['seasons']
        self.assertNotIn('progress_total', first)
        self.assertEqual((current['total'], current['progress_total'], len(current['episodes'])), (3, 1, 1))
        active_id = season_row_id(self.series_id, 2)
        rows = (await self.client.get(f'{profile}/rows', params=[('ids', active_id)])).json()
        self.assertEqual((rows[0]['progress'], rows[0]['list_season']['total']), (1, 1))
        page = (await self.client.get(f'{profile}/list')).json()
        active = next(row for row in page['results'] if row.get('list_id') == active_id)
        self.assertEqual(active['list_season']['total'], 1)
        seasons = (await self.client.get(f'{profile}/{self.series_id}/seasons')).json()['seasons']
        self.assertEqual((seasons[-1]['total'], seasons[-1]['completed']), (1, False))

        async with self.Session() as db:
            series = await db.get(Media, self.series_id)
            series.tmdb_data = {**series.tmdb_data, 'next_episode_to_air': {
                'season_number': 2, 'episode_number': 3, 'episode_type': 'finale'}}
            await db.commit()
        self.assertNotIn('progress_total', (await self.client.get(url)).json()['seasons'][-1])
        rows = (await self.client.get(f'{profile}/rows', params=[('ids', active_id)])).json()
        self.assertEqual(rows[0]['list_season']['total'], 3)

        async with self.Session() as db:
            series = await db.get(Media, self.series_id)
            series.tmdb_data = {key: value for key, value in series.tmdb_data.items() if key != 'next_episode_to_air'}
            future = await db.get(Media, self.episode_ids[-1])
            future.title, future.runtime = 'An announced episode', 54
            await db.commit()
        self.assertNotIn('progress_total', (await self.client.get(url)).json()['seasons'][-1])

        async with self.Session() as db:
            future = await db.get(Media, self.episode_ids[-1])
            future.title, future.runtime, future.release_date = 'Episode 2', None, None
            released = await db.scalar(select(Media).where(Media.tmdb_id == 923023))
            released.release_date = date.today().isoformat()
            await db.commit()
        self.assertNotIn('progress_total', (await self.client.get(url)).json()['seasons'][-1])

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

    async def test_equal_progress_season_boundary_is_saved_and_only_advancing_starts_next_season(self):
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids)))
            entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
            entry.status,entry.progress,entry.start_date,entry.finish_date='planning',0,None,None
            await db.commit()
        root=f'/tracking/entry/{self.series_id}'
        async def position(sn,count):
            summary=(await self.client.get(root+'/series-editor')).json()
            return await self.client.patch(root,json={'series_editor':{
                'revision':summary['revision'],'order':summary['order'],'action':'position',
                'episode_id':self.episode_ids[1],'season_number':sn,'episode_count':count}})
        first=await position(1,2)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['progress'],2)
        self.assertEqual(first.json()['series_editor']['selected_position']['season_number'],1)
        async with self.Session() as db:
            cards=list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id)))
            self.assertFalse(any(c.payload.get('started_watching') for c in cards))
            milestone=next(c for c in cards if c.payload.get('season_completion_id'))
            milestone_id=milestone.id
            watches=[(w.id,w.watched_at) for w in await db.scalars(select(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids)).order_by(WatchEvent.id))]
        advanced=await position(2,0)
        self.assertEqual(advanced.status_code,200,advanced.text)
        self.assertEqual(advanced.json()['progress'],2)
        self.assertEqual(advanced.json()['series_editor']['seasons'][1]['start_date'],date.today().isoformat())
        self.assertNotEqual(first.json()['series_editor']['revision'],advanced.json()['series_editor']['revision'])
        saved=(await self.client.get(root+'/series-editor')).json()['selected_position']
        self.assertEqual((saved['season_number'],saved['episode_count']),(2,0))
        self.assertEqual((await position(2,0)).status_code,200)
        async with self.Session() as db:
            cards=list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id)))
            self.assertEqual([c.id for c in cards if c.payload.get('season_completion_id')],[milestone_id])
            starts=[c for c in cards if c.payload.get('started_watching')]
            self.assertEqual(len(starts),1)
            self.assertEqual(starts[0].payload['season_number'],2)
            self.assertEqual(watches,[(w.id,w.watched_at) for w in await db.scalars(select(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids)).order_by(WatchEvent.id))])
        for status in ('dropped','planning','paused'):
            response=await self.client.patch(root,json={'status':status})
            self.assertEqual(response.status_code,200,response.text)
            async with self.Session() as db:
                card=await db.scalar(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id,
                    TrackingActivity.payload['season_status'].as_boolean().is_(True)))
                self.assertEqual((card.status,card.payload['season_number']),(status,2))

    async def test_cursor_does_not_hide_provider_progress_and_rejects_mismatched_position(self):
        root=f'/tracking/entry/{self.series_id}'
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids[2:])))
            entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
            entry.progress=2
            entry.season_cursor={'order':'tmdb:aired','season_number':1,'episode_count':2,'episode_id':self.episode_ids[1]}
            await db.commit()
        summary=(await self.client.get(root+'/series-editor')).json()
        invalid=await self.client.patch(root,json={'series_editor':{
            'order':summary['order'],'revision':summary['revision'],'action':'position',
            'season_number':2,'episode_count':0,'episode_id':self.episode_ids[2]}})
        self.assertEqual(invalid.status_code,422,invalid.text)
        async with self.Session() as db:
            db.add(WatchEvent(user_id=self.user_id,media_id=self.episode_ids[2],completed=True,watched_at=datetime.now()))
            await db.commit()
        updated=(await self.client.get(root+'/series-editor')).json()
        self.assertIsNone(updated['selected_position'])
        response=await self.client.patch(root,json={'status':'paused'})
        self.assertEqual(response.status_code,200,response.text)
        async with self.Session() as db:
            card=await db.scalar(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id,
                TrackingActivity.payload['season_status'].as_boolean().is_(True)))
            self.assertEqual(card.payload['season_number'],2)
