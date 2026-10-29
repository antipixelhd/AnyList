"""Unknown games remain retryable without endless provider work or weaker proof."""
import os
import unittest
from datetime import timedelta

os.environ.setdefault('SECRET_KEY','mapping-policy-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')

from core.game_mapping_policy import prepare,lifetime,corroboration_useful
from core.game_title_match import decide
from test_game_title_match import game


class MappingPolicyTests(unittest.TestCase):
    title={'id':'tt1','title':'Example','year':2017}

    def test_persistent_gaps_are_unknown_for_thirty_days_not_exclusions(self):
        value=prepare({'id':'tt1','status':'unresolved','reason':'ambiguous_candidates'},self.title)
        self.assertEqual(value['status'],'unresolved')
        self.assertEqual(lifetime(value),timedelta(days=30))
        self.assertNotIn('retry_after',value)
        self.assertEqual(lifetime({'status':'unresolved','reason':'incomplete_candidates'}),timedelta(days=1))

    def test_three_failures_stop_immediate_retries_without_fabricating_a_negative(self):
        value=None
        for delay in (60,120,None):
            value=prepare({'id':'tt1','status':'unresolved','reason':'timeout','retry_after':60},self.title,value)
            self.assertEqual(value.get('retry_after'),delay)
            self.assertEqual(value['status'],'unresolved')
        self.assertEqual(lifetime(value),timedelta(days=1))
        changed=prepare({'status':'unresolved','retry_after':60},{**self.title,'year':2018},value)
        self.assertEqual(changed['retry_attempts'],1)

    def test_cancelled_port_and_year_disagreements_can_still_gain_useful_support(self):
        rows=[game(10,'Example'),game(20,'Example',game_type={'id':11},
            parent_game={'id':30},release_dates=[{'status':{'name':'Cancelled'}}])]
        self.assertTrue(corroboration_useful(decide(self.title,rows,complete=True,branches=[])))
        self.assertTrue(corroboration_useful(decide(self.title,[game(10,'Example',year=2018)],complete=True,branches=[])))

    def test_candidate_projection_keeps_identity_and_poster_but_omits_optional_gallery(self):
        from core.game_title_candidates import FIELDS,identity_facts
        self.assertIn('cover.url',FIELDS.split(','))
        self.assertFalse(any(field=='summary' or field.startswith(('artworks.','screenshots.')) for field in FIELDS.split(',')))
        raw=game(10,'Example',summary='Long description',artworks=[{'id':1}],screenshots=[{'id':2}])
        compact={key:value for key,value in raw.items() if key not in ('summary','artworks','screenshots')}
        self.assertEqual(identity_facts(raw),identity_facts(compact))
