"""Inbound Continue Watching removals use real snapshot queries and queues."""
import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from models import Media, MediaType, MediaServerConnection, User
from models.base import Base
from models.tracking import StreamAction, StreamBaseline, SyncReview, TrackedEntry, TrackingPreferences
from core.tracking_snapshot import observe_stream_snapshot


@compiles(JSONB, 'sqlite')
def _jsonb_as_json(type_, compiler, **kw):
    return 'JSON'


class NuvioRemovalSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.addAsyncCleanup(self.engine.dispose)
        self.addAsyncCleanup(self.db.close)
        user = User(username='fixture', email='fixture@example.test', api_key='fixture')
        self.media = Media(title='Lioness', tmdb_id=42, imdb_id='tt0000042', media_type=MediaType.series)
        self.db.add_all([user, self.media])
        await self.db.flush()
        self.user_id = user.id
        self.conn = MediaServerConnection(user_id=user.id, type='nuvio', name='Source',
            url='https://example.test', token='fixture', push_watched=True, push_playback=False)
        self.db.add(self.conn)
        await self.db.flush()
        self.entry = TrackedEntry(user_id=user.id, media_id=self.media.id, status='watching')
        self.baseline = StreamBaseline(user_id=user.id, connection_id=self.conn.id, approved=True,
            observed_at=datetime.now() + timedelta(seconds=1), snapshot={
                'mappings': {'tt0000042': 42}, 'progress': {}, 'watched': [],
                'records': {'library': [], 'watched': [], 'progress': []},
                'nuvio_visibility': {'tv': [], 'mobile': []},
            })
        self.db.add_all([self.entry, self.baseline])
        await self.db.commit()

    async def observe(self, visibility=None, *, progress=None, watched=None, complete=True):
        await observe_stream_snapshot(self.db, self.conn, [], watched or [], progress or [], {},
            cw_visibility=visibility, complete=complete)
        await self.db.commit()

    async def reviews(self):
        return (await self.db.execute(select(SyncReview).where(
            SyncReview.kind == 'playback_removed'))).scalars().all()

    async def test_tv_dismissal_creates_review_and_mirrors_source_and_other_connections(self):
        target = MediaServerConnection(user_id=self.user_id, type='nuvio', name='Target',
            url='https://example.test', token='fixture', push_watched=True, push_playback=True)
        self.db.add(target)
        await self.db.flush()
        self.db.add(StreamBaseline(user_id=self.user_id, connection_id=target.id, approved=True,
            snapshot={'mappings': {'tt0000042': 42}, 'progress': {}}))
        await self.db.commit()
        await self.observe({'tv': ['tt0000042'], 'mobile': []})
        self.assertEqual(self.entry.status, 'paused')
        reviews = await self.reviews()
        self.assertEqual([(r.state, r.proposed_status) for r in reviews], [('pending', 'paused')])
        actions = (await self.db.execute(select(StreamAction))).scalars().all()
        self.assertEqual({a.connection_id for a in actions}, {self.conn.id, target.id})
        self.assertTrue(next(a for a in actions if a.connection_id == self.conn.id).payload['visibility_only'])
        self.assertFalse(next(a for a in actions if a.connection_id == target.id).payload.get('visibility_only', False))
        await self.observe({'tv': ['tt0000042'], 'mobile': []})
        self.assertEqual(len(await self.reviews()), 1)

    async def test_mobile_and_tv_dismissals_deduplicate_and_auto_confirm(self):
        self.db.add(TrackingPreferences(user_id=self.user_id, auto_confirm=True))
        await self.db.commit()
        await self.observe({'tv': ['tt0000042'], 'mobile': ['tt0000042|2|5']})
        self.assertEqual(self.entry.status, 'paused')
        self.assertEqual([(r.state, r.proposed_status) for r in await self.reviews()], [('confirmed', 'paused')])
        self.assertEqual(self.baseline.snapshot['nuvio_visibility']['mobile'], ['tt0000042|2|5'])

    async def test_mobile_only_dismissal_is_detected(self):
        await self.observe({'tv': [], 'mobile': ['tt0000042|2|5']})
        self.assertEqual(self.entry.status, 'paused')
        self.assertEqual(len(await self.reviews()), 1)

    async def test_first_visibility_observation_does_not_infer_historical_removal(self):
        snapshot = dict(self.baseline.snapshot)
        snapshot.pop('nuvio_visibility')
        self.baseline.snapshot = snapshot
        await self.db.commit()
        await self.observe({'tv': ['tt0000042'], 'mobile': ['tt0000042|2|5']})
        self.assertEqual(self.entry.status, 'watching')
        self.assertEqual(await self.reviews(), [])

    async def test_missing_platform_preserves_baseline_and_partial_pull_detects_nothing(self):
        self.baseline.snapshot = {**self.baseline.snapshot,
            'nuvio_visibility': {'tv': ['other-title'], 'mobile': []}}
        await self.db.commit()
        await self.observe({'mobile': []})
        self.assertEqual(self.baseline.snapshot['nuvio_visibility']['tv'], ['other-title'])
        await self.observe({'tv': ['tt0000042'], 'mobile': []}, complete=False)
        self.assertEqual(self.entry.status, 'watching')
        self.assertEqual(await self.reviews(), [])

    async def test_own_dismissal_echo_is_acknowledged_without_review(self):
        self.baseline.snapshot = {**self.baseline.snapshot,
            'cw_visibility_echo': {'tv': {'tt0000042': True}, 'mobile': {'tt0000042|2|5': True}}}
        await self.db.commit()
        await self.observe({'tv': ['tt0000042'], 'mobile': ['tt0000042|2|5']})
        self.assertEqual(self.entry.status, 'watching')
        self.assertEqual(await self.reviews(), [])
        self.assertFalse(any(self.baseline.snapshot.get('cw_visibility_echo', {}).values()))

    async def test_progress_removal_on_pull_only_nuvio_creates_review(self):
        row = {'content_id': 'tt0000042', 'content_type': 'series',
            'season': 1, 'episode': 2, 'position': 30, 'duration': 100}
        self.baseline.snapshot = {**self.baseline.snapshot, 'progress': {'tt0000042': row}}
        await self.db.commit()
        await self.observe({'tv': [], 'mobile': []})
        self.assertEqual(self.entry.status, 'paused')
        self.assertEqual(len(await self.reviews()), 1)

    async def test_matching_completion_does_not_infer_progress_removal(self):
        row = {'content_id': 'tt0000042', 'content_type': 'series',
            'season': 1, 'episode': 2, 'position': 30, 'duration': 100}
        self.baseline.snapshot = {**self.baseline.snapshot, 'progress': {'tt0000042': row}}
        await self.db.commit()
        await self.observe({'tv': [], 'mobile': []}, watched=[{**row, 'watched_at': '2020-01-01'}])
        self.assertEqual(await self.reviews(), [])

    async def test_dismissal_does_not_reinterpret_a_non_watching_title(self):
        self.entry.status = 'planning'
        await self.db.commit()
        await self.observe({'tv': ['tt0000042'], 'mobile': []})
        self.assertEqual(self.entry.status, 'planning')
        self.assertEqual(await self.reviews(), [])

    async def test_dismissal_with_stable_resume_still_pauses_title(self):
        row = {'content_id': 'tt0000042', 'content_type': 'series',
            'season': 1, 'episode': 2, 'position': 30, 'duration': 100}
        self.baseline.snapshot = {**self.baseline.snapshot,
            'progress': {'tt0000042': row}, 'records': {'library': [], 'watched': [], 'progress': [row]}}
        await self.db.commit()
        await self.observe({'tv': ['tt0000042'], 'mobile': []}, progress=[row])
        self.assertEqual(self.entry.status, 'paused')
        self.assertEqual(len(await self.reviews()), 1)

    async def test_next_up_dismissal_with_existing_episode_history_is_detected(self):
        watched = {'content_id': 'tt0000042', 'content_type': 'series',
            'season': 1, 'episode': 4, 'watched_at': '2020-01-01'}
        self.baseline.snapshot = {**self.baseline.snapshot,
            'watched': ['tt0000042:1:4'],
            'records': {'library': [], 'watched': [watched], 'progress': []}}
        await self.db.commit()
        await self.observe({'tv': ['tt0000042'], 'mobile': []}, watched=[watched])
        self.assertEqual(self.entry.status, 'paused')
        self.assertEqual(len(await self.reviews()), 1)
        self.assertEqual(self.baseline.snapshot['records']['watched'], [watched])

    async def test_progress_and_settings_removal_create_one_review(self):
        row = {'content_id': 'tt0000042', 'content_type': 'series',
            'season': 1, 'episode': 2, 'position': 30, 'duration': 100}
        self.baseline.snapshot = {**self.baseline.snapshot, 'progress': {'tt0000042': row}}
        await self.db.commit()
        await self.observe({'tv': ['tt0000042'], 'mobile': ['tt0000042|1|1']})
        self.assertEqual(self.entry.status, 'paused')
        self.assertEqual(len(await self.reviews()), 1)

    async def test_newer_local_status_edit_creates_conflict_without_propagation(self):
        self.entry.status_changed_at = self.baseline.observed_at + timedelta(seconds=1)
        await self.db.commit()
        await self.observe({'tv': ['tt0000042'], 'mobile': []})
        self.assertEqual(self.entry.status, 'watching')
        reviews = (await self.db.execute(select(SyncReview))).scalars().all()
        self.assertEqual([(r.kind, r.state) for r in reviews], [('conflict', 'pending')])
        self.assertEqual((await self.db.execute(select(StreamAction))).scalars().all(), [])

    async def test_same_pull_playback_update_is_not_mistaken_for_newer_local_edit(self):
        row = {'content_id': 'tt0000042', 'content_type': 'series',
            'season': 1, 'episode': 2, 'position': 30, 'duration': 100,
            'last_watched': '2020-01-01T00:00:00Z'}
        self.baseline.snapshot = {**self.baseline.snapshot, 'progress': {'tt0000042': row}}
        self.db.add(TrackingPreferences(user_id=self.user_id, auto_confirm=True))
        await self.db.commit()
        changed = {**row, 'position': 40,
            'last_watched': (self.baseline.observed_at + timedelta(seconds=1)).isoformat() + 'Z'}
        await self.observe({'tv': ['tt0000042'], 'mobile': []}, progress=[changed])
        self.assertEqual(self.entry.status, 'paused')
        self.assertEqual([(r.state, r.proposed_status) for r in await self.reviews()], [('confirmed', 'paused')])
