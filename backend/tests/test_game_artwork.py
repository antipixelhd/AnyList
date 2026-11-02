"""SteamGridDB filters, stable provider ranking and IGDB presentation policy."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

os.environ.setdefault('SECRET_KEY','test-secret')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core import game_artwork as artwork
from core.catalogue_normalize import game_artwork
from core.media_details import primary_details


def asset(id=1,**values):
    return {'id':id,'url':f'https://cdn2.steamgriddb.com/hero/{id}.png','thumb':None,
        'width':1920,'height':620,'style':'alternate','mime':'image/png','language':'en',
        'humor':False,'nsfw':False,'epilepsy':False,**values}


class ArtworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_static_filtered_request_and_provider_vote_order(self):
        http=SimpleNamespace(request=AsyncMock(return_value={'success':True,'data':[asset(3),asset(1)]}))
        with patch.object(artwork.settings,'steamgriddb_api_key','fake'):
            rows=await artwork.retrieve(http,'1578650','heroes')
        self.assertEqual([row['id'] for row in rows],[3,1])
        params=http.request.call_args.kwargs['params']
        self.assertEqual(params['types'],'static')
        self.assertEqual(params['languages'],'en')
        for flag in ('humor','nsfw','epilepsy'):self.assertEqual(params[flag],'false')

    def test_unwanted_assets_rejected_even_if_provider_ignores_filters(self):
        rows=[asset(),*[asset(index+2,**change) for index,change in enumerate([
            {'humor':True},{'nsfw':True},{'epilepsy':True},{'animated':True},
            {'mime':'image/gif'},{'style':'blurred'},{'language':'de'},
            {'url':'https://untrusted.test/image.jpg'},{'width':600,'height':900}])]]
        self.assertEqual([row['id'] for row in artwork.candidates(rows,'heroes')],[1])

    def test_igdb_order_icons_and_unique_primary_images(self):
        rows=[{'url':f'https://images.igdb.com/igdb/image/upload/t_original/a{i}.jpg',
            'image_type':i,'width':1920,'height':620} for i in (3,4,2,1,13,5,8,12)]
        normalized=game_artwork({'artworks':rows,'screenshots':[{'url':'https://cdn.test/screenshot.jpg','width':3840,'height':2160}]})
        self.assertEqual([url.split('/')[-1] for url in normalized['scene_artwork']],['a1.jpg','a4.jpg','a2.jpg','a3.jpg','screenshot.jpg'])
        work=SimpleNamespace(id=1,kind='game',name='Game',description='',image_url=None,
            protected_fields=[],field_sources={},attributes={**normalized,'steamgriddb_hero':{'image':asset()['url']}})
        data=primary_details(work)
        self.assertEqual(data['backdrop'],asset()['url'])
        self.assertTrue(data['artwork_preview']['image'].endswith('a1.jpg'))
        self.assertNotIn(data['artwork_preview']['image'],data['artwork'])
        work.attributes['backdrop_path']=rows[0]['url']
        self.assertEqual(primary_details(work)['backdrop'],rows[0]['url'])
