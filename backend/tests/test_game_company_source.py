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

    async def test_primary_scan_defers_ports_and_skips_non_display_metadata(self):
        rows=[{**involved(1,10),'game':game(10)},
            {**involved(2,20),'game':game(20,11)},
            {**involved(3,30),'game':game(30,1)},
            {**involved(4,40),'game':game(40,14)}]
        source=self.source([rows,[game(10)],[game(20,11,parent_game={'id':10})],[game(10)]])
        result=await source.page('70',{'phase':'primary','after':0,'deferred':[]})
        self.assertFalse(result['complete']);self.assertEqual(result['received'],4)
        self.assertEqual([r['record']['game'] for r in result['documents']],[10])
        self.assertNotIn('20',source.api.igdb.call_args_list[1].args[1])
        tail=await source.page('70',result['cursor'])
        self.assertTrue(tail['complete']);self.assertEqual(tail['received'],0)
        self.assertEqual(tail['documents'][0]['doc']['work']['identities'][0]['external_id'],'10')
        self.assertEqual(tail['documents'][0]['record']['game'],20)

    async def test_primary_paging_preserves_variant_tail_and_unknown_types(self):
        rows=[{**involved(i+1,i+1),'game':game(i+1,11 if i==0 else 0)} for i in range(SIZE)]
        source=self.source([rows,[game(i+1) for i in range(1,SIZE)],
            [{**involved(99,100),'game':game(100,999)}],[game(100,999)]])
        first=await source.page('70',{'phase':'primary','after':0,'deferred':[]})
        self.assertEqual(first['cursor']['phase'],'primary')
        second=await source.page('70',first['cursor'])
        self.assertEqual(second['omitted'],1)
        self.assertEqual(second['cursor']['deferred'][0]['game'],1)
        self.assertEqual(second['cursor']['phase'],'variants');self.assertFalse(second['complete'])

    async def test_variant_tail_reuses_parent_metadata_in_the_owned_acquisition(self):
        from core.game_company_source import _game_rows
        source=self.source([[{**involved(1,10),'game':game(10)},
            {**involved(2,20),'game':game(20,11)}],[game(10)],
            [game(20,11,parent_game={'id':10})]])
        token=_game_rows.set({})
        try:
            first=await source.page('70',{'phase':'primary','after':0,'deferred':[]})
            tail=await source.page('70',first['cursor'])
            self.assertTrue(tail['complete'])
            self.assertEqual(tail['documents'][0]['doc']['work']['identities'][0]['external_id'],'10')
            self.assertEqual(source.api.igdb.await_count,3)
        finally:_game_rows.reset(token)
        self.assertIsNone(_game_rows.get())

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
        query=source.api.igdb.call_args_list[1].args[1]
        self.assertIn('cover.url',query)
        self.assertNotIn('artworks.',query)
        self.assertNotIn('summary',query)

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
