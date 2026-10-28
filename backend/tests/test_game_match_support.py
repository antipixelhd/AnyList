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
    async def test_exact_publisher_and_historical_official_cast_verify_identity_without_inventing_a_release_year(self):
        self.title['year']=2016
        self.game['involved_companies'].append({'publisher':True,'company':{'name':'Example Publisher'}})
        self.store['42']['data'].update(developers=['Different Developer Inc.'],publishers=['Example Publisher'],
            detailed_description='A game',release_date={'coming_soon':False,'date':'Sep 22, 2017'})
        # A legacy cached store projection omitted publishers. Upgrade only
        # this failed developer gate; ordinary positive caches stay reusable.
        self.sources.cache.rows['42']={
            **{k:v for k,v in self.store['42']['data'].items() if k!='publishers'},'fingerprint':'legacy'}
        self.credits.return_value['rows']=[person('nm1','First Actor'),person('nm2','Second Actor')]
        def news(*args,**kwargs):
            return {'appnews':{'appid':42,'newsitems':[{'gid':'old-cast','feedname':'steam_community_announcements',
                'title':'Example Game English Dub Trailer','contents':'Voice cast: First Actor and Second Actor.',
                'date':1451606400}] if kwargs['params'].get('enddate') else []}}
        self.http.request.side_effect=news
        result,snapshots=await self.decision()
        self.assertEqual(result['status'],'verified')
        proof=result['evidence']['candidates'][0]['corroboration']
        self.assertFalse(proof['developer_agreement']);self.assertTrue(proof['publisher_agreement'])
        self.assertEqual(proof['publishers'],['example publisher'])
        self.assertEqual(proof['announcement'],'old-cast')
        self.assertEqual(result['evidence']['candidates'][0]['years'],[2017])
        self.assertNotIn('early_access',proof)
        self.assertTrue(valid_binding_evidence({'matching':result['evidence'],'matcher_version':VERSION,'igdb_id':'10'},'tt1'))
        legacy={**result['evidence'],'version':6}
        self.assertFalse(valid_binding_evidence({'matching':legacy,'matcher_version':6,'igdb_id':'10'},'tt1'))
        self.assertIn('42:publishers-v1',[r['key'] for r in snapshots if r['kind']=='game_match_store'])
        await self.decision();self.request.assert_awaited_once();self.credits.assert_awaited_once()
        extra,_=await corroborate(self.sources,self.title,[self.game,{**self.game,'id':20}])
        ambiguous=decide(self.title,[self.game,{**self.game,'id':20}],complete=True,branches=[],corroboration=extra)
        self.assertEqual(ambiguous['reason'],'ambiguous_candidates')

    async def test_publisher_only_needs_two_cast_members_on_a_trusted_own_title_announcement(self):
        self.game['involved_companies'].append({'publisher':True,'company':{'name':'Example Publisher'}})
        self.store['42']['data'].update(developers=['Different Developer'],publishers=['Example Publisher'])
        for state in ('one_actor','untrusted','cross_promotion','different_publisher'):
            with self.subTest(state=state):
                self.sources.cache.rows.clear()
                self.credits.return_value['rows']=[person('nm1','First Actor',[{'name':'Chelsea Williams'}])]+(
                    [] if state=='one_actor' else [person('nm2','Second Actor')])
                self.store['42']['data']['publishers']=['Other Publisher' if state=='different_publisher' else 'Example Publisher']
                self.http.request.return_value={'appnews':{'appid':42,'newsitems':[{'gid':'cast',
                    'feedname':'pcgamer' if state=='untrusted' else 'steam_community_announcements',
                    'title':'Another Game' if state=='cross_promotion' else 'Example Game Voice Cast',
                    'contents':'Voice cast: First Actor and Second Actor.'}]}}
                result,_=await self.decision()
                self.assertNotEqual(result['status'],'verified')

    def test_publisher_proof_cannot_use_duplicate_people_or_weaker_character_evidence(self):
        proof={'app':'42','store_title':'Example Game','publisher_agreement':True,'developer_agreement':False,
            'characters':['Chelsea Williams']}
        for people in ([],[{'id':'nm1'}],[{'id':'nm1'},{'id':'nm1'}],[{'id':'nm1'},{'id':'not-a-person-id'}]):
            result=decide(self.title,[self.game],complete=True,branches=[],corroboration={10:{**proof,'people':people}})
            self.assertNotEqual(result['status'],'verified')

    async def test_company_names_must_agree_in_the_same_role(self):
        self.store['42']['data'].update(developers=['Different Developer'],publishers=['Example Studio'])
        result,_=await self.decision()
        self.assertNotEqual(result['status'],'verified')
        self.credits.assert_not_awaited()

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

    async def test_exact_imdb_full_release_day_and_publisher_launch_explain_earlier_store_year(self):
        self.title['year']=2020
        self.game=game(name='Example Game',year=2019,external_games=self.game['external_games'],involved_companies=self.game['involved_companies'])
        self.store['42']['data'].update(detailed_description='A strategy game',release_date={'coming_soon':False,'date':'Jun 20, 2019'})
        self.credits.return_value['header']={'releaseDate':{'year':2020,'month':2,'day':25}}
        self.http.request.return_value={'appnews':{'appid':42,'newsitems':[{
            'gid':'full-launch','feedname':'steam_community_announcements','title':'Example Game Season One',
            'contents':'Example Game is coming out of Early Access, and today officially marks Season One. With this launch, play now.',
            'date':1582652671}]}}
        result,snapshots=await self.decision()
        self.assertEqual(result['status'],'verified')
        self.assertEqual(result['evidence']['candidates'][0]['years'],[2019])
        proof=result['evidence']['candidates'][0]['corroboration']['full_release']
        self.assertEqual(proof['date'],'2020-02-25')
        self.assertTrue(valid_binding_evidence({'matcher_version':VERSION,'igdb_id':'10','matching':result['evidence']},'tt1'))
        # Reuse the complete public evidence rather than replaying provider calls.
        before=self.http.request.await_count
        await self.decision();self.assertEqual(self.http.request.await_count,before)

    async def test_full_release_requires_exact_day_actual_launch_and_the_same_game(self):
        for text,stamp in [('Example Game is out of Early Access today.',1582560000),
                ('Example Game will be out of Early Access next week. Play today.',1582652671),
                ('Another Game is out of Early Access today.',1582652671),
                ('Example Game is not out of Early Access today.',1582652671)]:
            with self.subTest(text=text,stamp=stamp):
                self.sources.cache.rows.clear()
                self.title['year']=2020
                self.game=game(name='Example Game',year=2019,external_games=self.game['external_games'],involved_companies=self.game['involved_companies'])
                self.store['42']['data'].update(detailed_description='A strategy game',release_date={'coming_soon':False,'date':'Jun 20, 2019'})
                self.credits.return_value['header']={'releaseDate':{'year':2020,'month':2,'day':25}}
                self.http.request.return_value={'appnews':{'appid':42,'newsitems':[{
                    'gid':'other','feedname':'steam_community_announcements','title':'Release notes','contents':text,'date':stamp}]}}
                result,_=await self.decision();self.assertEqual(result['status'],'unresolved')

    async def test_non_store_detailed_identity_date_explains_the_regional_release_year(self):
        self.title['year']=2009
        self.game=game(name='Example Game',year=2010)
        header={'id':'tt1','titleText':{'text':'Example Game'},'releaseYear':{'year':2009},
            'releaseDate':{'year':2010,'month':3,'day':29}}
        with patch('core.game_match_support.IMDbGameSource.title',AsyncMock(return_value=header)) as identity:
            result,snapshots=await self.decision()
            self.assertEqual(result['status'],'verified')
            self.assertEqual(result['evidence']['imdb']['year'],2009)
            self.assertEqual(result['evidence']['candidates'][0]['years'],[2010])
            self.assertEqual(snapshots[0]['kind'],'game_match_identity')
            self.assertTrue(valid_binding_evidence({'matcher_version':VERSION,'igdb_id':'10','matching':result['evidence']},'tt1'))
            await self.decision();identity.assert_awaited_once()
        self.request.assert_not_awaited();self.credits.assert_not_awaited()

    async def test_regional_date_requires_current_same_id_name_year_and_complete_valid_date(self):
        self.title['year']=2009;self.game=game(name='Example Game',year=2010)
        base={'id':'tt1','titleText':{'text':'Example Game'},'releaseYear':{'year':2009},
            'releaseDate':{'year':2010,'month':3,'day':29}}
        for change in ({'id':'tt2'},{'titleText':{'text':'Another Game'}},{'releaseYear':{'year':2008}},
                {'releaseDate':{'year':2010,'month':None,'day':None}},
                {'releaseDate':{'year':2010,'month':2,'day':30}},
                {'releaseDate':{'year':2011,'month':3,'day':29}}):
            with self.subTest(change=change):
                self.sources.cache.rows.clear()
                with patch('core.game_match_support.IMDbGameSource.title',AsyncMock(return_value={**base,**change})):
                    result,_=await self.decision();self.assertEqual(result['status'],'unresolved')

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
