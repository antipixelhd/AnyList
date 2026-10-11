import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'test-only-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.media_images import provider_images, unique_images
from core import tmdb


class ImageProjectionTests(unittest.TestCase):
    def test_english_precedes_textless_and_other_languages_are_rejected(self):
        rows = provider_images({'posters': [{'file_path':'/neutral.jpg','iso_639_1':None},
            {'file_path':'/german.jpg','iso_639_1':'de'}, {'file_path':'/english.jpg','iso_639_1':'en'}]})
        self.assertEqual(rows['posters'], ['https://image.tmdb.org/t/p/original/english.jpg',
            'https://image.tmdb.org/t/p/original/neutral.jpg'])
    def test_orientation_and_safe_paths_keep_the_galleries_separate(self):
        rows = provider_images({'posters': [{'file_path':'/poster.jpg', 'aspect_ratio':.667},
            {'file_path':'/landscape.jpg', 'aspect_ratio':1.778}, {'file_path':'javascript:alert(1)'}],
            'backdrops':[{'file_path':'/art.jpg', 'width':1920, 'height':1080},
                         {'file_path':'/portrait.jpg', 'aspect_ratio':.667}]})
        self.assertEqual(rows['posters'], ['https://image.tmdb.org/t/p/original/poster.jpg'])
        self.assertEqual(rows['artwork'], ['https://image.tmdb.org/t/p/original/art.jpg'])

    def test_resized_copies_are_deduplicated_and_unsafe_urls_rejected(self):
        self.assertEqual(unique_images(['https://image.tmdb.org/t/p/w500/a.jpg',
            'https://image.tmdb.org/t/p/original/a.jpg', 'javascript:alert(1)']),
            ['https://image.tmdb.org/t/p/w500/a.jpg'])


class ImageProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_movie_and_series_endpoints_filter_english_and_textless(self):
        with patch.object(tmdb, '_get', AsyncMock(return_value={})) as fetch:
            await tmdb.get_title_images(42, 'movie', 'fixture')
            self.assertEqual(fetch.call_args.args[0], tmdb.TMDB_BASE+'/movie/42/images')
            self.assertEqual(fetch.call_args.kwargs['params'], {'include_image_language':'en,null'})
            await tmdb.get_title_images(43, 'series', 'fixture')
            self.assertEqual(fetch.call_args.args[0], tmdb.TMDB_BASE+'/tv/43/images')
