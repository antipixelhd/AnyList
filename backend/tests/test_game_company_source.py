import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY','company-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_company_source import GameCompanySource,SIZE
from core.catalogue_providers import ProviderError


def game(native,kind=0,**extra):
    return {'id':native,'name':f'Game {native}','game_type':{'id':kind},**extra}


def involved(native,game_id,**extra):
    return {'id':native,'company':70,'game':game_id,'developer':True,'publisher':False,'supporting':False,'porting':False,**extra}


class CompanySourceTests(unittest.IsolatedAsyncioTestCase):
    def source(self,values):
        source=object.__new__(GameCompanySource);source.api=SimpleNamespace(igdb=AsyncMock(side_effect=values));return source

    async def test_explicit_role_records_fold_ports_and_editions_but_keep_remasters_and_expansions(self):
        rows=[involved(i,native) for i,native in enumerate((10,20,30,40,50,60,70,80),start=1)]
        base=game(10,summary='Base metadata')
        source=self.source([rows,[base,game(20,11,parent_game={'id':10,'game_type':{'id':0}}),
            game(30,version_parent=10),game(40,9,parent_game=base),game(50,2,parent_game=base),game(60,13),game(70,1),game(80,14)]])
        result=await source.page('70')
        self.assertTrue(result['complete']);self.assertEqual(result['omitted'],0)
        self.assertEqual([row['doc']['work']['identities'][0]['external_id'] for row in result['documents']],['10','10','10','40','50'])
        self.assertEqual(source.api.igdb.await_count,2)
        expansion=result['documents'][-1]['doc']
        self.assertEqual(expansion['relationships'][0]['target']['description'],'Base metadata')

    async def test_full_page_requires_a_continuation_and_id_cursor_is_explicit(self):
        rows=[involved(i+101,i+1) for i in range(SIZE)]
        source=self.source([rows,[game(i+1) for i in range(SIZE)]])
        result=await source.page('70',100)
        self.assertFalse(result['complete']);self.assertEqual(result['cursor'],100+SIZE)
        self.assertIn('company=70 & id>100',source.api.igdb.call_args_list[0].args[1])

    async def test_missing_game_identity_and_wrong_company_are_failures_not_empty_results(self):
        for values in [[[involved(1,10)],[]],[[involved(1,10,company=99)]]]:
            with self.assertRaises(ProviderError):await self.source(values).page('70')

    async def test_unknown_kind_and_parent_cycle_remain_partial_without_title_matching(self):
        source=self.source([[involved(1,10),involved(2,20),involved(3,30)],
            [game(10,999),game(20,11,parent_game={'id':30}),game(30,11,parent_game={'id':20})]])
        result=await source.page('70')
        self.assertEqual(result['documents'],[]);self.assertEqual(result['omitted'],3)

    async def test_missing_parent_metadata_is_fetched_in_one_batch_for_multiple_variants(self):
        source=self.source([[involved(1,20),involved(2,30)],
            [game(20,11,parent_game={'id':10}),game(30,version_parent=10)],[game(10)]])
        result=await source.page('70')
        self.assertEqual([row['doc']['work']['identities'][0]['external_id'] for row in result['documents']],['10','10'])
        self.assertEqual(source.api.igdb.await_count,3)
