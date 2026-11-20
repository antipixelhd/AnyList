import os
import unittest
from copy import deepcopy

os.environ.setdefault('SECRET_KEY','itad-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.itad_prices import game_id,normalized
from core.catalogue_providers import ProviderError

NATIVE='018d937f-1212-7232-b23f-a046f6fd4a57'


class ItadPriceTests(unittest.TestCase):
    def test_verified_lookup_and_steam_shop_only_preserve_money_and_link(self):
        self.assertEqual(game_id({'found':True,'game':{'id':NATIVE}}),NATIVE)
        price={'currency':'JPY','amount':0,'amountInt':0}
        link='https://isthereanydeal.com/link/original/?x=1'
        result=normalized(NATIVE,[{'id':NATIVE,'deals':[{'shop':{'id':35},'price':{'amount':.01}},
            {'shop':{'id':61},'price':price,'url':link}]}],[{'id':NATIVE,'lows':[]}])
        self.assertEqual(result['current'],price);self.assertEqual(result['url'],link)
        self.assertIsNone(result['historical_low'])
        absent=normalized(NATIVE,[],[]);self.assertIsNone(absent['current'])

    def test_malformed_money_and_response_cannot_clear_a_cached_price_as_absent(self):
        valid=[{'id':NATIVE,'deals':[{'shop':{'id':61},'price':{'amountInt':799,'currency':'EUR'}}]}]
        for money in [True,{}, {'amountInt':-1,'currency':'EUR'},{'amountInt':True,'currency':'EUR'},
            {'amount':float('nan'),'currency':'EUR'},{'amount':float('inf'),'currency':'EUR'},
            {'amountInt':799,'currency':'javascript:alert(1)'}]:
            rows=deepcopy(valid);rows[0]['deals'][0]['price']=money
            with self.subTest(money=money),self.assertRaises(ProviderError):normalized(NATIVE,rows,[])
        for rows in [None,{},[{}],[{'id':NATIVE,'deals':None}],valid*2]:
            with self.subTest(rows=rows),self.assertRaises(ProviderError):normalized(NATIVE,rows,[])
        for lookup in [None,{}, {'found':'true','game':{'id':NATIVE}},{'found':True,'game':{'id':'bad'}}]:
            with self.assertRaises(ProviderError):game_id(lookup)

    def test_unsafe_links_and_ambiguous_shop_records_are_rejected(self):
        item={'shop':{'id':61},'price':{'amountInt':1,'currency':'USD'}}
        for url in ['javascript:alert(1)','http://store.steampowered.com/app/1','https://user:pass@example.test/']:
            with self.assertRaises(ProviderError):normalized(NATIVE,[{'id':NATIVE,'deals':[{**item,'url':url}]}],[])
        with self.assertRaises(ProviderError):normalized(NATIVE,[{'id':NATIVE,'deals':[item,item]}],[])
        with self.assertRaises(ProviderError):normalized(NATIVE,[],[{'id':NATIVE,'lows':[{**item,'timestamp':'bad date'}]}])

class ItadParallelTests(unittest.IsolatedAsyncioTestCase):
    async def test_prices_and_lows_overlap_after_lookup_without_extra_requests(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        from core.catalogue_providers import CatalogueProviders
        from core.config import settings
        entered,release=asyncio.Event(),asyncio.Event();calls=[]
        async def request(provider,method,url,**kwargs):
            calls.append(url)
            if url.endswith('lookup/v1'):return {'found':True,'game':{'id':NATIVE}}
            if len(calls)==3:entered.set()
            await release.wait()
            return []
        with patch.object(settings,'itad_api_key','test-only'):
            task=asyncio.create_task(CatalogueProviders(SimpleNamespace(request=request)).steam_prices('10','DE'))
            try:
                await asyncio.wait_for(entered.wait(),1)
                self.assertEqual(len(calls),3)
            finally:release.set()
            result=await task
        self.assertEqual(result['itad_id'],NATIVE)
        self.assertIsNone(result['current'])

    async def test_provider_failure_cancels_the_other_component(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        from core.catalogue_providers import CatalogueProviders
        from core.config import settings
        started,cancelled=asyncio.Event(),asyncio.Event()
        async def request(provider,method,url,**kwargs):
            if url.endswith('lookup/v1'):return {'found':True,'game':{'id':NATIVE}}
            if url.endswith('prices/v3'):
                await started.wait();raise ProviderError('timeout')
            started.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        with patch.object(settings,'itad_api_key','test-only'):
            with self.assertRaises(ProviderError):
                await CatalogueProviders(SimpleNamespace(request=request)).steam_prices('10','DE')
        self.assertTrue(cancelled.is_set())
