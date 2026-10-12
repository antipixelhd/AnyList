import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'test-only-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.media_images import provider_images, unique_images, screen_images
from types import SimpleNamespace
from core import tmdb


class ImageProjectionTests(unittest.TestCase):
    def test_english_text_posters_exclude_textless_and_other_languages(self):
        rows = provider_images({'posters': [{'file_path':'/neutral.jpg','iso_639_1':None},
            {'file_path':'/german.jpg','iso_639_1':'de'}, {'file_path':'/english.jpg','iso_639_1':'en'}]})
        self.assertEqual(rows['posters'], ['https://image.tmdb.org/t/p/original/english.jpg'])

    def test_textless_posters_are_only_a_fallback_and_artwork_is_always_explicitly_textless(self):
        rows = provider_images({'posters':[{'file_path':'/neutral.jpg','iso_639_1':None}],
            'backdrops':[{'file_path':'/english.jpg','iso_639_1':'en'},
                         {'file_path':'/untagged.jpg'}, {'file_path':'/neutral.jpg','iso_639_1':None}]})
        self.assertEqual(rows['posters'], ['https://image.tmdb.org/t/p/original/neutral.jpg'])
        self.assertEqual(rows['artwork'], rows['posters'])
    def test_orientation_and_safe_paths_keep_the_galleries_separate(self):
        rows = provider_images({'posters': [{'file_path':'/poster.jpg', 'aspect_ratio':.667},
            {'file_path':'/landscape.jpg', 'aspect_ratio':1.778}, {'file_path':'javascript:alert(1)'}],
            'backdrops':[{'file_path':'/art.jpg', 'iso_639_1':None, 'width':1920, 'height':1080},
                         {'file_path':'/portrait.jpg', 'aspect_ratio':.667}]})
        self.assertEqual(rows['posters'], ['https://image.tmdb.org/t/p/original/poster.jpg'])
        self.assertEqual(rows['artwork'], ['https://image.tmdb.org/t/p/original/art.jpg'])

    def test_resized_copies_are_deduplicated_and_unsafe_urls_rejected(self):
        self.assertEqual(unique_images(['https://image.tmdb.org/t/p/w500/a.jpg',
            'https://image.tmdb.org/t/p/original/a.jpg', 'javascript:alert(1)']),
            ['https://image.tmdb.org/t/p/w500/a.jpg'])

    def test_old_policy_never_overrides_primary_poster_or_leaks_text_artwork(self):
        media = SimpleNamespace(poster_path='/primary.jpg', tmdb_data={'_anylist_images':{
            'version':3,'posters':['https://example.test/curated.jpg'],'artwork':['https://example.test/text.jpg']}},
            backdrop_path='/unverified.jpg')
        result = screen_images(media,{})
        self.assertEqual(result['poster'], 'https://image.tmdb.org/t/p/original/primary.jpg')
        self.assertEqual(result['posters'], [result['poster']])
        self.assertIsNone(result['backdrop'])
        self.assertEqual(result['artwork'], [])


class ImageProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_movie_and_series_endpoints_filter_english_and_textless(self):
        with patch.object(tmdb, '_get', AsyncMock(return_value={})) as fetch:
            await tmdb.get_title_images(42, 'movie', 'fixture')
            self.assertEqual(fetch.call_args.args[0], tmdb.TMDB_BASE+'/movie/42/images')
            self.assertEqual(fetch.call_args.kwargs['params'], {'include_image_language':'en,null'})
            await tmdb.get_title_images(43, 'series', 'fixture')
            self.assertEqual(fetch.call_args.args[0], tmdb.TMDB_BASE+'/tv/43/images')
