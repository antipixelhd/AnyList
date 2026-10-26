import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault('SECRET_KEY','release-precision-unit-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')

from core.catalogue import merge_fields
from core.catalogue_normalize import normalize_igdb
from core.game_browse_sources import card
from core.game_release_dates import game_release, in_range, release


def stamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def game(form='YYYYMM', value='2027-03-01'):
    return {'id':1,'name':'Partial release','game_type':{'id':0},'first_release_date':stamp(value),
        'release_dates':[{'id':10,'date':stamp(value),'date_format':{'format':form},'platform':{'id':6,'name':'PC'}}]}


class GameReleasePrecisionTests(unittest.TestCase):
    def test_live_placeholder_shapes_remain_partial_in_work_release_and_card(self):
        for form,value,expected,precision in (('YYYYMM','2027-03-01','2027-03','month'),
                ('YYYY','2027-12-31','2027','year'), ('YYYYMMDD','2027-03-11','2027-03-11','day')):
            with self.subTest(form=form):
                raw=game(form,value)
                doc=normalize_igdb(raw)
                self.assertEqual(doc['work']['attributes']['release_date'],expected)
                self.assertEqual(doc['releases'][0]['release_date'],expected)
                self.assertEqual(card(raw)['release_date'],expected)
                self.assertEqual(card(raw)['release_date_precision'],precision)

    def test_quarter_tbd_invalid_and_unknown_formats_never_manufacture_a_day(self):
        point=game_release(game('YYYYQ2','2027-06-30'))
        self.assertEqual((point['release_date'],point['release_quarter']),('2027',2))
        self.assertFalse(in_range(point,'2027-01-01','2027-03-31'))
        self.assertTrue(in_range(point,'2027-04-15','2027-04-16'))
        for form in ('TBD','unsupported'):
            self.assertIsNone(game_release(game(form))['release_date'])
        invalid=game('YYYYMM');invalid['release_dates'][0]['m']=13
        self.assertIsNone(game_release(invalid)['release_date'])
        raw=game();raw.pop('release_dates')
        self.assertEqual(game_release(raw)['release_date'],'2027')
        self.assertIsNone(release({'date':stamp('2027-03-01'),'date_format':True})['release_date'])

    def test_ranges_overlap_known_periods_but_released_only_waits_for_period_end(self):
        month=game_release(game())
        self.assertTrue(in_range(month,'2027-03-15','2027-03-20'))
        self.assertFalse(in_range(month,'2027-04-01','2027-05-01'))
        self.assertFalse(in_range(month,released_before='2027-03-15'))
        self.assertTrue(in_range(month,released_before='2027-03-31'))
        year=game_release(game('YYYY','2027-12-31'))
        self.assertTrue(in_range(year,'2027-02-01','2027-02-20'))
        self.assertFalse(in_range(year,released_before='2027-11-01'))
        self.assertTrue(in_range(year,released_before='2027-12-31'))

    def test_equal_timestamp_precision_conflicts_choose_conservative_date_and_missing_first_uses_rows(self):
        raw=game();raw['release_dates'].append({**raw['release_dates'][0],'id':11,'date_format':{'format':'YYYYMMDD'}})
        self.assertEqual(game_release(raw)['release_date'],'2027-03')
        raw.pop('first_release_date')
        self.assertEqual(game_release(raw)['release_date'],'2027-03')

    def test_richer_projection_and_protected_dates_survive_shallow_enrichment(self):
        row=SimpleNamespace(kind='game',name='Partial release',description=None,image_url=None,
            attributes={},field_sources={},protected_fields=[])
        def apply(raw):
            merge_fields(row,normalize_igdb(raw)['work'],'igdb')
        apply(game('YYYYMMDD','2027-03-11'))
        shallow=game();shallow.pop('release_dates')
        apply(shallow)
        self.assertEqual(row.attributes['release_date'],'2027-03-11')
        apply(game())
        self.assertEqual(row.attributes['release_date'],'2027-03')
        row.protected_fields=['attributes.release_date']
        apply(game('YYYY','2027-12-31'))
        self.assertEqual((row.attributes['release_date'],row.attributes['release_date_precision']),('2027-03','month'))
        row.protected_fields=[]
        apply(game('TBD'))
        self.assertIsNone(row.attributes['release_date'])
        self.assertEqual(row.attributes['release_date_precision'],'unknown')
        row.attributes={**row.attributes,'release_date':'2020-02-01','release_date_precision':'day'}
        row.field_sources['attributes.release_date']='legacy'
        apply(game())
        self.assertEqual(row.attributes['release_date'],'2020-02-01')
        row.field_sources['attributes.release_date']='rawg'
        apply(game())
        self.assertEqual(row.attributes['release_date'],'2027-03')
