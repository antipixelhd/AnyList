"""Native TV/mobile settings round trips and recoverable visibility changes."""
import copy
import json
import os
import unittest
import httpx
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core import nuvio
from core.nuvio_settings import MobileSettings, TVSettings, next_up_seeds
from core.nuvio_visibility import record_visibility_echo
from models import MediaServerConnection, Media, MediaType


class ProfileSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.blobs = {
            'tv': {'version': 8, 'unknown': 'keep', 'features': {
                'appearance': {'theme': 'dark'}, 'trakt_settings': {
                    'custom': True, 'dismissed_next_up_keys': {
                        'type': 'string_set', 'value': ['other', 'tt1|1|2'], 'extra': 'keep'}}}},
            'mobile': {'version': 9, 'unknown': 'mobile-only', 'features': {
                'player_settings': {'speed': 1.5},
                'continue_watching_settings_payload': json.dumps({
                    'isVisible': True, 'style': 'Card', 'upNextFromFurthestEpisode': False,
                    'futurePreference': {'label': 'é'},
                    'dismissedNextUpKeys': ['other|2|1', 'tt1|9|9']})}},
        }
        self.calls = []
        self.watched = []
        self.progress = []
        self.fail_mobile = False
        self.reorder_readback = False
        self.discard_writes = False

    async def rpc(self, _client, _url, _token, operation, params):
        self.calls.append((operation, copy.deepcopy(params)))
        self.assertEqual(params['p_profile_id'], 3)
        if operation == 'sync_pull_watched_items':
            return copy.deepcopy(self.watched)
        if operation == 'sync_pull_watch_progress':
            return copy.deepcopy(self.progress)
        if operation == 'sync_delete_watch_progress':
            self.progress = [row for row in self.progress if row['progress_key'] not in params['p_keys']]
            return None
        platform = params['p_platform']
        if operation == 'sync_pull_profile_settings_blob':
            blob = copy.deepcopy(self.blobs.get(platform))
            if blob is None:
                return []
            if self.reorder_readback and blob:
                if platform == 'mobile':
                    payload = json.loads(blob['features']['continue_watching_settings_payload'])
                    payload['dismissedNextUpKeys'].reverse()
                    blob['features']['continue_watching_settings_payload'] = json.dumps(payload, indent=2)
                else:
                    blob['features']['trakt_settings']['dismissed_next_up_keys']['value'].reverse()
            return [{'settings_json': blob}]
        self.assertEqual(operation, 'sync_push_profile_settings_blob')
        if platform == 'mobile' and self.fail_mobile:
            self.fail_mobile = False
            raise nuvio.NuvioAPIError('temporary mobile failure')
        if not self.discard_writes:
            self.blobs[platform] = copy.deepcopy(params['p_settings_json'])

    async def update(self, **kwargs):
        with patch.object(nuvio, '_rpc', AsyncMock(side_effect=self.rpc)):
            return await nuvio.update_next_up_dismissals(None, 'https://example.test', 'fixture', 3, **kwargs)

    def writes(self):
        return [params['p_platform'] for operation, params in self.calls
            if operation == 'sync_push_profile_settings_blob']

    async def test_native_platform_merges_preserve_preferences_and_all_known_seeds(self):
        original = copy.deepcopy(self.blobs)
        self.assertTrue(await self.update(hide=['tt1'], seeds={'tt1': {(1, 2), (2, 7)}}))
        self.assertEqual(self.writes(), ['tv', 'mobile'])
        self.assertEqual(TVSettings.dismissals(self.blobs['tv']), {'other', 'tt1'})
        self.assertEqual(MobileSettings.dismissals(self.blobs['mobile']),
            {'other|2|1', 'tt1|9|9', 'tt1|1|2', 'tt1|2|7'})
        self.assertEqual(self.blobs['tv']['version'], 8)
        self.assertEqual(self.blobs['tv']['unknown'], 'keep')
        self.assertEqual(self.blobs['tv']['features']['appearance'], {'theme': 'dark'})
        self.assertEqual(self.blobs['tv']['features']['trakt_settings']['custom'], True)
        self.assertEqual(self.blobs['tv']['features']['trakt_settings']['dismissed_next_up_keys']['extra'], 'keep')
        self.assertEqual(self.blobs['mobile']['version'], 9)
        self.assertEqual(self.blobs['mobile']['unknown'], 'mobile-only')
        self.assertEqual(self.blobs['mobile']['features']['player_settings'], {'speed': 1.5})
        before = json.loads(original['mobile']['features']['continue_watching_settings_payload'])
        after = MobileSettings.payload(self.blobs['mobile'])
        before.pop('dismissedNextUpKeys')
        after.pop('dismissedNextUpKeys')
        self.assertEqual(after, before)
        self.assertNotIn('trakt_settings', self.blobs['mobile']['features'])
        self.assertNotIn('continue_watching_settings_payload', self.blobs['tv']['features'])
        self.calls.clear()
        self.assertFalse(await self.update(hide=['tt1'], seeds={'tt1': {(1, 2), (2, 7)}}))
        self.assertEqual(self.writes(), [])

    async def test_on_written_reports_only_exact_changed_keys_per_platform(self):
        callback = AsyncMock()
        self.blobs['tv']['features']['trakt_settings']['dismissed_next_up_keys']['value'].append('old|1|2')
        await self.update(hide=['tt2'], seeds={'tt2': {(1, 3)}}, on_written=callback)
        self.assertEqual(callback.await_args_list, [
            unittest.mock.call({'tv': {'tt2': True}}),
            unittest.mock.call({'mobile': {'tt2|1|3': True}}),
        ])

    async def test_on_written_keeps_confirmed_tv_delta_when_mobile_fails(self):
        self.fail_mobile = True
        callback = AsyncMock()
        with self.assertRaisesRegex(nuvio.NuvioAPIError, 'mobile.*temporary'):
            await self.update(hide=['tt2'], seeds={'tt2': {(1, 3)}}, on_written=callback)
        callback.assert_awaited_once_with({'tv': {'tt2': True}})

    async def test_confirmed_noop_show_retires_echo_by_title_alias(self):
        callback = AsyncMock()
        self.assertFalse(await self.update(show=['not-dismissed'], on_written=callback))
        self.assertEqual(callback.await_args_list, [
            unittest.mock.call({'tv': {'not-dismissed': False}}),
            unittest.mock.call({'mobile': {'not-dismissed': False}}),
        ])
        self.assertEqual(self.writes(), [])

    async def test_changed_show_also_retires_markers_for_already_visible_aliases(self):
        self.blobs = {
            'tv': {'features': {'trakt_settings': {'dismissed_next_up_keys': {
                'type': 'string_set', 'value': ['tt2']}}}},
            'mobile': {'features': {'continue_watching_settings_payload': json.dumps({
                'dismissedNextUpKeys': ['tt2|1|2']})}},
        }
        callback = AsyncMock()
        await self.update(show=['tt1', 'tt2'], on_written=callback)
        self.assertEqual(callback.await_args_list, [
            unittest.mock.call({'tv': {'tt2': False, 'tt1': False}}),
            unittest.mock.call({'mobile': {'tt2|1|2': False, 'tt2': False, 'tt1': False}}),
        ])

    async def test_echo_recorder_adds_exact_hides_and_clears_mobile_alias_markers(self):
        baseline = SimpleNamespace(snapshot={
            'mappings': {'tt1': 1},
            'cw_visibility_echo': {
                'tv': {'unrelated-tv': True},
                'mobile': {'tt1|1|2': True, 'tt2|3|4': True},
            },
        })
        db = SimpleNamespace(get=AsyncMock(return_value=baseline))
        conn = SimpleNamespace(id=4)

        await record_visibility_echo(db, conn, {
            'tv': {'new-tv': True},
            'mobile': {'tt1': False},
        })

        self.assertEqual(baseline.snapshot['mappings'], {'tt1': 1})
        self.assertEqual(baseline.snapshot['cw_visibility_echo'], {
            'tv': {'unrelated-tv': True, 'new-tv': True},
            'mobile': {'tt2|3|4': True},
        })

    async def test_empty_visibility_intent_makes_no_remote_calls(self):
        with patch.object(nuvio, '_rpc', AsyncMock()) as rpc:
            self.assertFalse(await nuvio.update_next_up_dismissals(None, 'https://example.test', 'fixture', 3))
        rpc.assert_not_awaited()

    async def test_transport_failure_identifies_the_failed_platform(self):
        for platform in ('tv', 'mobile'):
            with self.subTest(platform=platform):
                async def rpc(client, url, token, operation, params):
                    if params.get('p_platform') == platform:
                        raise httpx.ReadTimeout('exhausted settings retries')
                    return await self.rpc(client, url, token, operation, params)
                with patch.object(nuvio, '_rpc', AsyncMock(side_effect=rpc)), \
                        self.assertRaisesRegex(nuvio.NuvioAPIError, platform + '.*exhausted'):
                    await nuvio.update_next_up_dismissals(None, 'https://example.test', 'fixture', 3,
                        show=['tt2'])

    async def test_invalid_history_seed_fails_before_any_settings_write(self):
        for season in ('2', True):
            with self.subTest(season=season):
                self.watched = [{'content_id': 'tt2', 'content_type': 'series', 'season': season, 'episode': 1}]
                with self.assertRaisesRegex(nuvio.NuvioAPIError, 'mobile.*coordinates'):
                    await self.update(hide=['tt2'])
                self.assertEqual(self.writes(), [])

    async def test_show_wins_and_removes_every_episode_for_all_aliases(self):
        await self.update(hide=['tt1', 'tmdb:42'], seeds={
            'tt1': {(1, 2), (2, 7)}, 'tmdb:42': {(1, 2), (2, 7)}})
        self.assertTrue(await self.update(hide=['tt1'], show=['tt1', 'tmdb:42']))
        self.assertEqual(TVSettings.dismissals(self.blobs['tv']), {'other'})
        self.assertEqual(MobileSettings.dismissals(self.blobs['mobile']), {'other|2|1'})
        self.assertFalse(await self.update(show=['tt1', 'tmdb:42']))

    async def test_absent_and_empty_blobs_use_distinct_versions(self):
        for empty in (None, {}):
            with self.subTest(empty=empty):
                self.blobs = {'tv': empty, 'mobile': empty}
                await self.update(hide=['tt2'], seeds={'tt2': {(2, 7)}})
                self.assertEqual(self.blobs['tv'], {'version': 1, 'features': {'trakt_settings': {
                    'dismissed_next_up_keys': {'type': 'string_set', 'value': ['tt2']}}}})
                self.assertEqual(self.blobs['mobile']['version'], 4)
                self.assertEqual(MobileSettings.payload(self.blobs['mobile']), {'dismissedNextUpKeys': ['tt2|2|7']})

    async def test_missing_mobile_payload_initializes_without_replacing_other_settings(self):
        self.blobs['mobile']['features'].pop('continue_watching_settings_payload')
        await self.update(hide=['tt2'], seeds={'tt2': {(1, 1)}})
        self.assertEqual(MobileSettings.payload(self.blobs['mobile']), {'dismissedNextUpKeys': ['tt2|1|1']})
        self.assertEqual(self.blobs['mobile']['features']['player_settings'], {'speed': 1.5})

    async def test_malformed_blobs_are_rejected_without_overwriting_platform(self):
        invalid = [
            ('tv', {'features': []}),
            ('tv', {'features': {'trakt_settings': []}}),
            ('tv', {'features': {'trakt_settings': {'dismissed_next_up_keys': {'type': 'string', 'value': []}}}}),
            ('mobile', {'features': {'continue_watching_settings_payload': {}}}),
            ('mobile', {'features': {'continue_watching_settings_payload': '{broken'}}),
            ('mobile', {'features': {'continue_watching_settings_payload': '[]'}}),
            ('mobile', {'features': {'continue_watching_settings_payload': '{"dismissedNextUpKeys":[4]}'}}),
        ]
        for platform, blob in invalid:
            with self.subTest(platform=platform, blob=blob):
                self.setUp()
                self.blobs[platform] = copy.deepcopy(blob)
                with self.assertRaisesRegex(nuvio.NuvioAPIError, platform):
                    await self.update(hide=['tt2'], seeds={'tt2': {(1, 1)}})
                self.assertEqual(self.blobs[platform], blob)
                self.assertNotIn(platform, self.writes())

    async def test_readback_accepts_reordered_sets_and_formatted_mobile_json(self):
        self.reorder_readback = True
        self.assertTrue(await self.update(hide=['tt2'], seeds={'tt2': {(2, 3), (1, 4)}}))
        self.assertEqual(self.writes(), ['tv', 'mobile'])
        self.assertEqual(MobileSettings.dismissals(self.blobs['mobile']),
            {'other|2|1', 'tt1|9|9', 'tt2|2|3', 'tt2|1|4'})

    async def test_unconfirmed_platform_write_fails(self):
        for platform in ('tv', 'mobile'):
            with self.subTest(platform=platform):
                self.setUp()
                if platform == 'mobile':
                    self.blobs['tv']['features']['trakt_settings']['dismissed_next_up_keys']['value'].append('tt2')
                self.discard_writes = True
                with self.assertRaisesRegex(nuvio.NuvioAPIError, platform + '.*did not confirm'):
                    await self.update(hide=['tt2'], seeds={'tt2': {(1, 1)}})

    async def test_retry_after_mobile_failure_keeps_tv_success_and_retries_only_mobile(self):
        self.fail_mobile = True
        with self.assertRaisesRegex(nuvio.NuvioAPIError, 'mobile.*temporary'):
            await self.update(hide=['tt2'], seeds={'tt2': {(1, 1)}})
        self.assertIn('tt2', TVSettings.dismissals(self.blobs['tv']))
        self.assertNotIn('tt2|1|1', MobileSettings.dismissals(self.blobs['mobile']))
        self.calls.clear()
        self.assertTrue(await self.update(hide=['tt2'], seeds={'tt2': {(1, 1)}}))
        self.assertEqual(self.writes(), ['mobile'])
        self.assertIn('tt2|1|1', MobileSettings.dismissals(self.blobs['mobile']))

    async def test_history_fallback_uses_actual_seeds_and_never_invents_wildcards(self):
        self.watched = [
            {'content_id': 'tt2', 'content_type': 'series', 'season': None, 'episode': 3},
            {'content_id': 'movie', 'content_type': 'movie'},
        ]
        await self.update(hide=['tt2', 'no-history', 'movie'])
        self.assertEqual(MobileSettings.dismissals(self.blobs['mobile']),
            {'other|2|1', 'tt1|9|9', 'tt2|-1|3'})
        self.assertEqual([name for name, _ in self.calls].count('sync_pull_watched_items'), 1)
        self.assertEqual(next_up_seeds(self.watched), {'tt2': {(-1, 3)}})

    async def test_playback_clear_suppresses_both_platforms_from_retained_history(self):
        self.watched = [{'content_id': 'tt2', 'content_type': 'series', 'season': 1, 'episode': 4}]
        self.progress = [{'content_id': 'tt2', 'content_type': 'series', 'progress_key': 'tt2_s1e5'}]
        original_watched = copy.deepcopy(self.watched)
        with patch.object(nuvio, '_rpc', AsyncMock(side_effect=self.rpc)), \
                patch.object(nuvio, 'refresh_session', AsyncMock(return_value=SimpleNamespace(
                    access_token='fixture', refresh_token='rotated'))):
            counts = await nuvio.clear_sync_data('https://example.test', 'fixture', 3, playback=True)
        self.assertEqual(counts, {'collection': 0, 'watched': 0, 'playback': 1})
        self.assertEqual(self.progress, [])
        self.assertEqual(self.watched, original_watched)
        self.assertEqual(TVSettings.dismissals(self.blobs['tv']), {'other', 'tt1|1|2', 'tt2'})
        self.assertEqual(MobileSettings.dismissals(self.blobs['mobile']), {'other|2|1', 'tt1|9|9', 'tt2|1|4'})
        self.assertNotIn('sync_delete_watched_items', [name for name, _ in self.calls])

    async def test_watch_visibility_fetches_seed_history_and_retries_partial_failure(self):
        from core.nuvio_visibility import sync_next_up_visibility
        from tests.test_nuvio import _Result
        media = Media(id=1, title='Paused', media_type=MediaType.series, tmdb_id=42,
            imdb_id='tt42', tmdb_data={})
        conn = MediaServerConnection(id=4, user_id=7, type='nuvio', name='Fixture',
            url='https://example.test', token='fixture', server_user_id='3')
        baseline = SimpleNamespace(snapshot={'mappings': {'old42': '42'}})
        db = SimpleNamespace(execute=AsyncMock(return_value=_Result(rows=[(media, 'paused')])),
            get=AsyncMock(return_value=baseline), refresh=AsyncMock())
        token_context = AsyncMock()
        token_db = AsyncMock()
        token_context.__aenter__.return_value = token_db
        self.watched = [{'content_id': 'old42', 'content_type': 'series', 'season': 2, 'episode': 7}]
        self.fail_mobile = True
        with patch('db.AsyncSessionLocal', return_value=token_context), \
                patch.object(nuvio, 'refresh_session', AsyncMock(return_value=SimpleNamespace(
                    access_token='fixture', refresh_token='rotated'))), \
                patch.object(nuvio, '_rpc', AsyncMock(side_effect=self.rpc)):
            with self.assertRaisesRegex(nuvio.NuvioAPIError, 'mobile'):
                await sync_next_up_visibility(db, conn, [{'content_id': 'old42', 'content_type': 'series'}])
            self.assertEqual(conn.token, 'rotated')
            token_db.commit.assert_awaited_once()
            self.calls.clear()
            await sync_next_up_visibility(db, conn, [{'content_id': 'old42', 'content_type': 'series'}])
        self.assertEqual(self.writes(), ['mobile'])
        self.assertEqual(MobileSettings.dismissals(self.blobs['mobile']),
            {'tt1|9|9', 'other|2|1', 'old42|2|7', 'tt42|2|7', 'tmdb:42|2|7'})
        self.assertNotIn('old42|-1|-1', MobileSettings.dismissals(self.blobs['mobile']))

    async def test_scheduled_push_projects_both_platforms_from_shared_history(self):
        from routers.sync import _run_full_push
        from tests.test_nuvio import _Result, _SessionCM, _approved_stream_gate_results
        media = Media(id=1, title='Dropped', media_type=MediaType.series, tmdb_id=42,
            imdb_id='tt42', tmdb_data={})
        conn = SimpleNamespace(id=4, user_id=7, type='nuvio', url='https://example.test',
            token='fixture', server_user_id='3', push_collection=False, push_watched=False,
            push_playback=True, stremio_pushed_library_ids=None)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            _Result(scalars=[99]), _Result(scalars=[conn]), *_approved_stream_gate_results(7),
            _Result(scalars=[SimpleNamespace(tmdb_api_key='fixture')]), None,
            _Result(rows=[(media, 'dropped')]), None,
        ]), commit=AsyncMock(), refresh=AsyncMock(),
            get=AsyncMock(return_value=SimpleNamespace(approved=True, snapshot={'mappings': {'tt42': 42}})))
        self.watched = [{'content_id': 'tt42', 'content_type': 'series', 'season': 2, 'episode': 7}]
        with patch('routers.sync.async_sessionmaker', lambda *args, **kwargs: lambda: _SessionCM(db)), \
                patch('core.nuvio_projection.build_progress_items', AsyncMock(return_value=[])), \
                patch('core.nuvio_projection.progress_keys_to_clear', AsyncMock(return_value=[])), \
                patch.object(nuvio, 'refresh_session', AsyncMock(return_value=SimpleNamespace(
                    access_token='fixture', refresh_token='rotated'))), \
                patch.object(nuvio, '_rpc', AsyncMock(side_effect=self.rpc)):
            await _run_full_push(user_id=7, connection_id=4, job_id=99)
        self.assertEqual(self.writes(), ['tv', 'mobile'])
        self.assertEqual(TVSettings.dismissals(self.blobs['tv']), {'tt1|1|2', 'other', 'tt42', 'tmdb:42'})
        self.assertEqual(MobileSettings.dismissals(self.blobs['mobile']),
            {'tt1|9|9', 'other|2|1', 'tt42|2|7', 'tmdb:42|2|7'})
