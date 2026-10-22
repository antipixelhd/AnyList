import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'search-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

import httpx
from fastapi import FastAPI
from core import contributor_search as search
from db import get_db
from dependencies import get_current_user
from routers.tracking import router


def empty():
    return {kind: [] for kind in search.KINDS}


class ContributorSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        search._candidate_cache._store.clear()
        self.db = SimpleNamespace(execute=AsyncMock(), scalar=AsyncMock(), commit=AsyncMock())
        self.db.execute.return_value = SimpleNamespace(all=lambda: [])
        self.viewer = SimpleNamespace(id=7, games_access=True)
        self.local = patch.object(search, 'local_matches', AsyncMock(return_value=empty()))
        self.local_mock = self.local.start()
        self.keys = patch.object(search.settings_store, 'get_user_tmdb_key', AsyncMock(return_value='fixture-key'))
        self.keys.start()
        self.people = patch.object(search.tmdb, 'search_people', AsyncMock(return_value={'results': []}))
        self.people_mock = self.people.start()
        self.companies = patch.object(search.tmdb, 'search_company', AsyncMock(return_value={'results': []}))
        self.company_mock = self.companies.start()
        self.addCleanup(self.local.stop); self.addCleanup(self.keys.stop)
        games = patch.object(search, 'game_matches', AsyncMock(return_value=empty()))
        self.games_mock = games.start(); self.addCleanup(games.stop)
        self.addCleanup(self.people.stop); self.addCleanup(self.companies.stop)
        status = patch.object(search, 'known_principal_status', AsyncMock(return_value={}))
        self.principal_status = status.start()
        self.addCleanup(status.stop)
        verified = patch.object(search, 'verified_studios', AsyncMock(side_effect=lambda db, viewer, rows, key: (rows, False)))
        verified.start()
        self.addCleanup(verified.stop)
        people_verified=patch.object(search,'verified_people',AsyncMock(return_value=False))
        people_verified.start();self.addCleanup(people_verified.stop)

    async def test_polling_reuses_candidates_but_rechecks_eligibility(self):
        self.company_mock.return_value={'results':[{'id':14,'name':'Studio'}]}
        await search.search_contributors(self.db,self.viewer,'Studio','studio')
        await search.search_contributors(self.db,self.viewer,'studio','studio')
        self.company_mock.assert_awaited_once()
        self.local_mock.assert_awaited_once()
        self.assertEqual(search.verified_studios.await_count,2)

    async def test_remote_people_and_studios_link_to_details_and_respect_sections(self):
        self.people_mock.return_value = {'results': [
            {'id': 11, 'name': 'Actor', 'known_for_department': 'Acting', 'known_for':[{'id':1}], 'profile_path': '/actor.jpg'},
            {'id': 12, 'name': 'Director', 'known_for_department': 'Directing', 'known_for':[{'id':1}]},
            {'id': 13, 'name': 'Adult', 'known_for_department': 'Acting', 'adult': True},
        ]}
        self.company_mock.return_value = {'results': [{'id': 14, 'name': 'Studio', 'logo_path': '/logo.png'}]}
        data = await search.search_contributors(self.db, self.viewer, 'Name')
        self.assertEqual(data['results']['actor'][0]['url'], '/actors/tmdb:11')
        self.assertEqual(data['results']['staff'][0]['url'], '/person/tmdb:12')
        self.assertEqual(data['results']['studio'][0]['url'], '/studio/tmdb:14')
        self.assertEqual(len(data['results']['actor']), 1)
        self.assertFalse(data['partial'])
        self.db.commit.assert_not_called()

    async def test_mobile_studio_search_does_not_query_people(self):
        await search.search_contributors(self.db, self.viewer, 'Studio', 'studio')
        self.people_mock.assert_not_called()
        self.company_mock.assert_awaited_once()

    async def test_people_section_does_not_wait_for_studio_discovery(self):
        await search.search_contributors(self.db, self.viewer, 'Name', 'people')
        self.company_mock.assert_not_called()
        self.people_mock.assert_awaited_once()

    async def test_actor_search_does_not_query_studios_or_return_crew(self):
        self.people_mock.return_value = {'results': [{'id': 12, 'name': 'Director', 'known_for_department': 'Directing'}]}
        data = await search.search_contributors(self.db, self.viewer, 'Director', 'actor')
        self.assertEqual(data['results'], empty())
        self.company_mock.assert_not_called()

    async def test_remote_search_rejects_people_without_known_works_and_ranks_recognition(self):
        self.people_mock.return_value = {'results':[
            {'id':1,'name':'Test Unknown','known_for_department':'Acting','known_for':[],'popularity':100},
            {'id':2,'name':'Test Supporting','known_for_department':'Crew','known_for':[{'id':1}],'popularity':100},
            {'id':3,'name':'Test New','known_for_department':'Acting','known_for':[{'id':1}],'popularity':1},
            {'id':4,'name':'Test Known','known_for_department':'Acting','known_for':[{'id':1}],'popularity':20},
        ]}
        data=await search.search_contributors(self.db,self.viewer,'Test')
        self.assertEqual([p['key'] for p in data['results']['actor']],['tmdb:4','tmdb:3'])
        self.assertEqual(data['results']['staff'],[])
        self.assertFalse(any(key.startswith('_') for p in data['results']['actor'] for key in p))

    async def test_remote_results_do_not_reintroduce_known_supporting_crew(self):
        person=SimpleNamespace(id=5,kind='person',canonical_person_id=None,canonical_company_id=None,
            name='Minor Producer',image_url=None)
        self.db.execute.return_value=SimpleNamespace(all=lambda:[(SimpleNamespace(
            namespace='tmdb.person',external_id='11'),person)])
        self.principal_status.return_value={5:False}
        self.people_mock.return_value={'results':[{'id':11,'name':'Minor Producer',
            'known_for_department':'Production','known_for':[{'id':1}]}]}
        data=await search.search_contributors(self.db,self.viewer,'Minor')
        self.assertEqual(data['results'],empty())

    async def test_canonical_identity_deduplicates_provider_results_without_merging_namesakes(self):
        person = SimpleNamespace(id=5, kind='person', canonical_person_id=None, name='Canonical', image_url=None)
        alias = SimpleNamespace(id=6, kind='person', canonical_person_id=5, name='Alias', image_url=None)
        local = search.result(person, 'actor', ['Actor'])
        self.local_mock.return_value = {**empty(), 'actor': [local]}
        self.db.execute.return_value = SimpleNamespace(all=lambda: [(SimpleNamespace(namespace='tmdb.person', external_id='11'), alias)])
        self.people_mock.return_value = {'results': [
            {'id': 11, 'name': 'Alias', 'known_for_department': 'Acting', 'known_for':[{'id':1}]},
            {'id': 15, 'name': 'Canonical', 'known_for_department': 'Acting', 'known_for':[{'id':1}]},
        ]}
        with patch.object(search, 'canonical_people', AsyncMock(return_value={6: person})):
            data = await search.search_contributors(self.db, self.viewer, 'Alias')
        self.assertEqual([row['key'] for row in data['results']['actor']], ['catalogue:5', 'tmdb:15'])
        self.assertEqual(data['results']['actor'][0]['roles'], ['Actor'])

    async def test_provider_failure_preserves_local_matches_and_other_sections(self):
        self.local_mock.return_value = {**empty(), 'actor': [{'key': 'catalogue:5', 'name': 'Saved'}]}
        self.people_mock.side_effect = RuntimeError('private failure')
        self.company_mock.return_value = {'results': [{'id': 14, 'name': 'Studio'}]}
        data = await search.search_contributors(self.db, self.viewer, 'Saved')
        self.assertEqual(data['results']['actor'][0]['name'], 'Saved')
        self.assertEqual(data['results']['studio'][0]['name'], 'Studio')
        self.assertTrue(data['partial'])
        self.assertNotIn('private failure', str(data))

    async def test_without_provider_key_search_is_local_and_bounded(self):
        self.local_mock.return_value = {**empty(), 'actor': [{'key': f'catalogue:{i}'} for i in range(12)]}
        with patch.object(search.settings_store, 'get_user_tmdb_key', AsyncMock(return_value=None)):
            data = await search.search_contributors(self.db, self.viewer, 'Name')
        self.assertEqual(len(data['results']['actor']), 8)
        self.people_mock.assert_not_called(); self.company_mock.assert_not_called()

    async def test_game_only_query_never_fans_out_to_movie_providers_or_verification(self):
        self.games_mock.return_value={**empty(),'actor':[{'key':'catalogue:5','name':'Game actor',
            'roles':['Voice actor'],'url':'/actors/catalogue:5?media_type=game','_works':2}]}
        data=await search.search_contributors(self.db,self.viewer,'Game actor','actor','game')
        self.assertEqual(data['results']['actor'][0]['url'],'/actors/catalogue:5?media_type=game')
        search.settings_store.get_user_tmdb_key.assert_not_awaited()
        self.people_mock.assert_not_awaited();search.verified_people.assert_not_awaited()
        self.assertEqual(data['game_coverage'],'saved_credits')

    async def test_fresh_game_matches_are_not_hidden_by_cached_movie_candidates(self):
        self.games_mock.side_effect=[empty(),{**empty(),'actor':[{'key':'catalogue:5','name':'New actor',
            'roles':['Voice actor'],'url':'/actors/catalogue:5?media_type=game','_works':1}]}]
        first=await search.search_contributors(self.db,self.viewer,'New actor','actor')
        second=await search.search_contributors(self.db,self.viewer,'New actor','actor')
        self.assertFalse(first['results']['actor'])
        self.assertEqual(second['results']['actor'][0]['key'],'catalogue:5')
        movie_only=await search.search_contributors(self.db,self.viewer,'New actor','actor','movies_series')
        self.assertFalse(movie_only['results']['actor'])
        self.people_mock.assert_awaited_once();self.local_mock.assert_awaited_once()
        self.assertEqual(self.games_mock.await_count,2)

    async def test_policy_rechecks_do_not_mutate_the_cached_shortlist(self):
        self.local_mock.return_value={**empty(),'actor':[{'key':'catalogue:5','name':'Saved actor','roles':['Actor']}]}
        attempts=0
        async def verify(db,viewer,groups,key):
            nonlocal attempts
            attempts+=1
            if attempts==1:groups['actor']=[]
            return False
        search.verified_people.side_effect=verify
        first=await search.search_contributors(self.db,self.viewer,'Saved actor','actor','movies_series')
        second=await search.search_contributors(self.db,self.viewer,'Saved actor','actor','movies_series')
        self.assertFalse(first['results']['actor'])
        self.assertEqual(second['results']['actor'][0]['key'],'catalogue:5')
        self.local_mock.assert_awaited_once()

    async def test_canonical_game_merge_preserves_namesakes_and_fresh_shared_fields(self):
        self.local_mock.return_value={**empty(),'actor':[{'key':'catalogue:5','name':'Old name','roles':['Actor'],'image':'old.jpg','_works':1}]}
        self.games_mock.return_value={**empty(),'actor':[
            {'key':'catalogue:5','name':'Shared name','image':None,'roles':['Voice actor'],'url':'/actors/catalogue:5?media_type=game','_works':2},
            {'key':'catalogue:6','name':'Shared name','image':None,'roles':['Voice actor'],'url':'/actors/catalogue:6?media_type=game','_works':1}]}
        data=await search.search_contributors(self.db,self.viewer,'Shared name','actor')
        self.assertEqual([row['key'] for row in data['results']['actor']],['catalogue:5','catalogue:6'])
        self.assertEqual(data['results']['actor'][0]['name'],'Shared name')
        self.assertIsNone(data['results']['actor'][0]['image'])

    async def test_endpoint_is_authenticated_validated_and_trims_queries(self):
        app = FastAPI(); app.include_router(router, prefix='/tracking')
        async def database(): yield self.db
        app.dependency_overrides[get_db] = database
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.get('/tracking/quick-search/contributors?q=Name')).status_code, 401)
            app.dependency_overrides[get_current_user] = lambda: self.viewer
            for query in ('kind=unknown&q=Name', 'q=' + 'x' * 101, 'work_domain=unknown&q=Name'):
                self.assertEqual((await client.get('/tracking/quick-search/contributors?' + query)).status_code, 422)
            response = await client.get('/tracking/quick-search/contributors?q=x')
            self.assertEqual(response.json()['results'], empty())
            self.local_mock.assert_not_called()
            await client.get('/tracking/quick-search/contributors?q=%20Name%20&kind=staff')
            self.local_mock.assert_awaited_once_with(self.db, 'Name', 'staff')

    async def test_revocation_excludes_games_from_a_warm_shared_search(self):
        self.games_mock.return_value={**empty(),'actor':[{'key':'catalogue:5','name':'Restricted actor',
            'roles':['Voice actor'],'url':'/actors/catalogue:5?media_type=game','_works':1}]}
        first=await search.search_contributors(self.db,self.viewer,'Restricted actor','actor')
        self.assertEqual(len(first['results']['actor']),1)
        self.viewer.games_access=False
        second=await search.search_contributors(self.db,self.viewer,'Restricted actor','actor')
        self.assertFalse(second['results']['actor'])
        self.assertNotIn('game_coverage',second)
        self.assertEqual(self.games_mock.await_count,1)


class CreditClassificationTests(unittest.TestCase):
    def test_classification_matches_people_pages_and_ignores_thanks_and_archive_roles(self):
        person = SimpleNamespace(kind='person')
        credit = lambda role, label=None, character=None: SimpleNamespace(role=role, role_label=label, character_label=character)
        self.assertEqual(search.credit_kind(person, credit('actor')), 'actor')
        self.assertEqual(search.credit_kind(person, credit('guest', 'Voice Actor')), 'actor')
        self.assertEqual(search.credit_kind(person, credit('director', 'Director')), 'staff')
        self.assertIsNone(search.credit_kind(person, credit('thanks', 'Thanks')))
        self.assertIsNone(search.credit_kind(person, credit('actor', character='Archive footage')))
        self.assertEqual(search.credit_kind(SimpleNamespace(kind='organization'), credit('producer')), 'studio')
        self.assertIsNone(search.credit_kind(SimpleNamespace(kind='organization'), credit('network')))
