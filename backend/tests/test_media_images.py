import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'test-only-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.media_images import provider_images, unique_images, screen_images
from types import SimpleNamespace
from core import tmdb


class ImageProjectionTests(unittest.TestCase):
    def test_complete_games_gallery_is_not_a_display_limit(self):
        rows=[f'https://images.igdb.com/igdb/image/upload/t_thumb/sc{i}.jpg' for i in range(23)]
        self.assertEqual(len(unique_images(rows)),10)
        self.assertEqual(unique_images(rows+rows,limit=None),rows)
        self.assertEqual(unique_images(rows+[rows[0].replace('t_thumb','t_1080p')],limit=None),rows)
    def test_movie_has_a_provider_fallback_when_metahub_is_unavailable(self):
        from core.media_images import screen_backdrop
        media=SimpleNamespace(media_type=SimpleNamespace(value='movie'),imdb_id='tt0133093',
            tmdb_data={'_anylist_images':{'artwork':['https://artworks.thetvdb.com/old-background.jpg']}},
            backdrop_path='/stored.jpg')
        self.assertEqual(screen_backdrop(media)['backdrop_fallback'],'https://artworks.thetvdb.com/old-background.jpg')
        self.assertEqual(screen_backdrop(media)['backdrop_fallbacks'],['https://artworks.thetvdb.com/old-background.jpg','https://image.tmdb.org/t/p/original/stored.jpg'])
        media.tmdb_data={}
        self.assertEqual(screen_backdrop(media)['backdrop_fallback'],'https://image.tmdb.org/t/p/original/stored.jpg')

    def test_series_hero_uses_highest_rated_explicitly_textless_landscape(self):
        from core.media_images import best_tmdb_backdrop
        raw={'backdrops':[{'file_path':'/branded.jpg','iso_639_1':'en','vote_average':10},
                          {'file_path':'/unknown.jpg','vote_average':10},
                          {'file_path':'/portrait.jpg','iso_639_1':None,'aspect_ratio':.6,'vote_average':10},
                          {'file_path':'/popular.jpg','iso_639_1':None,'vote_average':8,'vote_count':1000},
                          {'file_path':'/rated.jpg','iso_639_1':None,'vote_average':9,'vote_count':5}]}
        self.assertEqual(best_tmdb_backdrop(raw),'https://image.tmdb.org/t/p/original/rated.jpg')
        self.assertIsNone(best_tmdb_backdrop({'backdrops':raw['backdrops'][:3]}))

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
    async def test_missing_bridge_is_distinct_from_provider_failure(self):
        from core.screen_artwork import title_artwork, ArtworkIdentityUnavailable
        media=SimpleNamespace(media_type=SimpleNamespace(value='series'),tmdb_id=323132,imdb_id=None,
                              tmdb_data={'external_ids':{'tvdb_id':None,'imdb_id':None}})
        with patch('core.screen_artwork.screen_sources',AsyncMock(return_value=[('tmdb',323132),('tvdb',None)])), \
             patch('core.tvdb._get',AsyncMock()) as provider:
            with self.assertRaises(ArtworkIdentityUnavailable):
                await title_artwork(None,media,'fixture','fixture')
        provider.assert_not_awaited()

    async def test_unmatched_gallery_keeps_poster_without_retrying_or_downloading_images(self):
        from core.media_images import _fill_screen_images
        from core.screen_artwork import ArtworkIdentityUnavailable
        media=SimpleNamespace(id=7,media_type=SimpleNamespace(value='series'),tmdb_id=323132,imdb_id=None,
                              poster_path='/primary.jpg',backdrop_path=None,tmdb_data={
                                  '_anylist_images':{'version':0,'posters':['https://example.test/unapproved.jpg']}})
        db=SimpleNamespace(refresh=AsyncMock(),commit=AsyncMock())
        async def execute(statement):
            for value in statement.compile().params.values():
                if isinstance(value,dict) and '_anylist_images' in value:
                    media.tmdb_data.update(value)
        db.execute=AsyncMock(side_effect=execute)
        with patch('core.screen_artwork.title_artwork',AsyncMock(side_effect=ArtworkIdentityUnavailable())) as provider, \
             patch('core.tmdb.get_show_light',AsyncMock()) as primary, \
             patch('core.media_image_analysis.inspect_image',AsyncMock()) as inspect:
            await _fill_screen_images(db,media,'fixture','fixture')
            await _fill_screen_images(db,media,'fixture','fixture')
        provider.assert_awaited_once();primary.assert_not_awaited();inspect.assert_not_awaited()
        projected=screen_images(media,{})
        self.assertEqual(projected['image_status'],{'state':'unavailable','error':None,'retry_after':0})
        self.assertEqual(projected['posters'],['https://image.tmdb.org/t/p/original/primary.jpg'])
        self.assertNotIn('last_error',media.tmdb_data['_anylist_images'])

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
