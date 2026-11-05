"""Automatic list admission stays independent of accepted resume delivery."""
import os
import unittest
from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from models import User, UserSettings, Media, Show, MediaServerConnection, PlaybackProgress, WatchEvent
from models.base import MediaType
from models.tracking import TrackedEntry, StreamBaseline, StreamAction, TrackingDeletion
from core.tracking_import import import_tracking_history
from core.sync_reconciliation import Reconciliation, _current, _playback
from core.pull_cycle import PullCycleState
from core.watch_state_policy import conservative_watch_state


@unittest.skipUnless(os.getenv('TRACKING_TEST_DATABASE_URL'), 'Requires disposable PostgreSQL')
class WatchStatePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ['TRACKING_TEST_DATABASE_URL'])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.db = AsyncSession(bind=self.connection, expire_on_commit=False, join_transaction_mode='create_savepoint')
        key = uuid4().hex
        self.user = User(username=key, email=f'{key}@test.invalid', api_key=key)
        self.db.add(self.user)
        await self.db.flush()
        self.settings = UserSettings(user_id=self.user.id, preferences={})
        self.show = Show(tmdb_id=91002, title='Sample series')
        self.movie = Media(tmdb_id=91001, media_type=MediaType.movie, title='Sample movie')
        self.series = Media(tmdb_id=91002, media_type=MediaType.series, title='Sample series')
        self.db.add_all([self.settings, self.show, self.movie, self.series])
        await self.db.flush()
        self.episode = Media(show_id=self.show.id, media_type=MediaType.episode, title='Later episode',
            season_number=1, episode_number=3, release_date='2020-01-01')
        self.source = MediaServerConnection(user_id=self.user.id, type='stremio', name='Source',
            url='https://invalid', token='fixture', sync_playback=True, push_playback=False)
        self.target = MediaServerConnection(user_id=self.user.id, type='nuvio', name='Peer',
            url='https://invalid', token='fixture', sync_playback=True, push_playback=True)
        self.db.add_all([self.episode, self.source, self.target])
        await self.db.flush()
        self.clock = datetime.utcnow() - timedelta(minutes=1)
        self.mappings = {'tmdb:91001': 91001, 'tmdb:91002': 91002}
        for conn in (self.source, self.target):
            self.db.add(StreamBaseline(connection_id=conn.id, user_id=self.user.id, approved=True,
                snapshot={'mappings': self.mappings}, observed_at=self.clock-timedelta(hours=1)))
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def resume(self, position=20000):
        state = Reconciliation(self.user.id, collecting=False)
        rows = [dict(content_id=key, content_type=kind, position=position, duration=100000,
            modified_at=self.clock.isoformat()+'Z', last_watched=int(self.clock.timestamp()*1000),
            **({'season': 1, 'episode': 3} if kind == 'series' else {}))
            for key, kind in [('tmdb:91001', 'movie'), ('tmdb:91002', 'series')]]
        state.snapshots.append(dict(connection_id=self.source.id, identity_version=self.source.identity_version,
            library=[], watched=[], progress=rows, tmdb_ids=self.mappings,
            complete=True, sync_playback=True, sync_watched=True))
        token = _current.set(state)
        try:
            await _playback(self.db, state, PullCycleState(self.user.id), set())
        finally:
            _current.reset(token)
        await import_tracking_history(self.db, self.user.id)

    async def test_default_conservative_keeps_resume_and_forwards_without_list_entries(self):
        from core.stream_actions import dispatch_stream_actions
        for source_type, target_type in [('stremio', 'nuvio'), ('nuvio', 'stremio')]:
            with self.subTest(source=source_type):
                self.source.type, self.target.type = source_type, target_type
                await self.db.flush()
                await self.resume(20000 if source_type == 'stremio' else 30000)
                self.assertEqual((await self.db.scalars(select(TrackedEntry))).all(), [])
                progress = (await self.db.scalars(select(PlaybackProgress))).all()
                self.assertEqual(len(progress), 2)
                self.assertEqual({row.progress_seconds for row in progress}, {20 if source_type == 'stremio' else 30})
                actions = (await self.db.scalars(select(StreamAction))).all()
                self.assertEqual(len(actions), 2)
                for action in actions:
                    self.assertEqual(action.state, 'pending')
                with patch('core.stream_actions.push_nuvio_progress', AsyncMock()) as nuvio, \
                     patch('core.stream_actions.push_stremio_progress', AsyncMock()) as stremio, \
                     patch('core.stream_actions.refresh_stream_connection', AsyncMock()), \
                     patch('core.tracking_snapshot.require_stream_reconciliation', AsyncMock()):
                    await dispatch_stream_actions(self.db, self.user.id)
                self.assertEqual((nuvio if target_type == 'nuvio' else stremio).await_count, 2)
                self.assertTrue(all(action.state == 'applied' for action in actions))
                for action in actions:
                    await self.db.delete(action)
                await self.db.commit()
        # A later generic import still cannot turn those saved offsets into entries.
        self.assertEqual(await import_tracking_history(self.db, self.user.id), 0)

    async def test_immediate_restores_resume_based_admission(self):
        self.settings.preferences = {'watch_state_sync': 'immediate'}
        await self.db.commit()
        await self.resume()
        entries = (await self.db.scalars(select(TrackedEntry))).all()
        self.assertEqual({entry.media_id for entry in entries}, {self.movie.id, self.series.id})
        self.assertEqual({entry.status for entry in entries}, {'watching'})

    async def test_completed_movie_and_later_episode_still_admit_entries(self):
        await self.resume()
        self.db.add_all([WatchEvent(user_id=self.user.id, media_id=media.id, completed=True, watched_at=self.clock)
            for media in (self.movie, self.episode)])
        await self.db.commit()
        self.assertEqual(await import_tracking_history(self.db, self.user.id), 2)
        entries = {entry.media_id: entry for entry in (await self.db.scalars(select(TrackedEntry))).all()}
        self.assertEqual(entries[self.movie.id].status, 'completed')
        self.assertEqual(entries[self.series.id].status, 'watching')
        self.assertEqual(entries[self.series.id].start_date, self.clock.date())

    async def test_existing_planned_entries_do_not_become_watching_from_offsets(self):
        self.db.add_all([TrackedEntry(user_id=self.user.id, media_id=media.id, status='planning',
            status_changed_at=self.clock-timedelta(hours=2), status_source='local')
            for media in (self.movie, self.series)])
        await self.db.commit()
        await self.resume()
        self.assertEqual({entry.status for entry in (await self.db.scalars(select(TrackedEntry))).all()}, {'planning'})
        self.assertEqual(len((await self.db.scalars(select(StreamAction))).all()), 2)

    async def test_existing_watching_and_series_with_completed_history_keep_syncing(self):
        self.db.add_all([
            TrackedEntry(user_id=self.user.id, media_id=self.movie.id, status='watching',
                status_changed_at=self.clock-timedelta(hours=2)),
            TrackedEntry(user_id=self.user.id, media_id=self.series.id, status='planning',
                status_changed_at=self.clock-timedelta(hours=2)),
            WatchEvent(user_id=self.user.id, media_id=self.episode.id, completed=True, watched_at=self.clock),
        ])
        await self.db.commit()
        await self.resume()
        entries = (await self.db.scalars(select(TrackedEntry))).all()
        self.assertEqual({entry.status for entry in entries}, {'watching'})
        self.assertEqual(len((await self.db.scalars(select(PlaybackProgress))).all()), 2)

    async def test_peer_echo_does_not_admit_sampled_titles_or_queue_a_return_write(self):
        from core.stream_actions import dispatch_stream_actions
        await self.resume()
        with patch('core.stream_actions.push_nuvio_progress', AsyncMock()), \
             patch('core.tracking_snapshot.require_stream_reconciliation', AsyncMock()):
            await dispatch_stream_actions(self.db, self.user.id)
        self.source, self.target = self.target, self.source
        await self.resume()
        self.assertEqual((await self.db.scalars(select(TrackedEntry))).all(), [])
        self.assertEqual((await self.db.scalars(select(StreamAction).where(StreamAction.state == 'pending'))).all(), [])

    async def test_relay_rechecks_source_deletion_and_newer_local_status(self):
        from core.stream_actions import valid_forwarded_progress
        await self.resume()
        action = (await self.db.scalars(select(StreamAction).where(StreamAction.media_id == self.movie.id))).one()
        self.assertTrue(await valid_forwarded_progress(self.db, action, None, None))
        marker = TrackingDeletion(user_id=self.user.id, media_id=self.movie.id)
        self.assertFalse(await valid_forwarded_progress(self.db, action, None, marker))
        entry = TrackedEntry(user_id=self.user.id, media_id=self.movie.id, status='paused',
            status_changed_at=self.clock+timedelta(seconds=1))
        self.assertFalse(await valid_forwarded_progress(self.db, action, entry, None))
        self.source.identity_version += 1
        await self.db.flush()
        self.assertFalse(await valid_forwarded_progress(self.db, action, None, None))
        self.source.identity_version -= 1
        await self.db.flush()
        baseline = await self.db.get(StreamBaseline, self.source.id)
        baseline.snapshot = {'mappings': self.mappings, 'progress': {}}
        await self.db.flush()
        self.assertFalse(await valid_forwarded_progress(self.db, action, None, None))

    async def test_missing_preferences_and_existing_accounts_default_conservative(self):
        self.assertTrue(await conservative_watch_state(self.db, self.user.id))
        self.settings.preferences = {'unrelated': True}
        await self.db.commit()
        self.assertTrue(await conservative_watch_state(self.db, self.user.id))
        await self.db.delete(self.settings)
        await self.db.commit()
        self.assertTrue(await conservative_watch_state(self.db, self.user.id))
