import os
import unittest
from io import BytesIO
import random
import tempfile
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-only-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from PIL import Image, ImageDraw
import httpx
from core.media_image_analysis import fingerprint, same_composition, select_variants
from core import media_image_analysis


class ImageDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_tvdb_thumbnails_are_analyzed_and_cached_by_full_image_identity(self):
        requests = []
        def respond(request):
            requests.append(str(request.url))
            return httpx.Response(200, headers={'content-type':'image/png'}, content=fixture(len(requests)))
        with tempfile.TemporaryDirectory() as directory, patch.object(media_image_analysis.settings, 'data_dir', Path(directory)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                first = 'https://artworks.thetvdb.com/banners/v4/movie/1/posters/shared.jpg'
                second = 'https://artworks.thetvdb.com/banners/v4/movie/2/posters/shared.jpg'
                thumbnail = 'https://artworks.thetvdb.com/banners/v4/movie/1/posters/api-small.jpg'
                a = await media_image_analysis.inspect_image(first, client, thumbnail)
                b = await media_image_analysis.inspect_image(second, client)
                self.assertEqual(a, fingerprint(fixture(1)))
                self.assertEqual(b, fingerprint(fixture(2)))
                self.assertEqual(await media_image_analysis.inspect_image(first, client, thumbnail), a)
                self.assertEqual(requests, [thumbnail, second.replace('.jpg', '_t.jpg')])
                self.assertEqual(len(list((Path(directory) / 'image_analysis').glob('*.json'))), 2)

    async def test_downloads_reject_untrusted_sources_redirects_and_oversized_content(self):
        requests = []
        def respond(request):
            requests.append(str(request.url))
            if request.url.path.endswith('redirect_t.jpg'):
                return httpx.Response(302, headers={'location':'https://untrusted.test/image.jpg'})
            return httpx.Response(200, headers={'content-type':'image/png'}, content=b'x'*2_000_001)
        with tempfile.TemporaryDirectory() as directory, patch.object(media_image_analysis.settings, 'data_dir', Path(directory)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                valid = 'https://artworks.thetvdb.com/banners/poster.jpg'
                invalid = ['https://untrusted.test/banners/poster.jpg',
                           'https://artworks.thetvdb.com.untrusted.test/banners/poster.jpg',
                           'http://artworks.thetvdb.com/banners/poster.jpg',
                           'https://artworks.thetvdb.com/banners/../poster.jpg']
                for url in invalid:
                    self.assertIsNone(await media_image_analysis.inspect_image(url, client))
                self.assertIsNone(await media_image_analysis.inspect_image(valid, client, invalid[0]))
                self.assertEqual(requests, [])
                self.assertIsNone(await media_image_analysis.inspect_image(valid, client))
                self.assertIsNone(await media_image_analysis.inspect_image(valid.replace('poster','redirect'), client))
                self.assertEqual(len(requests), 2)

    async def test_tmdb_primary_still_uses_a_resized_thumbnail(self):
        def respond(request):
            self.assertEqual(str(request.url), 'https://image.tmdb.org/t/p/w500/primary.jpg')
            return httpx.Response(200, headers={'content-type':'image/png'}, content=fixture())
        with tempfile.TemporaryDirectory() as directory, patch.object(media_image_analysis.settings, 'data_dir', Path(directory)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                self.assertEqual(await media_image_analysis.inspect_image('https://image.tmdb.org/t/p/original/primary.jpg', client), fingerprint(fixture()))


def fixture(seed=1, shift=0, title=False):
    image = Image.new('RGB', (320, 240), '#102030')
    draw = ImageDraw.Draw(image)
    generator = random.Random(seed)
    for _ in range(70):
        x, y = generator.randrange(60, 245), generator.randrange(50, 175)
        color = tuple(generator.randrange(40, 245) for _ in range(3))
        draw.rectangle((x, y+shift, x+12, y+shift+12), fill=color)
    if title:
        draw.text((10, 10), 'A GREAT STORY', fill='white')
    buffer = BytesIO(); image.save(buffer, format='PNG')
    return buffer.getvalue()


class ImageAnalysisTests(unittest.TestCase):
    def test_translations_and_title_overlays_share_a_composition_but_other_scenes_do_not(self):
        original = fingerprint(fixture())
        self.assertTrue(same_composition(original, fingerprint(fixture(shift=20))))
        self.assertTrue(same_composition(original, fingerprint(fixture(title=True))))
        self.assertFalse(same_composition(original, fingerprint(fixture(seed=7))))

    def test_prefers_centered_duplicates_without_removing_branding(self):
        base = fingerprint(fixture())
        centered = {**base, 'centering':.02}
        offset = {**base, 'centering':.25}
        other = fingerprint(fixture(seed=7))
        rows = [{'url':'offset','language':None,'analysis':offset},
                {'url':'centered','language':None,'analysis':centered},
                {'url':'english','language':'en','analysis':other},
                {'url':'branded','language':'en','analysis':{**base,'branded':True}}]
        self.assertCountEqual(select_variants(rows), ['english','branded'])
        self.assertEqual(select_variants([rows[-1]]), ['branded'])

    def test_english_duplicate_preferred_over_its_textless_copy(self):
        analysis = fingerprint(fixture())
        self.assertEqual(select_variants([{'url':'textless','language':None,'analysis':analysis},
            {'url':'english','language':'en','analysis':analysis}]), ['english'])

    def test_primary_poster_cannot_be_replaced_by_a_centered_or_higher_voted_duplicate(self):
        base = fingerprint(fixture())
        rows = [{'url':'primary','primary':True,'language':'en','votes':1,'analysis':{**base,'centering':.9}},
                {'url':'alternative','language':'en','votes':100,'analysis':{**base,'centering':0}}]
        self.assertEqual(select_variants(rows), ['primary'])

    def test_each_gallery_stops_at_ten_images(self):
        rows = [{'url':str(i),'language':'en','votes':i} for i in range(15)]
        self.assertEqual(len(select_variants(rows)), 10)

    def test_normalized_comparison_and_python_310_fallback_preserve_matches(self):
        base = fingerprint(fixture())
        adjusted = {**base, 'gray': [round(value*.5+60) for value in base['gray']]}
        cases = [(base, adjusted, True), (base, fingerprint(fixture(shift=20)), True),
                 (base, fingerprint(fixture(seed=7)), False),
                 (base, {**base, 'color':[255,255,255]}, False),
                 ({**base, 'gray':[40]*768}, base, False), (base, None, False)]
        for first, second, expected in cases:
            self.assertEqual(same_composition(first, second), expected)
        with patch.object(media_image_analysis, '_sumprod', media_image_analysis._fallback_sumprod):
            for first, second, expected in cases:
                self.assertEqual(same_composition(first, second), expected)

    def test_comparison_features_do_not_mutate_cached_fingerprints_or_rows(self):
        base = fingerprint(fixture())
        rows = [{'url':'primary','primary':True,'analysis':base},
                {'url':'copy','analysis':fingerprint(fixture(title=True))},
                {'url':'other','analysis':fingerprint(fixture(seed=7))}]
        before = deepcopy(rows)
        self.assertEqual(select_variants(rows), ['primary','other'])
        self.assertEqual(rows, before)
