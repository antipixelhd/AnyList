import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.stream_actions import RemotePlaybackChanged, dismiss_nuvio, show_nuvio_next_up
from core.nuvio_visibility import next_up_visibility, provider_content_ids
from models import MediaServerConnection, MediaType
from models.media import Media


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def all(self):
        return self.rows


def _nuvio_call_patches(*, progress_rows, watched_rows=(), followup_rows=None):
    client = AsyncMock()
    client_context = AsyncMock()
    client_context.__aenter__.return_value = client
    token_db = AsyncMock()
    token_context = AsyncMock()
    token_context.__aenter__.return_value = token_db
    progress_results = [progress_rows]
    if followup_rows is not None:
        progress_results.append(followup_rows)
    patches = [
        patch('db.AsyncSessionLocal', return_value=token_context),
        patch('core.nuvio.httpx.AsyncClient', return_value=client_context),
        patch('core.nuvio.connection_lock', return_value=asyncio.Lock()),
        patch('core.nuvio.refresh_session', AsyncMock(return_value=SimpleNamespace(
            refresh_token='rotated', access_token='access'))),
        patch('core.nuvio._pull_watch_progress', AsyncMock(side_effect=progress_results)),
        patch('core.nuvio._pull_watched_items', AsyncMock(return_value=list(watched_rows))),
        patch('core.nuvio._rpc', AsyncMock()),
        patch('core.nuvio.update_next_up_dismissals', AsyncMock()),
    ]
    started = [item.start() for item in patches]
    return patches, started


class NuvioVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_ids_and_next_up_visibility_cover_all_aliases_and_tv_records(self):
        watching = Media(id=1, title='Watched show', media_type=MediaType.series,
            tmdb_id=42, imdb_id='tt42', tmdb_data={})
        paused = Media(id=2, title='Paused show', media_type=MediaType.series,
            tmdb_id=84, imdb_id='tt84', tmdb_data={})
        baseline = SimpleNamespace(snapshot={'mappings': {
            'nuvio-old': '42', 'nuvio-new': 42, 'nuvio-paused': '84',
        }})
        aliases = provider_content_ids(watching, baseline, records=[
            {'content_id': 'remote-watched', 'content_type': 'tv', 'tmdb_id': '42'},
        ])
        self.assertEqual(set(aliases), {
            'nuvio-old', 'nuvio-new', 'remote-watched', 'tt42', 'tmdb:42',
        })

        records = [
            {'content_id': 'nuvio-old', 'content_type': 'tv'},
            {'content_id': 'tmdb:84', 'content_type': 'tv'},
        ]
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_Result([(watching, 'watching'), (paused, 'paused')])),
            get=AsyncMock(return_value=baseline),
        )
        hidden, visible, seeds = await next_up_visibility(db, 7, 1, records)
        self.assertEqual(visible, {'nuvio-old', 'nuvio-new', 'tt42', 'tmdb:42'})
        self.assertEqual(hidden, {'tmdb:84', 'nuvio-paused', 'tt84'})

    async def test_episode_seeds_expand_across_aliases_without_mixing_shows(self):
        paused = Media(id=1, title='Paused', media_type=MediaType.series,
            tmdb_id=42, imdb_id='tt42', tmdb_data={})
        watching = Media(id=2, title='Watching', media_type=MediaType.series,
            tmdb_id=84, imdb_id='tt84', tmdb_data={})
        baseline = SimpleNamespace(snapshot={'mappings': {'old42': '42', 'alias84': 84}})
        records = [
            {'content_id': 'old42', 'content_type': 'series'},
            {'content_id': 'alias84', 'content_type': 'series'},
        ]
        history = [
            {'content_id': 'old42', 'content_type': 'series', 'season': 1, 'episode': 8},
            {'content_id': 'new42', 'content_type': 'tv', 'tmdb_id': 42, 'season': 2, 'episode': 1},
            {'content_id': 'alias84', 'content_type': 'series', 'season': 3, 'episode': None},
        ]
        db = SimpleNamespace(execute=AsyncMock(return_value=_Result([(paused, 'paused'), (watching, 'watching')])),
            get=AsyncMock(return_value=baseline))
        hidden, visible, seeds = await next_up_visibility(db, 7, 1, records, seed_records=history)
        self.assertEqual(hidden, {'old42', 'new42', 'tt42', 'tmdb:42'})
        self.assertEqual(visible, {'alias84', 'tt84', 'tmdb:84'})
        self.assertEqual(seeds, {
            'old42': {(1, 8), (2, 1)}, 'new42': {(1, 8), (2, 1)},
            'tt42': {(1, 8), (2, 1)}, 'tmdb:42': {(1, 8), (2, 1)},
            'alias84': {(3, -1)}, 'tt84': {(3, -1)}, 'tmdb:84': {(3, -1)},
        })
        self.assertEqual(baseline.snapshot['mappings'], {'old42': '42', 'alias84': 84})

    async def test_dismiss_reads_current_resume_deletes_all_aliases_and_preserves_watched_history(self):
        remote_progress = [
            {'content_id': 'nuvio-old', 'content_type': 'series', 'progress_key': 'nuvio-old_s1e2',
                'season': 1, 'episode': 2, 'position': 65000, 'duration': 90000,
                'updated_at': '2026-09-18T12:00:00Z'},
            {'content_id': 'remote-progress', 'content_type': 'tv', 'tmdb_id': '42',
                'progress_key': 'remote-progress_s2e1', 'season': 2, 'episode': 1,
                'position': 30000, 'duration': 85000,
                'updated_at': '2026-09-17T12:00:00Z'},
        ]
        remote_watched = [{'content_id': 'remote-history', 'content_type': 'tv', 'tmdb_id': 42,
            'season': 1, 'episode': 1, 'watched_at': '2026-09-16T12:00:00Z'}]
        baseline = SimpleNamespace(snapshot={
            'mappings': {'nuvio-old': '42', 'tt42': 42},
            'progress': {'nuvio-old': {'content_id': 'nuvio-old', 'progress_key': 'old-baseline',
                'position': 1000}},
            'resume': {'nuvio-old': {'progress_key': 'old-baseline'}},
            'outbound': {'nuvio-old': {'progress_key': 'old-baseline'}},
            'records': {'progress': [{'content_id': 'nuvio-old', 'progress_key': 'old-baseline'}]},
        })
        db = AsyncMock()
        db.get.return_value = baseline
        conn = MediaServerConnection(id=5, user_id=7, type='nuvio', name='Fixture',
            url='https://example.test', token='old', server_user_id='1')
        patches, started = _nuvio_call_patches(progress_rows=remote_progress,
            watched_rows=remote_watched, followup_rows=[])
        try:
            await dismiss_nuvio(db, conn, {
                'content_id': 'nuvio-old', 'content_ids': ['nuvio-old', 'tt42', 'tmdb:42'],
                'content_type': 'series', 'tmdb_id': 42, 'imdb_id': 'tt42',
                'observed_at': '2026-09-20T12:00:00Z',
            })
        finally:
            for item in reversed(patches):
                item.stop()

        rpc = started[6]
        rpc.assert_awaited_once()
        self.assertEqual(rpc.await_args.args[-2], 'sync_delete_watch_progress')
        self.assertEqual(rpc.await_args.args[-1]['p_keys'], [
            'nuvio-old_s1e2', 'remote-progress_s2e1',
        ])
        visibility = started[7]
        hidden = set(visibility.await_args.kwargs['hide'])
        self.assertTrue({'nuvio-old', 'tt42', 'tmdb:42', 'remote-progress', 'remote-history'} <= hidden)
        self.assertEqual(visibility.await_args.kwargs['seeds'], {
            key: {(1, 1)} for key in hidden})
        self.assertEqual(baseline.snapshot['progress'], {})
        self.assertEqual(baseline.snapshot['records']['progress'], [])
        self.assertEqual(baseline.snapshot['outbound'], {})
        # Watched episode history is read for identity/race checks, never deleted.
        self.assertEqual([call.args[-2] for call in rpc.await_args_list], ['sync_delete_watch_progress'])

    async def test_dismiss_allows_older_remote_progress_but_rejects_newer_edits_before_mutation(self):
        conn = MediaServerConnection(id=5, user_id=7, type='nuvio', name='Fixture',
            url='https://example.test', token='old', server_user_id='1')
        record = {'content_id': 'tt42', 'content_ids': ['tt42', 'tmdb:42'],
            'content_type': 'series', 'tmdb_id': 42, 'observed_at': '2026-09-20T12:00:00Z'}
        remote = [{'content_id': 'tmdb:42', 'content_type': 'series',
            'progress_key': 'tmdb:42_s1e1', 'position': 70000, 'duration': 90000,
            'updated_at': '2026-09-21T12:00:00Z'}]
        baseline = SimpleNamespace(snapshot={'mappings': {'tmdb:42': '42'}, 'progress': {}})
        db = AsyncMock()
        db.get.return_value = baseline
        patches, started = _nuvio_call_patches(progress_rows=remote)
        try:
            with self.assertRaises(RemotePlaybackChanged):
                await dismiss_nuvio(db, conn, record)
        finally:
            for item in reversed(patches):
                item.stop()
        started[6].assert_not_awaited()
        started[7].assert_not_awaited()
        self.assertEqual(baseline.snapshot['progress'], {})

    async def test_legacy_next_up_only_dismissal_still_clears_current_resume(self):
        conn = MediaServerConnection(id=5, user_id=7, type='nuvio', name='Fixture',
            url='https://example.test', token='old', server_user_id='1')
        remote = [{'content_id': 'tt1', 'content_type': 'series', 'season': 1, 'episode': 3,
            'progress_key': 'tt1_s1e3', 'position': 45000, 'duration': 90000,
            'updated_at': '2026-09-18T12:00:00Z'}]
        db = AsyncMock()
        db.get.return_value = None
        patches, started = _nuvio_call_patches(progress_rows=remote, followup_rows=[])
        try:
            await dismiss_nuvio(db, conn, {'content_id': 'tt1', 'content_type': 'series',
                'next_up_only': True, 'observed_at': '2026-09-20T12:00:00Z'})
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(started[6].await_args.args[-2], 'sync_delete_watch_progress')
        self.assertEqual(started[6].await_args.args[-1]['p_keys'], ['tt1_s1e3'])
        self.assertEqual(started[7].await_args.kwargs['hide'], ['tt1'])
        self.assertEqual(started[7].await_args.kwargs['seeds'], {})

    async def test_source_visibility_only_dismiss_fetches_retained_episode_history(self):
        watched = [{'content_id': 'tt42', 'content_type': 'series', 'season': 2, 'episode': 7}]
        baseline = SimpleNamespace(snapshot={'mappings': {'old42': 42}})
        db = AsyncMock()
        db.get.return_value = baseline
        conn = MediaServerConnection(id=5, user_id=7, type='nuvio', name='Fixture',
            url='https://example.test', token='old', server_user_id='1')
        patches, started = _nuvio_call_patches(progress_rows=[], watched_rows=watched)
        try:
            await dismiss_nuvio(db, conn, {'content_id': 'tt42', 'content_ids': ['tt42'],
                'content_type': 'series', 'tmdb_id': 42, 'imdb_id': 'tt42'}, visibility_only=True)
        finally:
            for item in reversed(patches):
                item.stop()
        started[4].assert_not_awaited()
        started[5].assert_awaited_once()
        started[6].assert_not_awaited()
        self.assertEqual(set(started[7].await_args.kwargs['hide']), {'old42', 'tt42', 'tmdb:42'})
        self.assertEqual(started[7].await_args.kwargs['seeds'], {
            key: {(2, 7)} for key in ('old42', 'tt42', 'tmdb:42')})

    async def test_visibility_only_watching_clears_only_confirmed_synthetic_seed(self):
        synthetic = {'content_id': 'tt1', 'content_type': 'series', 'progress_key': 'tt1_s1e1',
            'position': 1000, 'duration': 90000, 'season': 1, 'episode': 1}
        real = {'content_id': 'tt1', 'content_type': 'series', 'progress_key': 'tt1_s1e2',
            'position': 42000, 'duration': 90000, 'season': 1, 'episode': 2}
        baseline = SimpleNamespace(snapshot={
            'progress': {'seed': dict(synthetic), 'real': dict(real)},
            'resume': {'tt1': {'progress_key': 'tt1_s1e1'}},
            'outbound': {'tt1': {'progress_key': 'tt1_s1e1', 'synthetic_resume': True}},
            'records': {'progress': [dict(synthetic), dict(real)]},
        })
        db = AsyncMock()
        db.get.return_value = baseline
        conn = MediaServerConnection(id=5, user_id=7, type='nuvio', name='Fixture',
            url='https://example.test', token='old', server_user_id='1')
        patches, started = _nuvio_call_patches(progress_rows=[synthetic, real],
            followup_rows=[real])
        clear = patch('core.nuvio_projection.progress_keys_to_clear', AsyncMock(return_value=['tt1_s1e1']))
        clear.start()
        try:
            await show_nuvio_next_up(db, conn, {
                'content_id': 'tt1', 'content_ids': ['tt1'], 'content_type': 'series',
            })
        finally:
            clear.stop()
            for item in reversed(patches):
                item.stop()
        self.assertEqual(started[6].await_args.args[-2], 'sync_delete_watch_progress')
        self.assertEqual(started[6].await_args.args[-1]['p_keys'], ['tt1_s1e1'])
        self.assertEqual(started[7].await_args.kwargs['show'], ['tt1'])
        self.assertEqual(set(baseline.snapshot['progress']), {'real'})
        self.assertEqual(baseline.snapshot['records']['progress'], [real])
        self.assertEqual(baseline.snapshot['outbound'], {})


if __name__ == '__main__':
    unittest.main()
