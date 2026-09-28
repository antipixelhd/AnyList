"""Watched-only Nuvio connections must still deliver Next Up decisions."""
import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from models import Media, MediaType, MediaServerConnection, WatchEvent
from models.base import Base
from models.tracking import StreamAction, StreamBaseline, TrackedEntry
from core.stream_actions import queue_local_dismissals, queue_restorations, dispatch_stream_actions


@compiles(JSONB, 'sqlite')
def _jsonb_as_json(type_, compiler, **kw):
    return 'JSON'


class NuvioWatchedOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.addAsyncCleanup(self.engine.dispose)
        self.addAsyncCleanup(self.db.close)
        self.media = Media(title='Fixture', tmdb_id=42, imdb_id='tt0000042', media_type=MediaType.series)
        self.conn = MediaServerConnection(user_id=1, type='nuvio', name='Fixture',
            url='https://example.test', token='fixture', server_user_id='1',
            push_watched=True, push_playback=False)
        self.db.add_all([self.media, self.conn])
        await self.db.flush()
        self.entry = TrackedEntry(user_id=1, media_id=self.media.id, status='dropped')
        self.baseline = StreamBaseline(user_id=1, connection_id=self.conn.id, approved=True,
            snapshot={'mappings': {'tt0000042': 42},
                'progress': {'tt0000042': {'content_id': 'tt0000042', 'position': 30}},
                'resume': {'tt0000042': {'position': 30}}})
        self.db.add_all([self.entry, self.baseline])
        await self.db.commit()

    async def test_dropped_updates_settings_without_reading_or_deleting_playback(self):
        before = copy.deepcopy(self.baseline.snapshot)
        await queue_local_dismissals(self.db, 1, self.media)
        await self.db.commit()
        action = (await self.db.execute(select(StreamAction))).scalar_one()
        self.assertTrue(action.payload['visibility_only'])
        token_db = AsyncMock()
        token_context = AsyncMock()
        token_context.__aenter__.return_value = token_db
        with patch('core.tracking_snapshot.require_stream_reconciliation', AsyncMock()), \
             patch('db.AsyncSessionLocal', return_value=token_context), \
             patch('core.nuvio.refresh_session', AsyncMock(return_value=SimpleNamespace(
                 refresh_token='fixture', access_token='fixture'))), \
             patch('core.nuvio._pull_watched_items', AsyncMock(return_value=[{
                 'content_id': 'tt0000042', 'content_type': 'series', 'season': 1, 'episode': 4,
             }])), \
             patch('core.nuvio._pull_watch_progress', AsyncMock()) as progress, \
             patch('core.nuvio._rpc', AsyncMock()) as rpc, \
             patch('core.nuvio.update_next_up_dismissals', AsyncMock()) as settings:
            await dispatch_stream_actions(self.db, 1)
        self.assertEqual(action.state, 'applied')
        self.assertEqual(action.attempts, 1)
        self.assertIn('tt0000042', settings.await_args.kwargs['hide'])
        progress.assert_not_awaited()
        rpc.assert_not_awaited()
        self.assertEqual(self.baseline.snapshot, before)

    async def test_watching_queues_and_dispatches_settings_only_show(self):
        self.entry.status = 'watching'
        await self.db.commit()
        await queue_restorations(self.db, 1, self.media)
        await self.db.commit()
        action = (await self.db.execute(select(StreamAction))).scalar_one()
        self.assertEqual(action.action, 'upsert')
        self.assertTrue(action.payload['next_up_only'])
        with patch('core.tracking_snapshot.require_stream_reconciliation', AsyncMock()), \
             patch('core.stream_actions.show_nuvio_next_up', AsyncMock()) as show, \
             patch('core.stream_actions.push_nuvio_progress', AsyncMock()) as progress:
            await dispatch_stream_actions(self.db, 1)
        self.assertEqual(action.state, 'applied')
        show.assert_awaited_once()
        progress.assert_not_awaited()

    async def test_stremio_watched_only_and_disabled_nuvio_do_not_queue_dismissals(self):
        self.conn.push_watched = False
        self.db.add(MediaServerConnection(user_id=1, type='stremio', name='Fixture',
            url='https://example.test', token='fixture', push_watched=True, push_playback=False))
        await self.db.commit()
        await queue_local_dismissals(self.db, 1, self.media)
        await self.db.flush()
        self.assertEqual((await self.db.execute(select(StreamAction))).scalars().all(), [])

    async def test_watched_write_retries_visibility_when_playback_is_disabled(self):
        from core.nuvio import NuvioAPIError
        from core.watch_intents import queue_watch_intents, dispatch_watch_intents
        from models.watch_intent import WatchIntent
        self.db.add(WatchEvent(user_id=1, media_id=self.media.id, completed=True))
        await self.db.commit()
        await queue_watch_intents(self.db, 1, {self.media.id})
        await self.db.commit()
        with patch('core.tracking_snapshot.require_stream_reconciliation', AsyncMock()), \
             patch('core.watch_intents._write_provider_watch_state', AsyncMock()) as writer, \
             patch('core.nuvio_visibility.sync_next_up_visibility', AsyncMock(
                 side_effect=[NuvioAPIError('fixture'), None])) as visibility:
            await dispatch_watch_intents(self.db, 1)
            intent = (await self.db.execute(select(WatchIntent))).scalar_one()
            self.assertEqual((intent.state, intent.last_error), ('pending', 'NuvioAPIError'))
            await dispatch_watch_intents(self.db, 1)
        self.assertEqual((intent.state, intent.attempts, intent.last_error), ('applied', 2, None))
        self.assertEqual(writer.await_count, 2)
        self.assertEqual(visibility.await_count, 2)
        self.assertIn('tt0000042', {row['content_id'] for row in visibility.await_args.args[2]})
