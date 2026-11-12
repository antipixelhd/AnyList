import os
import unittest

os.environ.setdefault('SECRET_KEY','parent-proposal-tests')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_credit_parents import parent_proposal,automatic_parent_valid,VERSION
from core.game_title_match import decide
from test_game_title_match import game


def automatic_proof():
    title={'id':'tt2','title':'Main game: Complete Edition','year':2017}
    child=game(20,title['title'],version_parent=10)
    parent=game(10,'Main game',year=2010)
    return {**decide(title,[child],complete=True,branches=[])['evidence'],
        'dependencies':['igdb:20','igdb:10','imdb:tt2','igdb-name:main game complete edition'],
        'variant':{'version':VERSION,'child':child,'parent':parent}}


class ParentProposalTests(unittest.TestCase):
    def setUp(self):
        self.title={'id':'tt1','title':'Example Game: Complete Edition','year':2017}
        self.edition=game(name=self.title['title'],version_parent=30)

    def test_exact_unique_edition_preserves_child_identity_and_explicit_parent(self):
        result=parent_proposal(self.title,[self.edition],complete=True)
        self.assertEqual((result['child_igdb_id'],result['parent_igdb_id'],result['kind']),(10,30,'edition'))

    def test_port_uses_its_explicit_parent_without_changing_the_main_title_identity(self):
        result=parent_proposal(self.title,[{**self.edition,'version_parent':None,'game_type':{'id':11},'parent_game':{'id':50}}],complete=True)
        self.assertEqual((result['parent_igdb_id'],result['kind']),(50,'port'))

    def test_missing_year_wrong_year_unknown_competitor_and_incomplete_pool_do_not_propose(self):
        for title,rows,complete in [(self.title,[self.edition],False),
                ({**self.title,'year':None},[self.edition],True),
                ({**self.title,'year':2018},[self.edition],True),
                (self.title,[self.edition,{**self.edition,'id':20,'first_release_date':None}],True),
                (self.title,[self.edition,{**self.edition,'id':20}],True)]:
            with self.subTest(title=title,rows=rows,complete=complete):
                self.assertIsNone(parent_proposal(title,rows,complete=complete))

    def test_invalid_or_self_parent_and_unrelated_work_do_not_propose(self):
        for change in ({'version_parent':True},{'version_parent':10},{'version_parent':None},
                {'version_parent':{'id':-2}},{'game_type':None}):
            self.assertIsNone(parent_proposal(self.title,[{**self.edition,**change}],complete=True))

    def test_automatic_participation_requires_the_full_unique_variant_and_independent_parent(self):
        import copy
        proof=automatic_proof()
        self.assertTrue(automatic_parent_valid(proof))
        for mutate in (
            lambda p:p.update(retrieval_complete=False),
            lambda p:p.update(version=999),
            lambda p:p['variant'].update(version=999),
            lambda p:p['variant']['parent'].update(id=11),
            lambda p:p['variant']['parent'].update(version_parent=99),
            lambda p:p['variant']['parent'].update(game_type={'id':3}),
            lambda p:p['variant']['parent'].update(first_release_date=None),
            lambda p:p['variant']['parent'].update(first_release_date=game(year=2024)['first_release_date']),
            lambda p:p['variant']['child'].update(version_parent=20),
            lambda p:p['variant']['child'].update(game_type={'id':1}),
            lambda p:p['variant']['child'].update(game_type={'id':2}),
            lambda p:p['variant']['child'].update(game_type={'id':14}),
            lambda p:p['imdb'].update(year=2018),
            lambda p:p['candidates'].append({**p['candidates'][0],'id':30}),
            lambda p:p['candidates'].append({**p['candidates'][0],'id':30,'year_match':False,'years':[]}),
        ):
            other=copy.deepcopy(proof);mutate(other)
            self.assertFalse(automatic_parent_valid(other),other)

    def test_automatic_port_keeps_its_scope_and_explicit_parent(self):
        proof=automatic_proof();child=proof['variant']['child']
        child.pop('version_parent');child.update(game_type={'id':11},parent_game={'id':10})
        proof['candidates']=decide(proof['imdb'],[child],complete=True,branches=[])['evidence']['candidates']
        self.assertTrue(automatic_parent_valid(proof))
