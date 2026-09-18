import os
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')
from core.stream_actions import dismiss_stremio, dismiss_nuvio, RemotePlaybackChanged
from models import MediaServerConnection


class StreamActionAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_nuvio_restore_and_reset_use_separate_progress_history_rpcs(self):
        conn=MediaServerConnection(id=987,user_id=1,type='nuvio',name='Fixture',url='https://example.test',token='old',server_user_id='1')
        record={'content_id':'tt1','content_type':'series','position':30,'duration':100,'season':1,'episode':2,'progress_key':'tt1_s1e2'}
        token_db=AsyncMock();context=AsyncMock();context.__aenter__.return_value=token_db
        with patch('db.AsyncSessionLocal',return_value=context), \
             patch('core.nuvio.refresh_session',AsyncMock(return_value=SimpleNamespace(refresh_token='rotated',access_token='fixture'))), \
             patch('core.nuvio._pull_watch_progress',AsyncMock(return_value=[])) as progress, \
             patch('core.nuvio._pull_watched_items',AsyncMock(return_value=[{'content_id':'tt1','content_type':'series','season':1,'episode':1}])) as watched, \
             patch('core.nuvio._rpc',AsyncMock()) as write:
            await dismiss_nuvio(AsyncMock(),conn,record,restore=True)
            self.assertEqual(write.await_args.args[-2],'sync_push_watch_progress')
            self.assertEqual(write.await_args.args[-1]['p_entries'],[record])
            watched.assert_not_awaited()
            write.reset_mock();progress.return_value=[record]
            await dismiss_nuvio(AsyncMock(),conn,record,restore=True)
            write.assert_not_awaited()
            await dismiss_nuvio(AsyncMock(),conn,{'content_id':'tt1','content_type':'series','deleted_at':'2026-09-18T12:00:00+00:00'},reset=True)
            self.assertEqual([call.args[-2] for call in write.await_args_list],['sync_delete_watch_progress','sync_delete_watched_items'])
            self.assertEqual(write.await_args_list[0].args[-1]['p_keys'],['tt1_s1e2'])
            self.assertEqual(write.await_args_list[1].args[-1]['p_keys'],[{'content_id':'tt1','season':1,'episode':1}])
            write.reset_mock();progress.return_value=[record]*200
            with self.assertRaises(RemotePlaybackChanged):
                await dismiss_nuvio(AsyncMock(),conn,{'content_id':'tt1','content_type':'series','deleted_at':'2026-09-18T12:00:00+00:00'},reset=True)
            write.assert_not_awaited()

    async def test_restore_preserves_library_and_rejects_a_different_episode(self):
        record={'content_id':'tt1','content_type':'series','season':1,'episode':2,'position':30,'duration':100}
        remote={'_id':'tt1','type':'series','removed':False,'temp':False,'state':{'timeOffset':0,'watched':'history'}}
        with patch('core.stremio.datastore_get',AsyncMock(return_value=[remote])), patch('core.stremio.datastore_put',AsyncMock()) as write:
            await dismiss_stremio('fixture',record,restore=True)
        restored=write.await_args.args[1][0]
        self.assertFalse(restored['removed']);self.assertFalse(restored['temp'])
        self.assertEqual(restored['state']['watched'],'history')
        self.assertEqual(restored['state']['video_id'],'tt1:1:2')
        self.assertEqual(restored['state']['timeOffset'],30)
        for video in ('tt1:1:2','tt1:2:4'):
            restored['state']['video_id']=video
            with patch('core.stremio.datastore_get',AsyncMock(return_value=[restored])), patch('core.stremio.datastore_put',AsyncMock()) as write:
                if video=='tt1:1:2':await dismiss_stremio('fixture',record,restore=True)
                else:
                    with self.assertRaises(RemotePlaybackChanged):await dismiss_stremio('fixture',record,restore=True)
                write.assert_not_awaited()

    async def test_reset_clears_history_preserves_library_and_rejects_newer_activity(self):
        record={'content_id':'tt1','content_type':'movie','deleted_at':'2026-09-18T12:00:00+00:00'}
        remote={'_id':'tt1','type':'movie','removed':False,'temp':False,'_mtime':'2026-09-17T12:00:00Z',
            'state':{'timeOffset':30,'duration':100,'watched':'history','timesWatched':2}}
        with patch('core.stremio.datastore_get',AsyncMock(return_value=[remote])), patch('core.stremio.datastore_put',AsyncMock()) as write:
            await dismiss_stremio('fixture',record,reset=True)
        cleared=write.await_args.args[1][0]
        self.assertFalse(cleared['removed']);self.assertFalse(cleared['temp'])
        self.assertEqual(cleared['state']['timesWatched'],0)
        self.assertIsNone(cleared['state']['watched'])
        self.assertEqual(cleared['state']['timeOffset'],0)
        with patch('core.stremio.datastore_get',AsyncMock(return_value=[cleared])), patch('core.stremio.datastore_put',AsyncMock()) as write:
            await dismiss_stremio('fixture',record,reset=True)
            write.assert_not_awaited()
        remote['_mtime']='2026-09-19T12:00:00Z'
        with patch('core.stremio.datastore_get',AsyncMock(return_value=[remote])), patch('core.stremio.datastore_put',AsyncMock()) as write:
            with self.assertRaises(RemotePlaybackChanged):await dismiss_stremio('fixture',record,reset=True)
            write.assert_not_awaited()

    async def test_nuvio_rotated_token_commits_before_progress_delete_and_survives_failure(self):
        conn=MediaServerConnection(id=987,user_id=1,type='nuvio',name='Fixture',url='https://example.test',token='old',server_user_id='1')
        record={'content_id':'tt1','position':30,'duration':100,'season':1,'episode':2,'progress_key':'tt1_s1e2'}
        token_db=AsyncMock()
        context=AsyncMock()
        context.__aenter__.return_value=token_db
        async def reject(*args):
            token_db.commit.assert_awaited_once()
            self.assertEqual(conn.token,'rotated')
            self.assertEqual(args[-2],'sync_delete_watch_progress')
            self.assertEqual(args[-1],{'p_profile_id':1,'p_keys':['tt1_s1e2']})
            raise TimeoutError()
        with patch('db.AsyncSessionLocal',return_value=context), \
             patch('core.nuvio.refresh_session',AsyncMock(return_value=SimpleNamespace(refresh_token='rotated',access_token='fixture'))), \
             patch('core.nuvio._pull_watch_progress',AsyncMock(return_value=[record])), \
             patch('core.nuvio._rpc',AsyncMock(side_effect=reject)):
            with self.assertRaises(TimeoutError):
                await dismiss_nuvio(AsyncMock(),conn,record)
        self.assertEqual(conn.token,'rotated')

    async def test_dismiss_preserves_library_history_and_unknown_fields(self):
        remote = {'_id':'tt1','removed':False,'temp':False,'name':'Fixture',
            'extra':'preserve','state':{'timeOffset':30,'duration':100,'watched':'history',
            'timesWatched':2,'lastWatched':'known-date','video_id':'tt1'}}
        with patch('core.stremio.datastore_get',AsyncMock(return_value=[remote])), \
             patch('core.stremio.datastore_put',AsyncMock()) as write:
            await dismiss_stremio('fixture',{'content_id':'tt1','position':30,'duration':100})
        payload=write.await_args.args[1][0]
        self.assertEqual(payload['state'],{**remote['state'],'timeOffset':0})
        for key in ('removed','temp','extra','name'):
            self.assertEqual(payload[key],remote[key])
        self.assertEqual(remote['state']['timeOffset'],30)

    async def test_newer_playback_is_not_overwritten_and_already_dismissed_is_idempotent(self):
        for position in (40,0):
            with self.subTest(position=position), \
                 patch('core.stremio.datastore_get',AsyncMock(return_value=[{'_id':'tt1','state':{'timeOffset':position,'duration':100}}])), \
                 patch('core.stremio.datastore_put',AsyncMock()) as write:
                if position:
                    with self.assertRaises(RemotePlaybackChanged):
                        await dismiss_stremio('fixture',{'content_id':'tt1','position':30,'duration':100})
                else:
                    await dismiss_stremio('fixture',{'content_id':'tt1','position':30,'duration':100})
                write.assert_not_awaited()
