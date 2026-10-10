import unittest

from core.studio_artwork import studio_logo


class StudioArtworkTests(unittest.TestCase):
    def test_landscape_variant_precedes_main_square_logo(self):
        data = {'logos':[{'file_path':'/main.png','width':1000,'height':1000},
                         {'file_path':'/wide.png','width':600,'height':180}]}
        self.assertEqual(studio_logo(data,'/main.png'),'https://image.tmdb.org/t/p/original/wide.png')

    def test_primary_landscape_logo_and_scalable_svg_are_preferred(self):
        data = {'logos':[{'file_path':'/main.svg','width':200,'height':50},
                         {'file_path':'/other.png','width':2000,'height':500}]}
        self.assertEqual(studio_logo(data,'/main.svg'),'https://image.tmdb.org/t/p/original/main.svg')

    def test_missing_or_untrusted_logos_leave_media_fallback_available(self):
        self.assertIsNone(studio_logo({'logos':[{'file_path':'https://wrong.test/a.png','width':600,'height':200},
                                              {'file_path':'/small.png','width':100,'height':40},
                                              {'file_path':'/zero.svg','width':100,'height':0}]}))
        self.assertIsNone(studio_logo({'logos':[]}))
