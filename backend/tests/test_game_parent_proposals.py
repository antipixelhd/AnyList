import os
import unittest

os.environ.setdefault('SECRET_KEY','parent-proposal-tests')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_credit_parents import parent_proposal
from test_game_title_match import game


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
