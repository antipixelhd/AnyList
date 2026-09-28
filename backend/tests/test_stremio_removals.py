"""Continue Watching removal follows independently selected sync directions."""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from models import Media, MediaType, MediaServerConnection, User
from models.base import Base
from models.tracking import StreamAction, StreamBaseline, SyncReview, TrackedEntry
from core.tracking_snapshot import observe_stream_snapshot
from core.stream_actions import queue_dismissals, queue_local_dismissals, dispatch_stream_actions


@compiles(JSONB, 'sqlite')
def _jsonb_as_json(type_, compiler, **kw):
    return 'JSON'


class StremioRemovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.addAsyncCleanup(self.engine.dispose)
        self.addAsyncCleanup(self.db.close)
        user = User(username='fixture', email='fixture@example.test', api_key='fixture')
        self.media = Media(title='Fixture', tmdb_id=42, imdb_id='tt0000042', media_type=MediaType.movie)
        self.db.add_all([user, self.media]); await self.db.flush()
        self.user_id = user.id
        self.conn = MediaServerConnection(user_id=user.id, type='stremio', name='Target',
            url='https://example.test', token='fixture', sync_playback=True, push_playback=False)
        self.db.add(self.conn); await self.db.flush()
        self.entry = TrackedEntry(user_id=user.id, media_id=self.media.id, status='watching')
        self.row = {'content_id': 'tt0000042', 'content_type': 'movie', 'position': 30, 'duration': 100}
        self.baseline = StreamBaseline(user_id=user.id, connection_id=self.conn.id, approved=True,
            observed_at=datetime.now() + timedelta(seconds=1), snapshot={
                'mappings': {'tt0000042': 42}, 'progress': {'tt0000042': self.row},
                'watched': [], 'records': {'library': [], 'watched': [], 'progress': [self.row]}})
        self.db.add_all([self.entry, self.baseline]); await self.db.commit()

    async def test_inbound_removal_does_not_require_outbound_playback(self):
        await observe_stream_snapshot(self.db, self.conn, [], [], [], {}, sync_playback=True)
        await self.db.commit()
        self.assertEqual(self.entry.status, 'dropped')
        reviews = (await self.db.execute(select(SyncReview))).scalars().all()
        self.assertEqual([(r.kind, r.state) for r in reviews], [('playback_removed', 'pending')])
        self.assertEqual((await self.db.execute(select(StreamAction))).scalars().all(), [])

    async def test_disabled_inbound_playback_preserves_watching(self):
        await observe_stream_snapshot(self.db, self.conn, [], [], [], {}, sync_playback=False)
        await self.db.commit()
        self.assertEqual(self.entry.status, 'watching')
        self.assertEqual((await self.db.execute(select(SyncReview))).scalars().all(), [])
        self.assertEqual(self.baseline.snapshot['progress'], {'tt0000042': self.row})

    async def test_partial_pull_does_not_infer_removal(self):
        await observe_stream_snapshot(self.db, self.conn, [], [], [], {}, complete=False)
        self.assertEqual(self.entry.status, 'watching')
        self.assertEqual((await self.db.execute(select(SyncReview))).scalars().all(), [])

    async def test_nuvio_removal_queues_stremio_without_saved_active_progress(self):
        self.conn.push_playback = True
        self.conn.sync_playback = False
        self.entry.status = 'dropped'
        self.baseline.snapshot = {'mappings': {'tt0000042': 42, 'legacy42': 42}, 'progress': {},
            'resume': {'tt0000042': {'position': 30}, 'legacy42': {'position': 10}, 'unrelated': {'position': 1}},
            'outbound': {'tt0000042': {'action': 'upsert'}, 'legacy42': {'action': 'upsert'}},
            'watched': ['tt0000042:None:None'], 'library': ['tt0000042']}
        source = MediaServerConnection(user_id=self.user_id, type='nuvio', name='Source',
            url='https://example.test', token='fixture', push_playback=False, push_watched=False)
        self.db.add(source); await self.db.commit()
        await queue_dismissals(self.db, source, self.media)
        await self.db.commit()
        action = (await self.db.execute(select(StreamAction))).scalar_one()
        self.assertEqual((action.connection_id, action.action), (self.conn.id, 'dismiss'))
        self.assertEqual(action.payload['content_id'], 'tt0000042')
        with patch('core.tracking_snapshot.require_stream_reconciliation', AsyncMock()), \
             patch('core.stream_actions.dismiss_stremio', AsyncMock()) as clear:
            await dispatch_stream_actions(self.db, self.user_id)
        self.assertEqual(action.state, 'applied')
        clear.assert_awaited_once()
        self.assertEqual(self.baseline.snapshot['resume'], {'unrelated': {'position': 1}})
        self.assertEqual(self.baseline.snapshot['outbound'], {})
        self.assertEqual(self.baseline.snapshot['watched'], ['tt0000042:None:None'])
        self.assertEqual(self.baseline.snapshot['library'], ['tt0000042'])

    async def test_outbound_cleanup_can_resolve_imdb_without_baseline_mapping(self):
        self.conn.push_playback = True
        self.baseline.snapshot = {'mappings': {}, 'progress': {}}
        self.entry.status = 'dropped'
        await self.db.commit()
        await queue_local_dismissals(self.db, self.user_id, self.media)
        await self.db.flush()
        action = (await self.db.execute(select(StreamAction))).scalar_one()
        self.assertEqual(action.payload['content_id'], 'tt0000042')

    async def test_outbound_disabled_does_not_queue_cleanup(self):
        self.baseline.snapshot = {'mappings': {'tt0000042': 42}, 'progress': {}}
        await self.db.commit()
        await queue_local_dismissals(self.db, self.user_id, self.media)
        await self.db.flush()
        self.assertEqual((await self.db.execute(select(StreamAction))).scalars().all(), [])

    async def test_matching_completion_is_not_a_playback_removal(self):
        watched = {'content_id': 'tt0000042', 'content_type': 'movie', 'watched_at': '2020-01-01'}
        await observe_stream_snapshot(self.db, self.conn, [], [watched], [], {})
        await self.db.commit()
        reviews = (await self.db.execute(select(SyncReview).where(
            SyncReview.kind == 'playback_removed'))).scalars().all()
        self.assertEqual(reviews, [])

    async def test_first_empty_snapshot_does_not_drop_local_watching(self):
        await self.db.delete(self.baseline)
        await self.db.commit()
        await observe_stream_snapshot(self.db, self.conn, [], [], [], {})
        await self.db.commit()
        self.assertEqual(self.entry.status, 'watching')
        reviews = (await self.db.execute(select(SyncReview))).scalars().all()
        self.assertEqual([(r.kind, r.state) for r in reviews], [('initial_import', 'pending')])

    async def test_pull_only_stremio_removal_still_queues_enabled_peer(self):
        peer = MediaServerConnection(user_id=self.user_id, type='nuvio', name='Peer',
            url='https://example.test', token='fixture', push_watched=True, push_playback=False)
        self.db.add(peer); await self.db.flush()
        self.db.add(StreamBaseline(user_id=self.user_id, connection_id=peer.id, approved=True,
            snapshot={'mappings': {'tt0000042': 42}, 'progress': {}}))
        await self.db.commit()
        await observe_stream_snapshot(self.db, self.conn, [], [], [], {}, sync_playback=True)
        await self.db.commit()
        action = (await self.db.execute(select(StreamAction))).scalar_one()
        self.assertEqual((action.connection_id, action.action), (peer.id, 'dismiss'))
        self.assertTrue(action.payload['visibility_only'])

    async def test_imdb_only_title_can_queue_outbound_cleanup(self):
        self.conn.push_playback = True
        self.media.tmdb_id = None
        self.baseline.snapshot = {'mappings': {}, 'progress': {}}
        await self.db.commit()
        await queue_local_dismissals(self.db, self.user_id, self.media)
        await self.db.flush()
        action = (await self.db.execute(select(StreamAction))).scalar_one()
        self.assertEqual(action.payload['content_id'], 'tt0000042')
