"""Connection onboarding uses the streaming draft, never background watch writes."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from core.connection_imports import observations, prepare_items, stage_first_snapshot, finish_import, remove_covered_items
from core.netflix_sessions import _apply_import
from core.streaming_evidence import activity_plan
from models import User, MediaServerConnection, NetflixImportSession, WatchEvent
from models.tracking import StreamBaseline, SyncReview, TrackedEntry
from routers.netflix_import import get_connection_import


def movie(**changes):
    return {'content_id': 'tmdb:42', 'content_type': 'movie', 'title': 'Movie',
            'last_watched': 1578009600000, 'position': 2520000, 'duration': 3600000, **changes}


class ConnectionEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_movie_threshold_minimum_and_explicit_watched_without_date(self):
        details = {'id': 42, 'title': 'Movie', 'runtime': 60, 'release_date': '2019-01-01'}
        with patch('core.connection_imports.tmdb.get_movie_light', AsyncMock(return_value=details)):
            strong = (await prepare_items([], [movie()], {'tmdb:42': 42}, 'fixture'))[0]
            self.assertTrue(strong['progress_reviewed'])
            self.assertEqual(strong['outcome']['status'], 'completed')
            weak = (await prepare_items([], [movie(position=600000)], {'tmdb:42': 42}, 'fixture'))[0]
            self.assertFalse(weak['progress_reviewed'])
            self.assertEqual(weak['outcome']['tracking_status'], 'dropped')
            self.assertEqual(await prepare_items([], [movie(position=599999)], {'tmdb:42': 42}, 'fixture'), [])
            explicit = (await prepare_items([movie(watched_at=None)], [], {'tmdb:42': 42}, 'fixture'))[0]
            self.assertTrue(explicit['progress_reviewed'])
            self.assertEqual(explicit['source_dates'], [])
            self.assertEqual(activity_plan(explicit), [])

    async def test_series_keeps_weak_evidence_but_suggests_only_completed_episodes(self):
        show = {'id': 99, 'name': 'Show', 'first_air_date': '2019-01-01',
                'seasons': [{'season_number': 1, 'episode_count': 5}]}
        catalog = {'episodes': [{'id': n, 'name': f'Episode {n}', 'season_number': 1,
                                'episode_number': n, 'air_date': '2019-01-01', 'runtime': 60} for n in range(1, 6)]}
        watched = [{'content_id': 'tmdb:99', 'content_type': 'series', 'title': 'Show',
                    'season': 1, 'episode': n, 'watched_at': 1578009600000,
                    'date_shared': n < 3} for n in range(1, 4)]
        progress = [{**movie(), 'content_id': 'tmdb:99', 'content_type': 'series', 'title': 'Show',
                     'season': 1, 'episode': n, 'position': 1200000} for n in (4, 5)]
        with patch('core.connection_imports.tmdb.get_show_light', AsyncMock(return_value=show)), \
             patch('core.connection_imports.tmdb.get_season', AsyncMock(return_value=catalog)):
            item = (await prepare_items(watched, progress, {'tmdb:99': 99}, 'fixture'))[0]
        self.assertEqual(item['match']['state'], 'matched')
        self.assertEqual(item['decision']['action'], 'confirm')
        self.assertEqual(item['outcome']['latest_episode'], 3)
        self.assertEqual(item['outcome']['status'], 'partial')
        self.assertEqual(len(item['episodes']), 5)
        self.assertEqual([e['watch_qualified'] for e in item['episodes']], [True, True, True, False, False])
        self.assertEqual(item['episodes'][0]['dates'], [])
        self.assertFalse(any(e['key'] == 'episode:1:1' for e in activity_plan(item)))

    async def test_unresolved_identifiers_never_trigger_name_search(self):
        with patch('core.connection_imports.tmdb.get_movie_light', AsyncMock()) as details:
            self.assertEqual(await prepare_items([movie()], [], {}, 'fixture'), [])
            details.assert_not_awaited()
        self.assertEqual(observations([], [movie(position='invalid')]), [])

    async def test_ambiguous_external_identity_is_not_automatically_accepted(self):
        from core.server_sync import _resolve_nuvio_tmdb_ids
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
        with patch('core.server_sync.tmdb.find_by_external_id', AsyncMock(return_value={
            'movie_results': [{'id': 1}, {'id': 2}],
        })):
            self.assertEqual(await _resolve_nuvio_tmdb_ids([
                {'content_id': 'tt1234567', 'content_type': 'movie'}], db, 7, 'fixture'), {})


@unittest.skipUnless(os.getenv('TRACKING_TEST_DATABASE_URL'), 'Requires disposable PostgreSQL database')
class ConnectionDraftDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ['TRACKING_TEST_DATABASE_URL'])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.db = AsyncSession(bind=self.connection, expire_on_commit=False, join_transaction_mode='create_savepoint')
        key = uuid4().hex
        self.user = User(username=key, email=f'{key}@example.test', api_key=key)
        self.db.add(self.user)
        await self.db.flush()
        self.conn = MediaServerConnection(user_id=self.user.id, type='stremio', name='Fixture',
            url='https://example.test', token='fixture', server_user_id='fixture', identity_version=0)
        self.db.add(self.conn)
        await self.db.flush()
        self.details = patch('core.connection_imports.tmdb.get_movie_light', AsyncMock(return_value={
            'id': 42, 'title': 'Movie', 'runtime': 60, 'release_date': '2019-01-01'}))
        self.details.start()
        self.addCleanup(self.details.stop)

    async def asyncTearDown(self):
        await self.db.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def stage(self, watched=None, progress=None):
        return await stage_first_snapshot(self.db, self.conn, [], watched or [], progress or [], {'tmdb:42': 42}, 'fixture')

    async def draft(self):
        baseline = await self.db.get(StreamBaseline, self.conn.id)
        return await self.db.get(NetflixImportSession, baseline.snapshot['import_session_id'])

    async def test_watched_disabled_bypasses_review_and_account_reservation(self):
        from core.import_admission import active_import
        from core.connection_imports import onboarding_pending
        self.conn.sync_watched = False
        await self.db.flush()
        self.assertIsNone(await active_import(self.db, self.user.id))
        with patch('core.connection_imports.prepare_items', AsyncMock()) as prepare:
            self.assertFalse(await self.stage(progress=[movie()]))
        prepare.assert_not_awaited()
        self.assertIsNone(await self.db.get(StreamBaseline, self.conn.id))
        self.assertFalse(await onboarding_pending(self.db, self.conn, require_snapshot=True))

    async def test_turning_watched_off_does_not_release_existing_review(self):
        from core.connection_imports import onboarding_pending
        await self.stage(progress=[movie()])
        self.conn.sync_watched = False
        await self.db.flush()
        self.assertTrue(await onboarding_pending(self.db, self.conn))
        self.assertTrue(await self.stage(progress=[movie()]))

    async def test_account_slot_includes_connection_before_snapshot(self):
        from core.import_admission import active_import, require_import_slot
        from fastapi import HTTPException
        current = await active_import(self.db, self.user.id)
        self.assertIsNone(current['import_id'])
        self.assertEqual(current['connection_id'], self.conn.id)
        with self.assertRaises(HTTPException) as error:
            await require_import_slot(self.db, self.user.id)
        self.assertEqual(error.exception.status_code, 409)
        await require_import_slot(self.db, self.user.id, connection_id=self.conn.id)
        self.assertIsNone(await active_import(self.db, self.user.id + 10000))

    async def test_pending_import_rejects_upload_and_new_connection(self):
        from io import BytesIO
        from fastapi import BackgroundTasks, HTTPException, UploadFile
        from routers.netflix_import import upload_netflix_history
        from routers.auth import create_connection
        from schemas import MediaServerConnectionCreate
        tasks = BackgroundTasks()
        with self.assertRaises(HTTPException) as upload_error:
            await upload_netflix_history('netflix', tasks,
                UploadFile(filename='history.csv', file=BytesIO(b'Title,Date\nMovie,2020-01-01')),
                None, self.db, self.user)
        self.assertEqual(upload_error.exception.status_code, 409)
        with self.assertRaises(HTTPException) as connection_error:
            await create_connection(MediaServerConnectionCreate(type='plex', name='Second',
                url='https://example.test', token='fixture'), tasks, self.db, self.user)
        self.assertEqual(connection_error.exception.status_code, 409)
        self.assertEqual(len((await self.db.scalars(select(MediaServerConnection)
            .where(MediaServerConnection.user_id == self.user.id))).all()), 1)
        self.assertEqual((await self.db.scalars(select(NetflixImportSession)
            .where(NetflixImportSession.user_id == self.user.id))).all(), [])
        self.assertEqual(tasks.tasks, [])

    async def test_file_draft_blocks_initial_snapshot_until_cancelled(self):
        from core import netflix_sessions
        from core.import_admission import active_import
        from datetime import timedelta
        from fastapi import HTTPException
        now = netflix_sessions._now()
        draft = NetflixImportSession(id=str(uuid4()), user_id=self.user.id, status='review',
            phase='review', payload={'source': 'amazon'}, revision=0,
            created_at=now, updated_at=now, expires_at=now + timedelta(days=7))
        self.db.add(draft)
        await self.db.flush()
        self.assertEqual((await active_import(self.db, self.user.id))['import_id'], draft.id)
        with self.assertRaises(HTTPException):
            await self.stage(progress=[movie()])
        self.assertIsNone(await self.db.get(StreamBaseline, self.conn.id))
        draft.status = 'cancelled'
        await self.db.flush()
        self.assertTrue(await self.stage(progress=[movie()]))
        self.assertEqual((await active_import(self.db, self.user.id))['import_id'], (await self.draft()).id)

    async def test_finished_cancelled_expired_drafts_release_slot(self):
        from core import netflix_sessions
        from core.import_admission import require_import_slot
        from datetime import timedelta
        await self.stage()
        now = netflix_sessions._now()
        for status in ('committed', 'cancelled', 'failed'):
            self.db.add(NetflixImportSession(id=str(uuid4()), user_id=self.user.id, status=status,
                phase='complete', payload={'source': 'netflix'}, revision=0,
                created_at=now, updated_at=now,
                expires_at=now + timedelta(days=-1 if status == 'failed' else 7)))
        await self.db.flush()
        self.assertIsNone(await require_import_slot(self.db, self.user.id))

    async def test_concurrent_starts_reserve_only_one_account_slot(self):
        import asyncio
        from datetime import timedelta
        from fastapi import HTTPException
        from sqlalchemy import delete
        from core import netflix_sessions
        from core.import_admission import require_import_slot
        # Separate committed user: independent transactions must share the mutex.
        async with AsyncSession(self.engine, expire_on_commit=False) as setup:
            key = uuid4().hex
            user = User(username=key, email=f'{key}@example.test', api_key=key)
            setup.add(user)
            await setup.commit()
            user_id = user.id
        async def start(source):
            async with AsyncSession(self.engine) as db:
                try:
                    await require_import_slot(db, user_id)
                    await asyncio.sleep(0.03)
                    now = netflix_sessions._now()
                    db.add(NetflixImportSession(id=str(uuid4()), user_id=user_id, status='preparing',
                        phase='parse', payload={'source': source}, revision=0,
                        created_at=now, updated_at=now, expires_at=now + timedelta(days=7)))
                    await db.commit()
                    return 202
                except HTTPException as error:
                    return error.status_code
        try:
            self.assertEqual(sorted(await asyncio.gather(start('netflix'), start('amazon'))), [202, 409])
        finally:
            async with AsyncSession(self.engine) as cleanup:
                await cleanup.execute(delete(User).where(User.id == user_id))
                await cleanup.commit()

    async def test_first_snapshot_is_private_draft_without_watch_or_notifications(self):
        self.assertTrue(await self.stage(progress=[movie()]))
        draft = await self.draft()
        self.assertEqual(draft.payload['source'], 'stremio')
        self.assertEqual(draft.payload['connection_id'], self.conn.id)
        self.assertEqual((await self.db.scalars(select(TrackedEntry))).all(), [])
        self.assertEqual((await self.db.scalars(select(WatchEvent))).all(), [])
        self.assertEqual((await self.db.scalars(select(SyncReview))).all(), [])
        self.assertTrue(await self.stage(progress=[movie(position=3000000)]))
        self.assertEqual((await self.draft()).id, draft.id)
        response = await get_connection_import(self.conn.id, self.db, self.user)
        self.assertEqual(response['session']['id'], draft.id)
        other = SimpleNamespace(id=self.user.id + 100)
        with self.assertRaisesRegex(Exception, '404'):
            await get_connection_import(self.conn.id, self.db, other)

    async def test_empty_snapshot_creates_baseline_without_opening_importer(self):
        self.assertTrue(await self.stage())
        baseline = await self.db.get(StreamBaseline, self.conn.id)
        self.assertTrue(baseline.approved)
        self.assertNotIn('import_session_id', baseline.snapshot)
        self.assertEqual((await self.db.scalars(select(NetflixImportSession).where(NetflixImportSession.user_id == self.user.id))).all(), [])
        self.assertFalse(await self.stage())

    async def test_commit_uses_common_import_and_repeat_data_has_no_changes(self):
        await self.stage(progress=[movie()])
        draft = await self.draft()
        await _apply_import(self.db, self.user.id, draft, AsyncMock())
        await self.db.flush()
        items = await prepare_items([], [movie()], {'tmdb:42': 42}, 'fixture')
        self.assertEqual(await remove_covered_items(self.db, self.user.id, items), [])
        earlier = await prepare_items([], [movie(last_watched=1546300800000)], {'tmdb:42': 42}, 'fixture')
        self.assertEqual(len(await remove_covered_items(self.db, self.user.id, earlier)), 1)
        await finish_import(self.db, draft)
        self.assertTrue((await self.db.get(StreamBaseline, self.conn.id)).approved)

    async def test_undated_completed_watch_preserves_progress_without_activity_date(self):
        await self.stage(watched=[movie(watched_at=None)])
        draft = await self.draft()
        await _apply_import(self.db, self.user.id, draft, AsyncMock())
        await self.db.flush()
        watch = (await self.db.scalars(select(WatchEvent))).one()
        self.assertTrue(watch.date_inferred)
        entry = (await self.db.scalars(select(TrackedEntry))).one()
        self.assertEqual(entry.progress, 1)
        self.assertIsNone(entry.finish_date)
        items = await prepare_items([movie(watched_at=None)], [], {'tmdb:42': 42}, 'fixture')
        self.assertEqual(await remove_covered_items(self.db, self.user.id, items), [])

    async def test_repeated_dropped_movie_is_covered_but_later_completion_is_new(self):
        await self.stage(progress=[movie(position=600000)])
        draft = await self.draft()
        await _apply_import(self.db, self.user.id, draft, AsyncMock())
        await self.db.flush()
        weak = await prepare_items([], [movie(position=600000)], {'tmdb:42': 42}, 'fixture')
        self.assertEqual(await remove_covered_items(self.db, self.user.id, weak), [])
        completed = await prepare_items([], [movie()], {'tmdb:42': 42}, 'fixture')
        self.assertEqual(len(await remove_covered_items(self.db, self.user.id, completed)), 1)

    async def test_cancelling_provisional_import_removes_connection(self):
        from routers.netflix_import import cancel_netflix_import
        await self.stage(progress=[movie()])
        draft = await self.draft()
        connection_id = self.conn.id
        with patch('core.stremio.logout', AsyncMock()):
            await cancel_netflix_import(draft.id, self.db, self.user)
        self.assertIsNone(await self.db.get(StreamBaseline, connection_id, populate_existing=True))
        self.assertIsNone(await self.db.get(MediaServerConnection, connection_id, populate_existing=True))
        self.assertEqual(draft.status, 'cancelled')

    async def test_cancel_refreshes_cached_status_after_worker_commits(self):
        from fastapi import HTTPException
        from sqlalchemy import update
        from routers.netflix_import import cancel_netflix_import
        from core import netflix_sessions
        await self.stage(progress=[movie()])
        draft = await self.draft()
        await _apply_import(self.db, self.user.id, draft, AsyncMock())
        await finish_import(self.db, draft)
        draft.status = 'committing'
        await self.db.commit()
        # Model another worker's final commit without refreshing this request's
        # already-loaded ORM identity. The locked read must see the database.
        await self.db.execute(update(NetflixImportSession).where(NetflixImportSession.id == draft.id)
            .values(status='committed', payload={}).execution_options(synchronize_session=False))
        self.assertEqual(draft.status, 'committing')
        with self.assertRaises(HTTPException) as committed:
            await cancel_netflix_import(draft.id, self.db, self.user)
        self.assertEqual(committed.exception.status_code, 409)
        self.assertEqual(draft.status, 'committed')
        self.assertIsNotNone(await self.db.get(MediaServerConnection, self.conn.id))
        self.assertTrue((await self.db.scalars(select(WatchEvent).where(WatchEvent.user_id == self.user.id))).all())
        self.assertNotIn(draft.id, netflix_sessions._cancelled)

    async def test_commit_worker_cancellation_removes_provisional_connection(self):
        from contextlib import asynccontextmanager
        from core import netflix_sessions
        await self.stage(progress=[movie()])
        draft = await self.draft()
        draft.status = 'committing'
        draft.idempotency_key = 'cancel-before-api-lock'
        await self.db.commit()
        connection_id, session_id, user_id = self.conn.id, draft.id, self.user.id

        @asynccontextmanager
        async def session():
            try:
                yield self.db
            except BaseException:
                await self.db.rollback()
                raise

        with patch.object(netflix_sessions, 'async_sessionmaker', return_value=session), \
             patch.object(netflix_sessions, '_apply_import', AsyncMock(side_effect=netflix_sessions.ImportCancelled)):
            await netflix_sessions._commit_session(session_id, user_id, 'cancel-before-api-lock')
        refreshed = await self.db.get(NetflixImportSession, session_id, populate_existing=True)
        self.assertEqual(refreshed.status, 'cancelled')
        self.assertEqual(refreshed.payload, {})
        self.assertIsNone(await self.db.get(MediaServerConnection, connection_id, populate_existing=True))
        self.assertIsNone(await self.db.get(StreamBaseline, connection_id, populate_existing=True))

    async def test_expiry_refreshes_cached_state_before_erasing_committed_import(self):
        from datetime import datetime, timedelta
        from sqlalchemy import update
        from routers.netflix_import import _load_owned
        await self.stage(progress=[movie()])
        draft = await self.draft()
        draft.expires_at = datetime.utcnow() - timedelta(seconds=1)
        await self.db.commit()
        await self.db.execute(update(NetflixImportSession).where(NetflixImportSession.id == draft.id)
            .values(status='committed', expires_at=datetime.utcnow() + timedelta(days=365))
            .execution_options(synchronize_session=False))
        self.assertEqual(draft.status, 'review')
        self.assertEqual((await _load_owned(self.db, draft.id, self.user.id)).status, 'committed')
        self.assertIsNotNone(await self.db.get(MediaServerConnection, self.conn.id))

    async def test_atomic_step_save_and_retry_keep_all_choices(self):
        from fastapi import HTTPException
        from routers.netflix_import import update_import_decisions
        await self.stage(progress=[movie(position=1200000)])
        draft = await self.draft()
        item_id = draft.payload['items'][0]['id']
        revision = draft.revision
        body = {'revision': revision, 'patches': [
            {'itemId': item_id, 'changes': {'status': 'completed', 'finish_date': '2020-01-01'}},
            {'itemId': 'missing', 'changes': {'status': 'skip'}}]}
        with self.assertRaises(HTTPException):
            await update_import_decisions(draft.id, body, self.db, self.user)
        self.assertEqual(draft.revision, revision)
        self.assertNotIn('finish_date', draft.payload['items'][0]['outcome'])
        body['patches'].pop()
        response = await update_import_decisions(draft.id, body, self.db, self.user)
        self.assertEqual(response['revision'], revision + 1)
        self.assertEqual(response['items'][0]['outcome']['finish_date'], '2020-01-01')
        self.assertTrue(response['items'][0]['progress_reviewed'])
        with self.assertRaises(HTTPException):
            await update_import_decisions(draft.id, body, self.db, self.user)

    async def test_import_notifications_follow_owner_expiry_and_cancellation(self):
        from datetime import datetime, timedelta
        from core.import_notifications import active_import_events
        await self.stage(progress=[movie()])
        draft = await self.draft()
        now = datetime.utcnow()
        events = await active_import_events(self.db, self.user.id, now)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['payload']['import_id'], draft.id)
        self.assertIn('connection=', events[0]['payload']['resolve_url'])
        self.assertEqual(await active_import_events(self.db, self.user.id + 99999, now), [])
        self.assertEqual(await active_import_events(self.db, self.user.id, now + timedelta(days=8)), [])
        draft.status = 'cancelled'
        await self.db.flush()
        self.assertEqual(await active_import_events(self.db, self.user.id, now), [])

    async def test_connection_stays_blocked_until_owned_handoff_finishes(self):
        from core.connection_imports import onboarding_pending, handoff_connection, release_handoff
        await self.stage(progress=[movie()])
        draft = await self.draft()
        self.assertTrue(await onboarding_pending(self.db, self.conn))
        await finish_import(self.db, draft)
        await self.db.flush()
        self.assertTrue(await onboarding_pending(self.db, self.conn))
        token = handoff_connection.set(self.conn.id)
        try:
            self.assertFalse(await onboarding_pending(self.db, self.conn))
        finally:
            handoff_connection.reset(token)
        await release_handoff(self.db, self.conn.id, draft.id)
        await self.db.flush()
        self.assertFalse(await onboarding_pending(self.db, self.conn))

    async def test_provisional_connections_are_not_delivery_targets(self):
        from core.connection_imports import handoff_connection
        from core.pull_cycle import PullCycleState
        from core.sync_delivery import delivery_targets
        await self.stage(progress=[movie()])
        self.conn.push_watched = True
        await self.db.flush()
        cycle = PullCycleState(self.user.id)
        self.assertNotIn(f'connection:{self.conn.id}', await delivery_targets(self.db, cycle))
        await finish_import(self.db, await self.draft())
        await self.db.flush()
        self.assertNotIn(f'connection:{self.conn.id}', await delivery_targets(self.db, cycle))
        token = handoff_connection.set(self.conn.id)
        try:
            self.assertIn(f'connection:{self.conn.id}', await delivery_targets(self.db, cycle))
        finally:
            handoff_connection.reset(token)

    async def test_full_resync_cannot_bypass_onboarding_before_first_snapshot(self):
        from fastapi import BackgroundTasks, HTTPException
        from routers.sync import sync_connection
        from core.connection_imports import onboarding_pending
        self.assertIsNone(await self.db.get(StreamBaseline, self.conn.id))
        self.assertTrue(await onboarding_pending(self.db, self.conn, require_snapshot=True))
        with self.assertRaises(HTTPException) as blocked:
            await sync_connection(self.conn.id, BackgroundTasks(), movie_limit=0, show_limit=0,
                full=True, db=self.db, current_user=self.user)
        self.assertEqual(blocked.exception.status_code, 409)
        self.assertIsNone(await self.db.get(StreamBaseline, self.conn.id))

    async def test_established_connection_can_still_repair_an_incomplete_clear(self):
        from core.connection_imports import onboarding_pending
        self.db.add(StreamBaseline(connection_id=self.conn.id, user_id=self.user.id,
            approved=False, snapshot={'clear_guard': {'pending': True}}))
        await self.db.flush()
        self.assertFalse(await onboarding_pending(self.db, self.conn, require_snapshot=True))
        self.assertTrue(await onboarding_pending(self.db, self.conn, require_approval=True))

    async def test_routine_account_pull_does_not_admit_provisional_connection(self):
        from fastapi import HTTPException
        from core.account_sync import queue_account_pull
        await self.stage(progress=[movie()])
        with self.assertRaises(HTTPException) as blocked:
            await queue_account_pull(self.db, self.user.id)
        self.assertEqual(blocked.exception.status_code, 400)
        from core.connection_imports import finish_import
        await finish_import(self.db, await self.draft())
        await self.db.flush()
        with self.assertRaises(HTTPException):
            await queue_account_pull(self.db, self.user.id)

    async def test_file_drafts_are_recoverable_without_a_connection_or_review_row(self):
        from datetime import datetime, timedelta
        from core.import_notifications import active_import_events
        for source in ('netflix', 'amazon'):
            self.db.add(NetflixImportSession(id=str(uuid4()), user_id=self.user.id,
                status='preparing', phase='parse', payload={'source': source},
                expires_at=datetime.utcnow() + timedelta(days=7)))
        await self.db.flush()
        events = await active_import_events(self.db, self.user.id, datetime.utcnow())
        self.assertEqual({event['provider'] for event in events}, {'netflix', 'amazon'})
        self.assertTrue(all('section=imports' in event['payload']['resolve_url'] for event in events))

    async def test_expired_import_removes_provisional_connection(self):
        from datetime import datetime, timedelta
        from routers.netflix_import import _load_owned
        from fastapi import HTTPException
        await self.stage(progress=[movie()])
        draft = await self.draft()
        connection_id = self.conn.id
        draft.expires_at = datetime.utcnow() - timedelta(seconds=1)
        await self.db.flush()
        with self.assertRaises(HTTPException) as expired:
            await _load_owned(self.db, draft.id, self.user.id)
        self.assertEqual(expired.exception.status_code, 410)
        self.assertIsNone(await self.db.get(MediaServerConnection, connection_id, populate_existing=True))

    async def test_changed_connection_cannot_commit_old_account_history(self):
        await self.stage(progress=[movie()])
        draft = await self.draft()
        self.conn.identity_version = 1
        await self.db.flush()
        with self.assertRaisesRegex(ValueError, 'connection changed'):
            await _apply_import(self.db, self.user.id, draft, AsyncMock())
        self.assertEqual((await self.db.scalars(select(TrackedEntry))).all(), [])

    async def test_commit_worker_applies_draft_and_releases_connection_in_one_transaction(self):
        from contextlib import asynccontextmanager
        from core import netflix_sessions
        await self.stage(progress=[movie()])
        draft = await self.draft()
        draft.status = 'committing'
        draft.idempotency_key = 'connection-fixture'
        await self.db.commit()

        @asynccontextmanager
        async def session():
            yield self.db

        with patch.object(netflix_sessions, 'async_sessionmaker', return_value=session), \
             patch.object(netflix_sessions, '_pull_committed_import', AsyncMock()) as handoff:
            await netflix_sessions._commit_session(draft.id, self.user.id, draft.idempotency_key)
        await self.db.refresh(draft)
        self.assertEqual(draft.status, 'committed')
        self.assertEqual(draft.payload, {})
        baseline = await self.db.get(StreamBaseline, self.conn.id)
        self.assertTrue(baseline.approved)
        self.assertNotIn('import_session_id', baseline.snapshot)
        self.assertEqual((await self.db.scalars(select(TrackedEntry))).one().status, 'completed')
        handoff.assert_awaited_once()
        root = (await self.db.scalars(select(TrackedEntry).where(TrackedEntry.user_id == self.user.id))).one()
        self.assertEqual(draft.result['delivery_media_ids'], [root.media_id])
        self.assertEqual(draft.result['delivery_watch_ids'], [root.media_id])

    async def test_handoff_seeds_accepted_changes_without_new_provider_delta(self):
        from core.connection_imports import seed_import_delivery
        from core.pull_cycle import PullCycleState
        from models import Rating
        await self.stage(progress=[movie()])
        draft = await self.draft()
        await _apply_import(self.db, self.user.id, draft, AsyncMock())
        root = (await self.db.scalars(select(TrackedEntry).where(TrackedEntry.user_id == self.user.id))).one()
        self.db.add(Rating(user_id=self.user.id, media_id=root.media_id, rating=8.5))
        draft.status = 'committed'
        draft.result = {'pull_pending': True, 'pull_job_id': 123,
            'delivery_media_ids': [root.media_id], 'delivery_watch_ids': [root.media_id]}
        await self.db.flush()
        cycle = PullCycleState(self.user.id)
        with patch('core.stream_actions.queue_local_dismissals', AsyncMock()) as dismiss:
            await seed_import_delivery(self.db, self.user.id, 123, cycle)
        self.assertEqual(cycle.new_watched_ids, {root.media_id})
        self.assertEqual(cycle.new_ratings, {(root.media_id, None): 8.5})
        dismiss.assert_awaited_once()
        unrelated = PullCycleState(self.user.id)
        await seed_import_delivery(self.db, self.user.id, 999, unrelated)
        self.assertEqual(unrelated.new_watched_ids, set())

    async def test_unresolved_first_history_is_retryable_instead_of_baselined(self):
        row = movie(content_id='tt1234567')
        with self.assertRaisesRegex(ValueError, 'Sync again'):
            await stage_first_snapshot(self.db, self.conn, [], [], [row], {}, 'fixture')
        self.assertIsNone(await self.db.get(StreamBaseline, self.conn.id))
        self.assertEqual((await self.db.scalars(select(NetflixImportSession).where(NetflixImportSession.user_id == self.user.id))).all(), [])
        await stage_first_snapshot(self.db, self.conn, [], [], [row], {'tt1234567': 42}, 'fixture')
        self.assertEqual((await self.draft()).status, 'review')
        self.assertEqual((await self.db.scalars(select(WatchEvent))).all(), [])

    async def _stage_series_resume(self):
        show = {'id': 99, 'name': 'Show', 'first_air_date': '2019-01-01',
                'seasons': [{'season_number': 1, 'episode_count': 8}]}
        catalog = {'episodes': [{'id': 90000 + n, 'name': f'Episode {n}', 'season_number': 1,
            'episode_number': n, 'air_date': '2019-01-01', 'runtime': 60} for n in range(1, 9)]}
        watched = [{'content_id': 'tmdb:99', 'content_type': 'series', 'title': 'Show',
                    'season': 1, 'episode': n, 'watched_at': 1578009600000} for n in range(1, 4)]
        progress = [{**movie(), 'content_id': 'tmdb:99', 'content_type': 'series', 'title': 'Show',
                     'season': 1, 'episode': 4, 'position': 1200000}]
        with patch('core.connection_imports.tmdb.get_show_light', AsyncMock(return_value=show)), \
             patch('core.connection_imports.tmdb.get_season', AsyncMock(return_value=catalog)):
            await stage_first_snapshot(self.db, self.conn, [], watched, progress, {'tmdb:99': 99}, 'fixture')
        return await self.draft()

    async def test_accepted_watching_import_restores_resume_without_marking_episode_watched(self):
        from models import Media, PlaybackProgress
        draft = await self._stage_series_resume()
        item = draft.payload['items'][0]
        item['outcome'].update(status='partial', tracking_status='watching', status_overridden=True)
        await _apply_import(self.db, self.user.id, draft, AsyncMock())
        await self.db.flush()
        playback = (await self.db.scalars(select(PlaybackProgress))).one()
        episode = await self.db.get(Media, playback.media_id)
        self.assertEqual((episode.season_number, episode.episode_number), (1, 4))
        self.assertEqual(playback.progress_seconds, 1200)
        self.assertAlmostEqual(playback.progress_percent, 1 / 3)
        self.assertEqual((await self.db.scalars(select(TrackedEntry))).one().progress, 3)
        self.assertFalse(await self.db.scalar(select(WatchEvent.id).where(WatchEvent.media_id == episode.id)))
        from routers.history import get_continue_watching
        with patch('routers.history.media_presentation.enrich_with_state', AsyncMock()), \
             patch('routers.history.get_user_metadata_language', AsyncMock(return_value=None)):
            response = await get_continue_watching(20, False, self.db, self.user)
        self.assertEqual([(row['media']['episode_number'], row['progress_seconds'])
                          for row in response['continue_watching']], [(4, 1200)])

    async def test_skipped_paused_dropped_or_completed_series_does_not_restore_resume(self):
        from models import PlaybackProgress
        from copy import deepcopy
        draft = await self._stage_series_resume()
        for choice, status in (('skip', 'watching'), ('partial', 'dropped'), ('partial', 'paused'), ('completed', 'watching')):
            with self.subTest(choice=choice, status=status):
                payload = deepcopy(draft.payload)
                payload['items'][0]['outcome'].update(status=choice, tracking_status=status, status_overridden=True)
                await _apply_import(self.db, self.user.id, SimpleNamespace(id=draft.id, user_id=self.user.id, payload=payload), AsyncMock())
                await self.db.flush()
                self.assertEqual((await self.db.scalars(select(PlaybackProgress))).all(), [])

    async def test_new_connection_import_preserves_existing_local_resume(self):
        from models import PlaybackProgress
        draft = await self._stage_series_resume()
        item = draft.payload['items'][0]
        item['outcome'].update(status='partial', tracking_status='watching', status_overridden=True)
        await _apply_import(self.db, self.user.id, draft, AsyncMock())
        await self.db.flush()
        playback = (await self.db.scalars(select(PlaybackProgress))).one()
        playback.progress_seconds = 600
        await self.db.flush()
        await _apply_import(self.db, self.user.id, draft, AsyncMock())
        await self.db.flush()
        self.assertEqual((await self.db.scalars(select(PlaybackProgress))).one().progress_seconds, 600)

    async def _assert_first_pull_stages_history(self, provider, *, metadata_error=False, watched_enabled=True):
        from contextlib import asynccontextmanager, ExitStack
        from datetime import datetime, timezone
        from core import server_sync
        from models.base import CollectionSource
        from models.sync import SyncJob, SyncStatus
        self.conn.type = provider
        self.conn.server_user_id = '1'
        self.conn.sync_watched = watched_enabled
        self.conn.sync_playback = True
        self.conn.sync_collection = False
        job = SyncJob(user_id=self.user.id, connection_id=self.conn.id,
                      source=CollectionSource(provider), status=SyncStatus.pending, job_type='pull')
        self.db.add(job)
        await self.db.commit()

        @asynccontextmanager
        async def session():
            yield self.db

        async def metadata(*args, **kwargs):
            if metadata_error:
                args[4]['errors'] += 1
            return []

        with ExitStack() as stack:
            stack.enter_context(patch.object(server_sync, 'async_sessionmaker', return_value=session))
            stack.enter_context(patch('core.settings_store.get_effective_tmdb_key', AsyncMock(return_value='fixture')))
            stack.enter_context(patch.object(server_sync, 'sync_items', AsyncMock(side_effect=metadata)))
            stack.enter_context(patch.object(server_sync, 'pre_cache_all_collected_bg', AsyncMock()))
            stack.enter_context(patch('core.streaming_library.retry_pending_library_deliveries', AsyncMock()))
            history = stack.enter_context(patch.object(server_sync, '_apply_nuvio_watch_history', AsyncMock()))
            playback = stack.enter_context(patch.object(server_sync, '_apply_nuvio_progress', AsyncMock()))
            snapshot = stack.enter_context(patch('core.tracking_snapshot.observe_stream_snapshot', AsyncMock(return_value=set())))
            if provider == 'nuvio':
                stack.enter_context(patch.object(server_sync.nuvio, 'pull_sync_data', AsyncMock(return_value=(
                    None, {'library': [], 'watched': [movie(watched_at=None)], 'progress': [movie()], 'cw_visibility': {}}))))
                run = server_sync._run_nuvio_sync
            else:
                stack.enter_context(patch.object(server_sync, '_pull_stremio_items', AsyncMock(return_value=(
                    [{'_id': 'tmdb:42'}], True, datetime.now(timezone.utc).replace(tzinfo=None)))))
                stack.enter_context(patch.object(server_sync, '_stremio_records', AsyncMock(return_value=(
                    [], [movie(watched_at=None)], [movie()], set()))))
                run = server_sync._run_stremio_sync
            await run(self.user.id, job.id, 0, 0, self.conn.id)
            await self.db.refresh(job)
            self.assertEqual(job.status, SyncStatus.completed, job.error_message)
            if watched_enabled:
                self.assertEqual((await self.draft()).status, 'review')
                playback.assert_not_awaited()
                snapshot.assert_not_awaited()
            else:
                self.assertEqual((await self.db.scalars(select(NetflixImportSession)
                    .where(NetflixImportSession.user_id == self.user.id))).all(), [])
                playback.assert_awaited_once()
                snapshot.assert_awaited_once()
            history.assert_not_awaited()
            self.assertEqual((await self.db.scalars(select(WatchEvent))).all(), [])
            self.assertEqual((await self.db.scalars(select(SyncReview))).all(), [])

    async def test_nuvio_playback_only_first_pull_does_not_open_importer(self):
        await self._assert_first_pull_stages_history('nuvio', watched_enabled=False)

    async def test_stremio_playback_only_first_pull_does_not_open_importer(self):
        await self._assert_first_pull_stages_history('stremio', watched_enabled=False)

    async def test_nuvio_first_pull_stages_instead_of_applying_or_notifying(self):
        await self._assert_first_pull_stages_history('nuvio')

    async def test_stremio_first_pull_stages_instead_of_applying_or_notifying(self):
        await self._assert_first_pull_stages_history('stremio')

    async def test_nuvio_metadata_error_cannot_bypass_first_import_review(self):
        await self._assert_first_pull_stages_history('nuvio', metadata_error=True)

    async def test_stremio_metadata_error_cannot_bypass_first_import_review(self):
        await self._assert_first_pull_stages_history('stremio', metadata_error=True)
