import unittest
from types import SimpleNamespace
from core.catalogue import merge_fields
from scripts.backfill_person_portraits import cached_portraits

TMDB='https://image.tmdb.org/t/p/original/person.jpg'
TVDB='https://artworks.thetvdb.com/banners/person/photo.jpg'


def person(image=TVDB,source='tvdb',protected=()):
    return SimpleNamespace(kind='person',name='Actor',description=None,image_url=image,
        field_sources={'image_url':source},protected_fields=list(protected),attributes={})


class PersonPortraitTests(unittest.TestCase):
    def test_tmdb_portrait_remains_stable_when_tvdb_enriches_later(self):
        row=person()
        merge_fields(row,{'image_url':TMDB},'tmdb')
        self.assertEqual(row.image_url,TMDB)
        merge_fields(row,{'image_url':TVDB},'tvdb')
        self.assertEqual(row.image_url,TMDB)
        self.assertEqual(row.field_sources['image_url'],'tmdb')

    def test_missing_tmdb_preserves_tvdb_and_custom_images_are_not_replaced(self):
        for row in [person(),person(protected=['image_url']),person(image='https://custom.test/selected.jpg',source='manual')]:
            expected=row.image_url
            merge_fields(row,{'image_url':None},'tmdb')
            self.assertEqual(row.image_url,expected)
            if row.protected_fields or row.field_sources['image_url']=='manual':
                merge_fields(row,{'image_url':TMDB},'tmdb')
                self.assertEqual(row.image_url,expected)

    def test_backfill_uses_provider_ids_and_valid_cached_paths(self):
        snapshot=SimpleNamespace(kind='movie',cast=[{'id':3,'profile_path':'/person.jpg'},
            {'id':4,'profile_path':None},{'id':5,'profile_path':'//other.test/image'}],aggregate_cast=[],crew=[])
        self.assertEqual(cached_portraits(snapshot),{'3':TMDB})
