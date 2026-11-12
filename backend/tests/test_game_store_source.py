import asyncio
import json
import os
import unittest
from datetime import datetime,timezone

os.environ.setdefault('SECRET_KEY','game-store-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
import httpx
from core.catalogue_providers import ProviderError,ProviderHTTP
from core.game_store_source import text_rows,age_ratings,igdb_age_ratings,GameStoreSource
from core.game_rating_source import GameRatingSource


class GameStoreParserTests(unittest.TestCase):
    def test_requirements_keep_plain_text_and_structure_without_executable_html(self):
        rows=text_rows('<strong>Minimum:</strong><br><ul><li>Requires 64-bit CPU</li><li><strong>Memory:</strong> 12 GB RAM</li><li>Storage: 60 GB</li><li>Additional Notes: </li></ul><script>BAD_SCRIPT</script><img src=x onerror=BAD_EVENT><iframe>BAD_FRAME</iframe><p>Notes: &lt;safe&gt; &amp; fast</p>')
        self.assertEqual(rows,[{'label':None,'value':'Requires 64-bit CPU'},{'label':'Memory','value':'12 GB RAM'},{'label':'Storage','value':'60 GB'},{'label':'Notes','value':'<safe> & fast'}])
        self.assertNotIn('BAD_',json.dumps(rows));self.assertEqual(text_rows(''),[])
        with self.assertRaises(ProviderError):text_rows('x'*24001)

    def test_real_board_rating_never_infers_from_generic_required_age_or_generated_rating(self):
        rows=age_ratings({'esrb':{'rating':'m','required_age':'16','descriptors':'Violence\r\nLanguage'},'usk':{'rating':'16'},'steam_germany':{'rating':'16'},'pegi':{'rating':'999'}})
        self.assertEqual([(row['board'],row['rating']) for row in rows],[('ESRB','Mature 17+'),('USK','16')])
        self.assertEqual(rows[0]['descriptors'],['Violence','Language']);self.assertEqual(age_ratings(None),[])

    def test_igdb_uses_expanded_current_fields_and_keeps_source_identity(self):
        rows=igdb_age_ratings([{'id':4,'organization':{'name':'PEGI'},'rating_category':{'rating':'18'}},{'category':4,'rating':21}])
        self.assertEqual(rows,[{'board':'PEGI','region':'Europe','rating':'18','descriptors':[],'source':'igdb','source_id':'4'}])


class GameStoreTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_critic_and_info_share_one_store_request_and_ignore_mac_only_requirements(self):
        count=0
        async def respond(request):
            nonlocal count
            count+=1;await asyncio.sleep(.01)
            self.assertEqual(request.url.params['cc'],'US')
            return httpx.Response(200,json={'987654321':{'success':True,'data':{'steam_appid':987654321,
                'metacritic':{'score':91},'ratings':{'usk':{'rating':'18'}},'pc_requirements':[],
                'mac_requirements':{'minimum':'Mac only'}}}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            http=ProviderHTTP(client=client)
            info=GameStoreSource(None);ratings=GameRatingSource(None);info.http=ratings.http=http
            a,b=await asyncio.gather(info.fetch('987654321','info',datetime.now(timezone.utc)),ratings.fetch('987654321','critic',datetime.now(timezone.utc)))
            self.assertEqual(count,1);self.assertIsNone(a['requirements']);self.assertEqual(b['score'],91)
