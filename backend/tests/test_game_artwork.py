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
    def test_compact_tracking_background_matches_detail_policy_without_gallery_projection(self):
        from core.game_tracking import present
        from models.catalogue import CatalogueEntity
        work=CatalogueEntity(id=7,kind='game',name='Game',protected_fields=[],attributes={
            'game_class':'game','steamgriddb_hero':{'image':asset()['url']},
            'scene_artwork':['https://images.igdb.com/igdb/image/upload/t_original/scene.jpg'],
            'artwork_policy_version':1})
        state=present(work)
        detail=primary_details(work)
        self.assertEqual(state.backdrop,detail['backdrop'])
        self.assertEqual(state.backdrop_mobile,detail['backdrop_mobile'])
        self.assertNotIn('artwork',state.model_dump())
        pinned=present(work,pinned_banner=asset(2)['url'])
        self.assertEqual(pinned.backdrop,asset(2)['url'])
        self.assertEqual(pinned.backdrop_mobile,asset(2)['url'])

    def test_cycle_threshold_and_ranked_fallback_for_redacted_votes(self):
        rows=artwork.candidates([asset(index) for index in range(10)],'heroes')
        self.assertEqual(artwork.cycling(rows),[row['url'] for row in rows[:6]])
        rows[2]['upvotes']=2
        self.assertEqual(artwork.cycling(rows),[rows[2]['url']])

    async def test_animated_heroes_use_an_independent_source_filter(self):
        http=SimpleNamespace(request=AsyncMock(return_value={'success':True,'data':[asset(animated=True)]}))
        with patch.object(artwork.settings,'steamgriddb_api_key','fake'):
            rows=await artwork.retrieve(http,'1578650','heroes_animated')
        self.assertEqual(len(rows),1)
        self.assertEqual(http.request.call_args.kwargs['params']['types'],'animated')
        self.assertIn('/heroes/steam/',http.request.call_args.args[2])
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
        normalized=game_artwork({'artworks':[*rows,{'url':'https://cdn.test/icon.jpg','image_type':1,'width':48,'height':48}],
            'screenshots':[{'url':'https://cdn.test/screenshot.jpg','width':3840,'height':2160}]})
        self.assertNotIn('https://cdn.test/icon.jpg',normalized['artwork'])
        self.assertEqual([url.split('/')[-1] for url in normalized['scene_artwork']],['a2.jpg','a3.jpg','a1.jpg','a4.jpg','screenshot.jpg'])
        work=SimpleNamespace(id=1,kind='game',name='Game',description='',image_url=None,
            protected_fields=[],field_sources={},attributes={**normalized,'steamgriddb_hero':{'image':asset()['url']}})
        data=primary_details(work)
        self.assertEqual(data['backdrop'],asset()['url'])
        self.assertTrue(data['artwork_preview']['image'].endswith('a2.jpg'))
        self.assertEqual(data['backdrop_mobile'],normalized['scene_artwork'][0])
        self.assertNotIn(data['artwork_preview']['image'],data['artwork'])
        work.attributes['backdrop_path']=rows[0]['url']
        self.assertEqual(primary_details(work)['backdrop'],rows[0]['url'])
        self.assertEqual(primary_details(work)['backdrop_mobile'],rows[0]['url'])

    def test_mobile_uses_screenshots_before_steam_when_artwork_is_ineligible(self):
        screenshot='https://images.igdb.com/igdb/image/upload/t_original/scene.jpg'
        normalized=game_artwork({'artworks':[{'url':'https://cdn.test/portrait.jpg','image_type':2,'width':600,'height':900}],
            'screenshots':[{'url':screenshot,'width':1920,'height':1080}]})
        work=SimpleNamespace(id=1,kind='game',name='Game',description='',image_url=None,
            protected_fields=[],field_sources={},attributes={**normalized,'steamgriddb_hero':{'image':asset()['url']}})
        self.assertEqual(primary_details(work)['backdrop_mobile'],screenshot)
        work.attributes['scene_artwork']=[]
        self.assertEqual(primary_details(work)['backdrop_mobile'],asset()['url'])
