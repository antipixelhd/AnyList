import os
import unittest
from io import BytesIO
import random

os.environ.setdefault('SECRET_KEY', 'test-only-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from PIL import Image, ImageDraw
from core.media_image_analysis import fingerprint, same_composition, select_variants


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

    def test_selects_english_text_before_textless_and_prefers_clean_centered_duplicates(self):
        base = fingerprint(fixture())
        centered = {**base, 'centering':.02}
        offset = {**base, 'centering':.25}
        other = fingerprint(fixture(seed=7))
        rows = [{'url':'offset','language':None,'analysis':offset},
                {'url':'centered','language':None,'analysis':centered},
                {'url':'english','language':'en','analysis':other},
                {'url':'branded','language':'en','analysis':{**base,'branded':True}}]
        self.assertEqual(select_variants(rows), ['english','centered'])
        self.assertEqual(select_variants([rows[-1]]), ['branded'])

    def test_english_duplicate_preferred_over_its_textless_copy(self):
        analysis = fingerprint(fixture())
        self.assertEqual(select_variants([{'url':'textless','language':None,'analysis':analysis},
            {'url':'english','language':'en','analysis':analysis}]), ['english'])

    def test_brand_ocr_tokens_do_not_treat_the_media_title_as_a_provider(self):
        for name in ('Netflix','Apple TV+','Disney+','Prime Video','An Apple Original'):
            self.assertTrue(fingerprint(fixture(), text=name)['branded'])
        self.assertFalse(fingerprint(fixture(), text='Severance')['branded'])
