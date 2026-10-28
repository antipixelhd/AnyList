"""Uncertain years need independent positive evidence, never a wider year tolerance."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

os.environ.setdefault('SECRET_KEY','mapping-support-tests')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_match_support import corroborate
from core.game_title_match import decide,valid_binding_evidence,VERSION
from test_game_title_match import game
from test_game_imdb_discovery_source import Cache


def person(native,name,characters=None):
    return {'name':{'id':native,'nameText':{'text':name}},'category':{'id':'actor'},'characters':characters}


class SupportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.title={'id':'tt1','title':'Example Game','year':None}
        self.game=game(name='Example Game',external_games=[{'uid':'42','external_game_source':{'name':'Steam'}}],
            involved_companies=[{'developer':True,'company':{'id':1,'name':'Example Studio'}}])
        self.store={'42':{'success':True,'data':{'steam_appid':42,'type':'game','name':'Example Game',
            'developers':['Example Studio'],'release_date':{'coming_soon':True,'date':'Coming soon'},
            'detailed_description':'Play as Chelsea Williams in Example Game.'}}}
        self.http=SimpleNamespace(request=AsyncMock(return_value={'appnews':{'appid':42,'newsitems':[]}}))
        self.sources=SimpleNamespace(api=SimpleNamespace(http=self.http),cache=Cache())
        self.credits=AsyncMock(return_value={'rows':[person('nm1','First Actor',[{'name':'Chelsea Williams'}])],
            'has_next':False,'cursor':None})
        self.patch=patch('core.game_match_support.IMDbGameSource.credits',self.credits);self.patch.start();self.addCleanup(self.patch.stop)
        self.request=AsyncMock(side_effect=lambda http,app:self.store)
        self.patch2=patch('core.game_match_support.store_request',self.request);self.patch2.start();self.addCleanup(self.patch2.stop)

    async def decision(self):
        extra,snapshots=await corroborate(self.sources,self.title,[self.game])
        result=decide(self.title,[self.game],complete=True,branches=[],corroboration=extra)
        return result,snapshots

    async def test_missing_year_uses_independent_store_character_and_studio_without_news(self):
        result,snapshots=await self.decision()
        self.assertEqual(result['status'],'verified');self.assertEqual(len(snapshots),2)
        self.http.request.assert_not_awaited()
        self.assertTrue(valid_binding_evidence({'matching':result['evidence'],'matcher_version':VERSION,'igdb_id':'10'},'tt1'))
        await self.decision()
        self.request.assert_awaited_once();self.credits.assert_awaited_once()

    async def test_missing_year_can_use_two_independently_announced_cast_members(self):
        self.store['42']['data']['detailed_description']='A game without named protagonists'
        self.credits.return_value['rows']=[person('nm1','First Actor'),person('nm2','Second Actor')]
        self.http.request.return_value={'appnews':{'appid':42,'newsitems':[{'gid':'cast','feedname':'steam_community_announcements',
            'title':"Example Game’s Voice Cast Revealed",'contents':'Voice cast: First Actor and Second Actor.','date':1700000000}]}}
        result,_=await self.decision();self.assertEqual(result['status'],'verified')

    async def test_cross_promotion_untrusted_feed_and_single_person_do_not_establish_identity(self):
        self.store['42']['data']['detailed_description']='Play as Hospital Patient'
        self.credits.return_value['rows']=[person('nm1','First Actor',[{'name':'Hospital Patient'}]),person('nm2','Second Actor')]
        self.http.request.return_value={'appnews':{'appid':42,'newsitems':[
            {'gid':'x','feedname':'pcgamer','title':'Example Game','contents':'Voice cast First Actor Second Actor'},
            {'gid':'y','feedname':'steam_community_announcements','title':'Another Game','contents':'Voice cast First Actor Second Actor'},
            {'gid':'z','feedname':'steam_community_announcements','title':'Example Game','contents':'Voice cast First Actor'}]}}
        result,_=await self.decision();self.assertEqual(result['status'],'unresolved')

    async def test_studio_or_store_identity_mismatch_cannot_be_used_as_corroboration(self):
        self.store['42']['data']['developers']=['Different Studio']
        result,_=await self.decision();self.assertEqual(result['status'],'unresolved');self.credits.assert_not_awaited()

    async def test_character_reveal_requires_the_exact_actor_and_character_pair(self):
        self.title['year']=2027
        self.game['first_release_date']=None
        self.store['42']['data']['detailed_description']='A space adventure'
        self.credits.return_value['rows']=[person('nm1','First Actor',[{'name':'Preach'}])]
        self.http.request.return_value={'appnews':{'appid':42,'newsitems':[{'gid':'reveal','feedname':'steam_community_announcements',
            'title':'Meet Preach','contents':'Preach is voiced by First Actor.','date':1700000000}]}}
        result,_=await self.decision();self.assertEqual(result['status'],'verified')

    async def test_year_disagreement_without_explaining_evidence_is_not_a_match(self):
        self.title['year']=2018
        result,_=await self.decision();self.assertEqual(result['status'],'unresolved')

    async def test_early_access_year_explains_identity_without_changing_release_date(self):
        self.title['year']=2018
        self.game=game(name='Example Game',year=2019,external_games=self.game['external_games'],involved_companies=self.game['involved_companies'])
        self.store['42']['data']['release_date']={'coming_soon':False,'date':'Oct 22, 2019'}
        self.http.request.return_value={'appnews':{'appid':42,'newsitems':[{'gid':'release','feedname':'steam_release',
            'title':'Now Available on Steam Early Access - Example Game','contents':'Available now. We will add controller support next month.','date':1519257600}]}}
        result,_=await self.decision();self.assertEqual(result['status'],'verified')
        self.assertEqual(result['evidence']['candidates'][0]['years'],[2019])
        self.assertEqual(self.http.request.await_count,2)

    async def test_future_early_access_plans_are_not_availability_evidence(self):
        self.title['year']=2018
        self.game=game(name='Example Game',year=2019,external_games=self.game['external_games'],involved_companies=self.game['involved_companies'])
        self.store['42']['data']['release_date']={'coming_soon':False,'date':'Oct 22, 2019'}
        self.http.request.return_value={'appnews':{'appid':42,'newsitems':[{'gid':'planned','feedname':'steam_release',
            'title':'Example Game will launch in Early Access next year','contents':'Not available yet.','date':1519257600}]}}
        result,_=await self.decision();self.assertEqual(result['status'],'unresolved')

    async def test_deeper_evidence_never_converts_incomplete_or_competing_candidates_to_unique(self):
        extra,_=await corroborate(self.sources,self.title,[self.game])
        self.assertEqual(decide(self.title,[self.game],complete=False,branches=[],corroboration=extra)['reason'],'incomplete_candidates')
        self.assertEqual(decide(self.title,[self.game,{**self.game,'id':20}],complete=True,branches=[],corroboration=extra)['status'],'unresolved')

    async def test_unsupported_gap_without_store_identity_does_no_extra_network_work(self):
        self.game.pop('external_games')
        result,_=await self.decision();self.assertEqual(result['status'],'unresolved')
        self.http.request.assert_not_awaited();self.credits.assert_not_awaited();self.request.assert_not_awaited()

    async def test_exact_imdb_store_and_igdb_dates_supply_a_scoped_launch_proof(self):
        self.title['year']=2017
        self.game['platforms']=[{'id':6}]
        self.game['release_dates']=[{'date':self.game['first_release_date'],'date_format':{'format':'YYYYMMDD'},'status':{'name':'Full Release'}}]
        self.credits.return_value['header']={'releaseDate':{'year':2017,'month':9,'day':22}}
        self.store['42']['data']['release_date']={'coming_soon':False,'date':'Sep 22, 2017'}
        port=game(20,'Example Game',first_release_date=None,game_type={'id':11},parent_game={'id':100},
            platforms=[{'id':19}],release_dates=[{'status':{'name':'Cancelled'}}])
        extra,_=await corroborate(self.sources,self.title,[self.game,port])
        self.assertEqual(extra[10]['release_scope']['imdb_date'],'2017-09-22')
        self.assertEqual(decide(self.title,[self.game,port],complete=True,branches=[],corroboration=extra)['igdb_id'],10)
