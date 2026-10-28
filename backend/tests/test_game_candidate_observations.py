import os
import unittest
from copy import deepcopy

os.environ.setdefault('SECRET_KEY','candidate-observations-tests')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_candidate_observations import changes_binding
from core.game_title_candidates import observations
from test_game_title_match import game


class CandidateObservationTests(unittest.TestCase):
    def setUp(self):
        self.raw=game(name='Example',platforms=[{'id':6},{'id':48}])
        self.evidence={'igdb_id':'10','dependencies':['igdb-query:old','igdb-name:example'],
            'matching':{'imdb':{'id':'tt1','title':'Example','year':2017},'support':{
                'candidate_key':'old','candidate_fingerprint':'old-hash','candidate_observations':observations([self.raw])}}}

    def data(self,rows,**values):
        return {'rows':rows,'scope_names':['example'],'scope_fingerprint':'new-hash','scope_observations':observations(rows),**values}

    def test_different_query_observing_identical_facts_keeps_accepted_mapping(self):
        self.assertFalse(changes_binding(self.evidence,'new',self.data([self.raw])))
        reordered={**self.raw,'platforms':list(reversed(self.raw['platforms']))}
        self.assertFalse(changes_binding(self.evidence,'new',self.data([reordered])))
        unrelated=game(20,name='Unrelated')
        self.assertFalse(changes_binding(self.evidence,'new',self.data([self.raw,unrelated])))

    def test_metadata_change_and_new_namesake_invalidate_matching_evidence(self):
        for changed in ({**self.raw,'name':'Changed Name'}, {**self.raw,'game_type':{'id':1}},
                {**self.raw,'parent_game':{'id':30}}, {**self.raw,'platforms':[{'id':7}]},
                {**self.raw,'involved_companies':[{'id':3,'developer':True}]}):
            with self.subTest(changed=changed):self.assertTrue(changes_binding(self.evidence,'new',self.data([changed])))
        self.assertTrue(changes_binding(self.evidence,'new',self.data([self.raw,game(20,name='Example')])))

    def test_own_query_change_or_incomplete_traversal_revalidates(self):
        self.assertTrue(changes_binding(self.evidence,'old',self.data([])))
        self.assertFalse(changes_binding(self.evidence,'old',self.data([self.raw],scope_fingerprint='old-hash')))
        self.assertTrue(changes_binding(self.evidence,'old',self.data([self.raw],scope_fingerprint='old-hash',complete=False)))

    def test_legacy_proofs_upgrade_when_observed_and_empty_search_does_not_disprove_them(self):
        legacy=deepcopy(self.evidence);legacy['matching']['support'].pop('candidate_observations')
        self.assertTrue(changes_binding(legacy,'new',self.data([self.raw])))
        self.assertFalse(changes_binding(legacy,'new',self.data([])))
