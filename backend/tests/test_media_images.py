import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'test-only-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.media_images import provider_images, unique_images, screen_images
from types import SimpleNamespace
from core import tmdb


class ImageProjectionTests(unittest.TestCase):
    def test_tvdb_taxonomy_keeps_title_posters_and_explicit_textless_backgrounds(self):
        from core.screen_artwork import artwork_candidates
        types=[{'id':10,'name':'Poster','recordType':'movie','width':680,'height':1000},
               {'id':11,'name':'Background','recordType':'movie','width':1920,'height':1080},
               {'id':12,'name':'Poster','recordType':'season'}]
        def art(id,**values):
            return dict(type=id,image=f'https://artworks.thetvdb.com/{id}-{len(values)}.jpg',**values)
        raw={'artworks':[art(10,language='eng'),art(10,language='deu'),art(12,language='eng'),
                         art(11,includesText=False),art(11,includesText=True),art(11)]}
        result=provider_images(artwork_candidates(raw,types,'movie'),'tvdb')
        self.assertEqual(len(result['posters']),1)
        self.assertEqual(len(result['artwork']),1)
        self.assertTrue(all(url.startswith('https://artworks.thetvdb.com/') for rows in result.values() for url in rows))

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
    async def test_movie_artwork_resolves_only_an_unambiguous_imdb_bridge(self):
        from core.screen_artwork import title_artwork
        media=SimpleNamespace(media_type=SimpleNamespace(value='movie'),tmdb_id=7,imdb_id='tt77',tmdb_data={})
        async def fetch(path,key):
            if path=='/search/remoteid/tt77':return {'data':[{'movie':{'id':8}}]}
            if path=='/movies/8/extended':return {'data':{'id':8,'artworks':[]}}
            if path=='/artwork/types':return {'data':[]}
            raise AssertionError(path)
        with patch('core.screen_artwork.screen_sources',AsyncMock(return_value=[('tmdb',7),('tvdb',None)])), \
             patch('core.tmdb.get_external_ids',AsyncMock(return_value={'imdb_id':'tt77'})), \
             patch('core.tvdb._get',AsyncMock(side_effect=fetch)) as provider:
            self.assertEqual(await title_artwork(None,media,'tvdb-key','tmdb-key'),{'posters':[],'backdrops':[]})
        self.assertIn('/movies/8/extended',[call.args[0] for call in provider.await_args_list])

    async def test_ambiguous_bridge_does_not_guess_a_title(self):
        from core.screen_artwork import title_artwork
        media=SimpleNamespace(media_type=SimpleNamespace(value='movie'),tmdb_id=None,imdb_id='tt77',tmdb_data={})
        with patch('core.screen_artwork.screen_sources',AsyncMock(return_value=[('tvdb',None)])), \
             patch('core.tvdb._get',AsyncMock(return_value={'data':[{'movie':{'id':8}},{'movie':{'id':9}}]})) as provider:
            with self.assertRaisesRegex(ValueError,'verified TVDB'):
                await title_artwork(None,media,'fixture')
        provider.assert_awaited_once()

    async def test_movie_and_series_endpoints_filter_english_and_textless(self):
        with patch.object(tmdb, '_get', AsyncMock(return_value={})) as fetch:
            await tmdb.get_title_images(42, 'movie', 'fixture')
            self.assertEqual(fetch.call_args.args[0], tmdb.TMDB_BASE+'/movie/42/images')
            self.assertEqual(fetch.call_args.kwargs['params'], {'include_image_language':'en,null'})
            await tmdb.get_title_images(43, 'series', 'fixture')
            self.assertEqual(fetch.call_args.args[0], tmdb.TMDB_BASE+'/tv/43/images')
