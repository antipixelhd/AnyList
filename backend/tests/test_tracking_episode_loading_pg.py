"""Cold primary reads, shared episode acquisition and cached progress stay independent."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
import test_statistics_pg as fixture
from core import tracking_episode_loading as loading
from models import Media, Show, TrackedEntry, User, UserProfileData, WatchEvent
from core.tracking_episode_persistence import persist_tmdb_episodes
from models.base import MediaType, PrivacyLevel
from models.users import UserSettings
from dependencies import get_current_user


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable PostgreSQL')
class EpisodeLoadingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)
        self.app.dependency_overrides[get_current_user] = lambda: self.viewer
        self.raw = {'id': 987123, 'name': 'Background series', 'overview': 'Immediate overview',
            'status': 'Ended', 'first_air_date': '2020-01-01', 'genres': [],
            'external_ids': {'imdb_id': 'tt9871230'},
            'number_of_seasons': 3, 'number_of_episodes': 6,
            'seasons': [{'season_number': n, 'episode_count': 2} for n in range(1, 4)]}
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.calls = []
        self.bad = False

        async def season(native, number, **kwargs):
            self.calls.append(number)
            self.started.set()
            await self.release.wait()
            return {'episodes': [] if self.bad else [
                {'id': 9871230 + number * 10 + n, 'episode_number': n,
                 'name': f'Episode {number}.{n}', 'runtime': 45, 'air_date': '2020-01-01'} for n in (1, 2)]}

        self.show_patch = patch('core.tmdb.get_show', AsyncMock(return_value=self.raw))
        self.season_patch = patch('core.tmdb.get_season', side_effect=season)
        self.show_patch.start()
        self.season_patch.start()
        async with self.Session() as db:
            db.add(UserSettings(user_id=self.user_id, tmdb_api_key='fixture-only'))
            await db.commit()
        response = await self.client.post('/tracking/catalog/series/987123')
        self.assertEqual(response.status_code, 200, response.text)
        self.series = response.json()['id']

    async def asyncTearDown(self):
        self.release.set()
        await loading.close()
        self.show_patch.stop()
        self.season_patch.stop()
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def test_primary_does_not_acquire_episodes_and_tab_receives_complete_catalogue_later(self):
        self.assertEqual(self.calls, [])
        self.release.clear()
        pending = asyncio.create_task(self.client.get(f'/tracking/title/{self.series}/seasons'))
        try:
            await asyncio.wait_for(self.started.wait(), 2)
            primary = await asyncio.wait_for(self.client.get(
                f'/tracking/title/{self.series}?episodes=false'), 1)
            data = primary.json()
            self.assertEqual(data['overview'], 'Immediate overview')
            self.assertFalse(data['episodes_loaded'])
            self.assertTrue(all(not row['episodes'] and row['released_count'] is None for row in data['seasons']))
            self.assertFalse(pending.done())
        finally:
            self.release.set()
        data = (await pending).json()
        self.assertEqual(data['season_status'], {'state': 'ready', 'ready': True, 'pending': False})
        self.assertEqual([len(row['episodes']) for row in data['seasons']], [2, 2, 2])
        self.assertEqual(sorted(self.calls), [1, 2, 3])
        await self.client.get(f'/tracking/title/{self.series}/seasons')
        self.assertEqual(len(self.calls), 3)

    async def test_cancelled_subscriber_does_not_cancel_the_other_page(self):
        self.release.clear()
        first = asyncio.create_task(self.client.get(f'/tracking/title/{self.series}/seasons'))
        await asyncio.wait_for(self.started.wait(), 2)
        second = asyncio.create_task(self.client.get(f'/tracking/title/{self.series}/seasons'))
        await asyncio.sleep(.02)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.release.set()
        self.assertTrue((await second).json()['season_status']['ready'])
        self.assertEqual(sorted(self.calls), [1, 2, 3])

    async def test_early_progress_save_joins_acquisition_without_holding_the_account_lock(self):
        self.release.clear()
        with (patch('core.local_outbound.dispatch_local_tracking_delta', AsyncMock()),
                patch('core.tmdb.get_external_ids', AsyncMock(return_value={'imdb_id': 'tt9871230'}))):
            pending = asyncio.create_task(self.client.patch(f'/tracking/entry/{self.series}', json={'progress': 3}))
            try:
                await asyncio.wait_for(self.started.wait(), 2)
                self.assertFalse(pending.done())
                async with self.Session() as db:
                    await db.scalar(select(User).where(User.id == self.user_id).with_for_update(nowait=True))
                    self.assertIsNone(await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == self.series)))
            finally:
                self.release.set()
            response = await pending
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['progress'], 3)
        self.assertEqual(response.json()['released_episodes'], 6)
        self.assertEqual(sorted(self.calls), [1, 2, 3])

    async def test_shutdown_releases_owned_job_without_a_failure_or_partial_catalogue(self):
        self.release.clear()
        pending = asyncio.create_task(self.client.get(f'/tracking/title/{self.series}/seasons'))
        await asyncio.wait_for(self.started.wait(), 2)
        await loading.close()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        async with self.Session() as db:
            row = await db.scalar(loading.query(self.series))
            self.assertIsNone(row.lease_token)
            self.assertIsNone(row.next_attempt_at)
            self.assertIsNone(row.error_code)
            self.assertNotIn('tracking_catalogue_refreshed_at', (await db.get(Media, self.series)).tmdb_data)

    async def test_independent_worker_reuses_lease_and_expired_owner_cannot_publish(self):
        for ownership in ('stolen', 'expired'):
            self.release.clear()
            self.started.clear()
            task = asyncio.create_task(loading._load(self.engine, self.series, self.user_id))
            try:
                await asyncio.wait_for(self.started.wait(), 2)
                other = await asyncio.wait_for(loading._load(self.engine, self.series, self.user_id), 1)
                self.assertTrue(other['pending'])
                async with self.Session() as db:
                    row = await db.scalar(loading.query(self.series))
                    if ownership == 'stolen':
                        row.lease_token = 'replacement-owner'
                    else:
                        row.lease_until = loading.now()
                    await db.commit()
            finally:
                self.release.set()
            self.assertTrue((await task)['pending'])
            async with self.Session() as db:
                media = await db.get(Media, self.series)
                self.assertNotIn('tracking_catalogue_refreshed_at', media.tmdb_data)
                self.assertEqual(await db.scalar(select(func.count()).select_from(Media).where(
                    Media.media_type == MediaType.episode)), 0)
                row = await db.scalar(loading.query(self.series))
                row.lease_token = None
                row.lease_until = None
                await db.commit()

    async def test_failed_refresh_keeps_cached_episodes_and_durable_retry_cooldown(self):
        first = (await self.client.get(f'/tracking/title/{self.series}/seasons')).json()
        async with self.Session() as db:
            media = await db.get(Media, self.series)
            media.tmdb_data = {**media.tmdb_data, 'tracking_catalogue_refreshed_at': '2000-01-01T00:00:00+00:00'}
            await db.commit()
        self.bad = True
        response = (await self.client.get(f'/tracking/title/{self.series}/seasons')).json()
        self.assertTrue(response['season_status']['ready'])
        self.assertEqual(response['seasons'], first['seasons'])
        tasks = list(loading._flights.values())
        await asyncio.gather(*tasks)
        calls = len(self.calls)
        retried = (await self.client.get(f'/tracking/title/{self.series}/seasons')).json()
        self.assertTrue(retried['season_status']['ready'])
        self.assertEqual(retried['season_status']['error'], 'episode_metadata_unavailable')
        self.assertEqual(retried['seasons'], first['seasons'])
        self.assertEqual(len(self.calls), calls)
        async with self.Session() as db:
            row = await db.scalar(loading.query(self.series))
            self.assertIsNone(row.lease_token)
            self.assertGreater(row.next_attempt_at, loading.now())

    async def test_stale_refresh_revision_waits_for_publication_instead_of_polling(self):
        await self.client.get(f'/tracking/title/{self.series}/seasons')
        revision = '2000-01-01T00:00:00+00:00'
        async with self.Session() as db:
            media = await db.get(Media, self.series)
            media.tmdb_data = {**media.tmdb_data, 'tracking_catalogue_refreshed_at': revision}
            await db.commit()
        self.release.clear()
        self.started.clear()
        first = (await self.client.get(f'/tracking/title/{self.series}/seasons')).json()
        self.assertTrue(first['season_status']['ready'])
        self.assertTrue(first['season_status']['waitable'])
        await asyncio.wait_for(self.started.wait(), 2)
        next_read = asyncio.create_task(self.client.get(f'/tracking/title/{self.series}/seasons', params={'revision': revision}))
        try:
            await asyncio.sleep(.02)
            self.assertFalse(next_read.done())
        finally:
            self.release.set()
        data = (await asyncio.wait_for(next_read, 1)).json()
        self.assertFalse(data['season_status']['pending'])
        self.assertNotEqual(data['catalogue_revision'], revision)

    async def test_batch_refresh_keeps_ids_private_history_and_recovered_metadata(self):
        data = (await self.client.get(f'/tracking/title/{self.series}/seasons')).json()
        ids = [episode['id'] for season in data['seasons'] for episode in season['episodes']]
        async with self.Session() as db:
            episode = await db.get(Media, ids[0])
            show_id = episode.show_id
            episode.runtime = 52
            episode.poster_path = '/saved.jpg'
            episode.overview = 'Recovered description'
            episode.tmdb_data = {'keep': 'source evidence'}
            episode.tvdb_id = 445566
            db.add(WatchEvent(user_id=self.user_id, media_id=episode.id, completed=True))
            await db.commit()
            rows = list(await db.scalars(select(Media).where(Media.id.in_(ids)).order_by(Media.season_number, Media.episode_number)))
            await persist_tmdb_episodes(db, show_id, [(row.season_number, {'id': row.tmdb_id,
                'episode_number': row.episode_number, 'name': 'Updated episode', 'runtime': 0}) for row in rows])
            await db.commit()
            await db.refresh(episode)
            self.assertEqual(episode.id, ids[0])
            self.assertEqual((episode.runtime, episode.poster_path, episode.overview), (52, '/saved.jpg', 'Recovered description'))
            self.assertEqual(episode.tvdb_id, 445566)
            self.assertEqual(episode.tmdb_data, {'keep': 'source evidence'})
            self.assertEqual(await db.scalar(select(WatchEvent.media_id).where(WatchEvent.media_id == ids[0])), ids[0])
            # A previously loaded ORM object cannot hide another writer's
            # corrected identity position from the locked validation read.
            async with self.Session() as other:
                changed = await other.get(Media, ids[0])
                changed.season_number = 99
                await other.commit()
            with self.assertRaisesRegex(ValueError, 'numbering changed'):
                await persist_tmdb_episodes(db, show_id, [(1, {'id': rows[0].tmdb_id, 'episode_number': 1})])
            await db.rollback()

    async def test_batch_insert_race_reuses_identity_and_rejects_changed_numbering(self):
        async with self.Session() as db:
            show = Show(tmdb_id=667788, title='Race fixture', canonical_source='tmdb')
            db.add(show)
            await db.commit()
            show_id = show.id
        for native, position in ((6677881, (1, 1)), (6677882, (9, 9))):
            async with self.Session() as db:
                original = db.scalars
                raced = False
                async def interleave(statement, *args, **kwargs):
                    nonlocal raced
                    if getattr(statement, 'is_insert', False) and not raced:
                        raced = True
                        async with self.Session() as other:
                            episode = Media(tmdb_id=native, media_type=MediaType.episode, show_id=show_id,
                                season_number=position[0], episode_number=position[1], title='Concurrent import')
                            other.add(episode)
                            await other.flush()
                            other.add(WatchEvent(user_id=self.user_id, media_id=episode.id, completed=True))
                            await other.commit()
                    return await original(statement, *args, **kwargs)
                with patch.object(db, 'scalars', side_effect=interleave):
                    if position == (1, 1):
                        await persist_tmdb_episodes(db, show_id, [(1, {'id': native, 'episode_number': 1, 'name': 'Updated'})])
                        await db.commit()
                    else:
                        with self.assertRaisesRegex(ValueError, 'numbering changed'):
                            await persist_tmdb_episodes(db, show_id, [(1, {'id': native, 'episode_number': 1})])
                        await db.rollback()
            async with self.Session() as db:
                rows = list(await db.scalars(select(Media).where(Media.tmdb_id == native)))
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0].season_number, rows[0].episode_number), position)
                self.assertEqual(await db.scalar(select(WatchEvent.media_id).where(WatchEvent.media_id == rows[0].id)), rows[0].id)

    async def test_large_batches_are_complete_and_duplicate_evidence_is_rejected(self):
        async with self.Session() as db:
            show = Show(tmdb_id=776655, title='Long series', canonical_source='tmdb')
            db.add(show)
            await db.commit()
            rows = [(1, {'id': 7760000 + n, 'episode_number': n}) for n in range(1, 502)]
            await persist_tmdb_episodes(db, show.id, rows)
            await db.commit()
            self.assertEqual(await db.scalar(select(func.count()).select_from(Media).where(Media.show_id == show.id)), 501)
            for bad in ([rows[0], rows[0]], [rows[0], (1, {'id': 8888888, 'episode_number': 1})]):
                with self.assertRaisesRegex(ValueError, 'duplicate'):
                    await persist_tmdb_episodes(db, show.id, bad)

    async def test_cached_list_progress_is_present_without_episode_rows_and_watch_state_is_private(self):
        loaded = (await self.client.get(f'/tracking/title/{self.series}/seasons')).json()
        episode = loaded['seasons'][1]['episodes'][0]['id']
        async with self.Session() as db:
            db.add(TrackedEntry(user_id=self.user_id, media_id=self.series, status='watching', progress=3,
                rating_mode='manual', manual_score=7, notes='Keep my note'))
            db.add(WatchEvent(user_id=self.user_id, media_id=episode, completed=True))
            other = User(username='episode-other', email='episode-other@example.test', api_key='other-fixture')
            db.add(other)
            await db.flush()
            db.add(UserProfileData(user_id=other.id, privacy_level=PrivacyLevel.public))
            await db.commit()
        light = (await self.client.get(f'/tracking/title/{self.series}?episodes=false')).json()
        self.assertEqual(light['entry']['progress'], 3)
        self.assertEqual(light['entry']['released_episodes'], 6)
        self.assertEqual(light['entry']['season_position'], 'S2E1')
        self.assertEqual(light['entry']['notes'], 'Keep my note')
        self.assertEqual(light['progress_positions'][-1], 'S3E2')
        self.assertFalse(any(row['episodes'] for row in light['seasons']))
        editor = (await self.client.get(f'/tracking/editor/{self.series}')).json()
        self.assertEqual(editor['entry']['released_episodes'], 6)
        private = (await self.client.get(f'/tracking/title/{self.series}/seasons')).json()
        self.assertTrue(private['seasons'][1]['episodes'][0]['watched'])
        self.viewer = other
        public = (await self.client.get(f'/tracking/title/{self.series}/seasons')).json()
        self.assertIsNone(public['entry'])
        self.assertFalse(any(ep['watched'] for row in public['seasons'] for ep in row['episodes']))
        self.viewer = None
        self.assertEqual((await self.client.get(f'/tracking/title/{self.series}/seasons')).status_code, 401)
