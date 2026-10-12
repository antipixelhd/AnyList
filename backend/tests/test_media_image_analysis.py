import os
import unittest
from io import BytesIO
import random
from copy import deepcopy
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-only-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from PIL import Image, ImageDraw
from core.media_image_analysis import fingerprint, same_composition, select_variants
from core import media_image_analysis


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
