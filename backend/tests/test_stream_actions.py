import os
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')
from core.stream_actions import (dismiss_stremio, dismiss_nuvio, push_stremio_progress,
    push_nuvio_progress, queue_restorations, RemotePlaybackChanged)
from core import stremio
from models import MediaServerConnection, Media, MediaType, Show
from models.tracking import StreamAction, StreamBaseline, TrackedEntry


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class _QueueDB:
    def __init__(self, responses, baseline):
        self.responses = iter(responses)
        self.baseline = baseline
        self.added = []

    async def execute(self, _statement):
        return _FakeResult(next(self.responses))

    async def get(self, model, _identity):
        if model is StreamBaseline:
            return self.baseline
        return None

    def add(self, value):
        self.added.append(value)


class StreamActionAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_stremio_progress_creates_temporary_item_for_new_watching_title(self):
        record={'content_id':'tt1234567','content_type':'movie','title':'Runner',
            'position':1000,'duration':120_000,
            'observed_at':'2026-09-20T12:00:00Z','last_watched':'2026-09-20T12:00:00Z'}
        stored=[]
        async def save(_token, items):
            stored.extend(items)
        with patch('core.stremio.datastore_get',AsyncMock(side_effect=[[],stored])) as read, \
             patch('core.stremio.datastore_put',AsyncMock(side_effect=save)):
            await push_stremio_progress('fixture',record)
        self.assertFalse(stored[0]['removed'])
        self.assertTrue(stored[0]['temp'])
        self.assertEqual(stored[0]['state']['timeOffset'],1000)
        self.assertEqual(stored[0]['state']['video_id'],'tt1234567')
        self.assertTrue(read.await_args_list[0].kwargs['allow_missing'])

    async def test_stremio_progress_repairs_hidden_same_position_movie(self):
        record={'content_id':'tt1234567','content_type':'movie','position':1000,
            'duration':120_000,'observed_at':'2026-09-20T12:00:00Z'}
        hidden={'_id':'tt1234567','type':'movie','removed':True,'temp':False,
            '_mtime':'2026-09-21T12:00:00Z',
            'state':{'timeOffset':1000,'duration':120_000,'video_id':'tt1234567'}}
        saved=[]
        async def save(_token, items):
            saved.extend(items)
        with patch('core.stremio.datastore_get',AsyncMock(side_effect=[[hidden],saved])), \
             patch('core.stremio.datastore_put',AsyncMock(side_effect=save)):
            await push_stremio_progress('fixture',record)
        self.assertFalse(saved[0]['removed'])
        self.assertTrue(saved[0]['temp'])
        self.assertEqual(saved[0]['state']['video_id'],'tt1234567')

    async def test_stremio_progress_rejects_a_readback_that_remains_hidden(self):
        record={'content_id':'tt1234567','content_type':'movie','position':1000,
            'duration':120_000,'observed_at':'2026-09-20T12:00:00Z'}
        hidden={'_id':'tt1234567','type':'movie','removed':True,'temp':True,
            '_mtime':'2026-09-19T12:00:00Z',
            'state':{'timeOffset':0,'duration':0,'video_id':None}}
        after={'_id':'tt1234567','type':'movie','removed':True,'temp':True,
            'state':{'timeOffset':1000,'duration':120_000,'video_id':'tt1234567'}}
        with patch('core.stremio.datastore_get',AsyncMock(side_effect=[[hidden],[after]])), \
             patch('core.stremio.datastore_put',AsyncMock()):
            with self.assertRaises(stremio.StremioAPIError):
                await push_stremio_progress('fixture',record)

    async def test_stremio_progress_upsert_is_confirmed_by_readback(self):
        last_watched_ms=1_789_905_600_000
        record={'content_id':'tt1','content_type':'movie','position':30_000,'duration':100_000,
            'last_watched':last_watched_ms,'observed_at':'2026-09-20T12:00:00Z'}
        before={'_id':'tt1','type':'movie','state':{'timeOffset':0,'duration':0}}
        after={'_id':'tt1','type':'movie','state':{'timeOffset':30_000,'duration':100_000,'video_id':'tt1'}}
        with patch('core.stremio.datastore_get',AsyncMock(side_effect=[[before],[after]])) as read, \
             patch('core.stremio.datastore_put',AsyncMock()) as write:
            await push_stremio_progress('fixture',record)

        write.assert_awaited_once()
        read.assert_awaited_with('fixture',ids=['tt1'])
        candidate=write.await_args.args[1][0]
        self.assertEqual(candidate['state']['timeOffset'],30_000)
        self.assertEqual(candidate['state']['lastWatched'],'2026-09-20T12:00:00Z')

    async def test_stremio_upsert_rejects_a_content_type_mismatch(self):
        with patch('core.stremio.datastore_get',AsyncMock(return_value=[{
                '_id':'tt1','type':'movie','state':{'timeOffset':0,'duration':0},
            }])), patch('core.stremio.datastore_put',AsyncMock()) as write:
            with self.assertRaises(RemotePlaybackChanged):
                await push_stremio_progress('fixture',{
                    'content_id':'tt1','content_type':'series','position':1000,'duration':100_000,
                    'observed_at':'2026-09-20T12:00:00Z',
                })
        write.assert_not_awaited()

    async def test_local_watching_queues_one_second_resume_for_next_unwatched_episode(self):
        series=Media(id=20,tmdb_id=123,imdb_id='tt-show',media_type=MediaType.series,title='Fixture Show')
        entry=TrackedEntry(user_id=1,media_id=series.id,status='watching',progress=3)
        episodes=[Media(id=100+i,media_type=MediaType.episode,title=f'Episode {i+1}',
            show_id=77,season_number=1,episode_number=i+1,release_date='2020-01-01') for i in range(5)]
        connection=MediaServerConnection(id=5,user_id=1,type='stremio',name='Approved',
            url='https://example.test',token='fixture',push_playback=True)
        baseline=StreamBaseline(user_id=1,connection_id=connection.id,approved=True,
            snapshot={'mappings':{'tt-show':123},'progress':{},'resume':{}})
        db=_QueueDB([
            [],[entry],[77],episodes,[100,101,102],[],[],[connection],[],[],
        ],baseline)

        with patch('routers.sync._get_effective_tmdb_key',AsyncMock(return_value=None)), \
             patch('routers.sync._ensure_nuvio_imdb_ids',AsyncMock()), \
             patch('routers.sync._nuvio_imdb_id',return_value='tt1234567'):
            await queue_restorations(db,1,series)

        self.assertEqual(len(db.added),1)
        action=db.added[0]
        self.assertIsInstance(action,StreamAction)
        self.assertEqual(action.action,'upsert')
        self.assertEqual(action.connection_id,connection.id)
        self.assertEqual(action.payload['content_id'],'tt-show')
        self.assertEqual(action.payload['video_id'],'tt-show:1:4')
        self.assertEqual((action.payload['season'],action.payload['episode']),(1,4))
        self.assertEqual((action.payload['position'],action.payload['duration']),(1000,100_000))
        self.assertTrue(action.payload['last_watched'].endswith('Z'))

    async def test_nuvio_progress_upsert_uses_progress_key_and_confirms_readback(self):
        conn=MediaServerConnection(id=988,user_id=1,type='nuvio',name='Fixture',url='https://example.test',token='old',server_user_id='1')
        record={'content_id':'tt1','content_type':'series','video_id':'tt1:1:2','position':30,'duration':100,
            'season':1,'episode':2,'progress_key':'tt1_s1e2','observed_at':'2026-09-20T12:00:00Z'}
        token_db=AsyncMock();context=AsyncMock();context.__aenter__.return_value=token_db
        client=object();client_context=AsyncMock();client_context.__aenter__.return_value=client
        db=SimpleNamespace(refresh=AsyncMock())
        with patch('db.AsyncSessionLocal',return_value=context), \
             patch('core.nuvio.httpx.AsyncClient',return_value=client_context), \
             patch('core.nuvio.refresh_session',AsyncMock(return_value=SimpleNamespace(refresh_token='rotated',access_token='fixture'))), \
             patch('core.nuvio._pull_watch_progress',AsyncMock(side_effect=[[],[record]])) as read, \
             patch('core.nuvio._rpc',AsyncMock()) as write:
            await push_nuvio_progress(db,conn,record)

        self.assertEqual(write.await_args.args[-2],'sync_push_watch_progress')
        self.assertEqual(write.await_args.args[-1]['p_entries'],[{key:value for key,value in record.items()
            if key in ('content_id','content_type','video_id','season','episode','position','duration','last_watched','progress_key')}])
        self.assertEqual(read.await_count,2)

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

    async def test_reset_treats_a_missing_stremio_item_as_already_cleared(self):
        record={'content_id':'tt1','content_type':'movie','deleted_at':'2026-09-18T12:00:00+00:00'}
        with patch('core.stremio.datastore_get',AsyncMock(return_value=[])) as read, \
             patch('core.stremio.datastore_put',AsyncMock()) as write:
            await dismiss_stremio('fixture',record,reset=True)
        read.assert_awaited_once_with('fixture',ids=['tt1'],allow_missing=True)
        write.assert_not_awaited()

    async def test_restore_of_removed_item_becomes_temporary_visible_resume(self):
        record={'content_id':'tt1','content_type':'movie','position':30,'duration':100}
        remote={'_id':'tt1','type':'movie','removed':True,'temp':False,
            'state':{'timeOffset':0,'duration':100}}
        with patch('core.stremio.datastore_get',AsyncMock(return_value=[remote])), \
             patch('core.stremio.datastore_put',AsyncMock()) as write:
            await dismiss_stremio('fixture',record,restore=True)
        restored=write.await_args.args[1][0]
        self.assertFalse(restored['removed'])
        self.assertTrue(restored['temp'])
        self.assertEqual(restored['state']['timeOffset'],30)

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
