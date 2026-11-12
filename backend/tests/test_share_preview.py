import asyncio
import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'share-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from fastapi import HTTPException
from PIL import Image
from core import share_preview as preview
from core.share_cards import render_card


class ShareProjectionTests(unittest.TestCase):
    def test_target_is_an_identity_not_a_proxy_url(self):
        self.assertEqual(preview.target('/user/alex/stats'), ('profile', 'alex'))
        self.assertEqual(preview.target('/user/alex/stats/games'), ('game_statistics', 'alex'))
        self.assertEqual(preview.target('/actors/tmdb%3A42'), ('actor', 'tmdb:42'))
        self.assertEqual(preview.target('/studio/42'), ('studio', 'tmdb:42'))
        self.assertEqual(preview.target('/actors/catalogue:42?media_type=game'), ('game_actor', 'catalogue:42'))
        for path in ['/title/2147483648', '/title/1?token=x', '//evil.test', '/user/a%2fb', '/actors/0',
                     '/title/1?media_type=game', '/actors/42?media_type=game&list_scope=in']:
            with self.assertRaises(HTTPException):
                preview.target(path)

    def test_age_uses_birthday_and_death_date(self):
        self.assertEqual(preview.age('1980-12-31', '2020-01-01'), 39)
        self.assertIsNone(preview.age('nonsense'))
        self.assertIsNone(preview.age('2999-01-01'))

    def test_media_has_imdb_not_personal_or_tmdb_score(self):
        card = preview.media_card({'type':'movie', 'title':'Film', 'year':'2025', 'runtime':125,
            'imdb_score':8.1, 'tmdb_score':9, 'entry':{'score':2}, 'overview':'SECRET', 'genres':['Drama']})
        self.assertEqual(card['stats'], [{'value':'8.1', 'label':'IMDb'}])
        self.assertIn('2h 5m', card['eyebrow'])
        self.assertNotIn('SECRET', str(card))
        self.assertEqual(preview.media_card({'type':'movie','title':'Film'})['stats'], [])

    def test_profile_uses_combined_watch_time_without_theme(self):
        user = SimpleNamespace(username='alex', display_name='Alex', profile=SimpleNamespace(background_url='https://example.com/banner.jpg', profile_color='red'))
        card = preview.profile_card(user, {'formats':[{'key':'movie','titles':123}, {'key':'series','titles':45}], 'totals':{'watch_days':25.75}})
        self.assertEqual(card['stats'], [{'value':'123','label':'Movies watched'}, {'value':'45','label':'Series watched'}, {'value':'25.8','label':'Days watched'}])
        self.assertNotIn('profile_color', card)
        self.assertEqual(preview.profile_card(user, None)['stats'], [])

    def test_media_creators_exclude_specialist_director_jobs(self):
        people=[{'name':role,'kind':'staff','roles':[role]} for role in
                ('Director of Photography','Casting Director','Art Director','Director','Creator','Author','Developer','Showrunner','Head Writer')]
        people.append({'name':'Cast member','kind':'actor','roles':['Director']})
        card=preview.media_card({'type':'movie','title':'Fixture','people':people})
        self.assertEqual(card['subtitle'],'Director, Creator, Author, Developer, Showrunner, Head Writer')

    def test_long_titles_and_missing_or_bad_images_still_render_small_jpeg(self):
        for kind in ['media', 'profile', 'actor', 'studio']:
            result = render_card({'kind':kind, 'title':'Very long title <>& ' * 30, 'eyebrow':'Metadata ' * 30,
                'subtitle':'Director ' * 30, 'genres':['Action', 'Science Fiction', 'Drama'],
                'stats':[{'value':'8.1','label':'IMDb'}]}, b'not an image', b'bad')
            self.assertLess(len(result), 300_000)
            with Image.open(BytesIO(result)) as image:
                self.assertEqual(image.size, (1200,630))
                self.assertEqual(image.format, 'JPEG')


class ShareAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        preview._images.clear()

    async def test_game_statistics_preview_is_denied_before_database_or_cache_reads(self):
        with self.assertRaises(HTTPException) as denied:
            await preview.card_data(None,'/user/alex/stats/games')
        self.assertEqual(denied.exception.status_code,403)

    async def test_private_and_local_images_are_never_requested(self):
        with patch('core.share_preview.httpx.AsyncClient') as client:
            for url in ['http://example.com/x.jpg', 'https://user:pass@example.com/x', 'https://example.com:8443/x']:
                self.assertIsNone(await preview.fetch_image(url))
            loop = asyncio.get_running_loop()
            with patch.object(loop, 'getaddrinfo', AsyncMock(return_value=[(0,0,0,'',('127.0.0.1',443))])):
                self.assertIsNone(await preview.fetch_image('https://example.com/x'))
            client.assert_not_called()

    async def test_concurrent_images_share_one_render_and_cache(self):
        card = {'kind':'media','title':'Film'}
        with patch.object(preview, 'fetch_image', AsyncMock(return_value=None)), patch.object(preview, 'render_card', return_value=b'jpeg') as render:
            self.assertEqual(await asyncio.gather(preview.image_bytes(card), preview.image_bytes(card)), [b'jpeg',b'jpeg'])
            self.assertEqual(await preview.image_bytes(card), b'jpeg')
            self.assertEqual(render.call_count, 1)

    async def test_cache_is_bounded(self):
        with patch.object(preview, 'MAX_IMAGES', 2), patch.object(preview, 'fetch_image', AsyncMock(return_value=None)), patch.object(preview, 'render_card', return_value=b'jpeg'):
            for title in ['A','B','C']:
                await preview.image_bytes({'kind':'media','title':title})
            self.assertEqual(len(preview._images), 2)
