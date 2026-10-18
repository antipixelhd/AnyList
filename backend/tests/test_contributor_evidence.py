import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'evidence-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')
from core import contributor_evidence as evidence


FILTERS = {'min_runtime':30, 'excluded_genres':[]}


def film(**values):
    return {'id':1, 'media_type':'movie', 'release_date':'2025-01-01', 'vote_count':500, **values}


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_person_evidence_is_shared_across_filters_but_studio_variants_preserve_them(self):
        other = {**FILTERS, 'min_runtime':1000}
        self.assertEqual(evidence.evidence_key('person',1,FILTERS,True), evidence.evidence_key('person',1,other,False))
        self.assertNotEqual(evidence.evidence_key('studio',1,FILTERS,True), evidence.evidence_key('studio',1,other,True))
        self.assertEqual(evidence.evidence_key('studio',429,FILTERS,True),evidence.evidence_key('studio',9993,FILTERS,True))

    def test_visibility_role_filters_deduplication_and_unknown_are_distinct(self):
        self.assertIsNone(evidence.work_summary(None,'actor',FILTERS,True))
        self.assertIsNone(evidence.work_summary({'complete':False},'studio',FILTERS,True))
        self.assertEqual(evidence.work_summary({'complete':True},'studio',FILTERS,True)['works'],0)
        rows = [film(),film(),film(id=2,adult=True),film(id=3,character='Self (archive footage)'),film(id=4,runtime=2)]
        self.assertEqual(evidence.work_summary({'cast':rows},'actor',FILTERS,True)['works'],1)
        anime = film(genre_ids=[16],original_language='ja')
        self.assertEqual(evidence.work_summary({'cast':[anime]},'actor',FILTERS,False)['works'],0)

    async def test_verification_fetches_person_once_and_preserves_both_roles(self):
        profile={'id':7,'combined_credits':{'cast':[film()],'crew':[film(job='Director')]}}
        with patch.object(evidence.tmdb,'get_person',AsyncMock(return_value=profile)) as provider:
            data=await evidence.load_evidence({'kind':'person','native_id':7},'key')
        self.assertEqual(provider.await_count,1)
        self.assertEqual(evidence.work_summary(data,'actor',FILTERS,True)['works'],1)
        self.assertEqual(evidence.work_summary(data,'staff',FILTERS,True)['works'],1)

    async def test_studio_verification_is_two_calls_and_continues_instead_of_inventing_empty(self):
        key,config=evidence.evidence_key('studio',429,FILTERS,True)
        job={'kind':'studio','config':config,'payload':None}
        movies=AsyncMock(return_value={'results':[], 'total_pages':3})
        series=AsyncMock(return_value={'results':[], 'total_pages':0})
        with patch.object(evidence.tmdb,'discover_movies',movies),patch.object(evidence.tmdb,'discover_shows',series):
            data=await evidence.load_evidence(job,'key')
            self.assertFalse(data['complete'])
            self.assertEqual(data['pages'],{'movie':2,'series':None})
            movies.return_value={'results':[film()], 'total_pages':3}
            data=await evidence.load_evidence({**job,'payload':data},'key')
        self.assertTrue(data['complete'])
        self.assertEqual(movies.await_count,2)
        self.assertEqual(series.await_count,1)
        self.assertEqual(movies.call_args.kwargs['with_companies'],'429|9993|165407')

    async def test_malformed_provider_data_is_not_verified_empty(self):
        with patch.object(evidence.tmdb,'get_person',AsyncMock(return_value={'id':7})):
            with self.assertRaises(ValueError):
                await evidence.load_evidence({'kind':'person','native_id':7},'key')

    async def test_studio_eligibility_has_a_lifetime_budget_when_every_work_is_excluded(self):
        job={'kind':'studio','native_id':7,'config':{'companies':[7], 'filters':FILTERS,'show_anime':True}}
        provider=AsyncMock(return_value={'results':[], 'total_pages':500})
        with patch.object(evidence.tmdb,'discover_movies',provider),patch.object(evidence.tmdb,'discover_shows',provider):
            first=await evidence.load_evidence(job,'key')
            last=await evidence.load_evidence({**job,'payload':first},'key')
        self.assertTrue(last['complete'])
        self.assertEqual(last['scope'],'eligibility_sample')
        self.assertEqual(last['pages'],{'movie':None,'series':None})
        self.assertEqual(provider.await_count,4)

    async def test_actor_full_credits_override_shortlist_and_empty_results_stay_out(self):
        from core import contributor_search as search
        def person(native, name, known):
            return {'key':f'tmdb:{native}','name':name,'_native':native,'_known_for':known}
        groups={'actor':[person(1,'Empty',[film()]),person(2,'Beyond shortlist',[film(vote_count=1)]),
            person(3,'Unknown',[film(runtime=2)]),person(4,'Known',[film()])], 'staff':[]}
        key=lambda native:evidence.evidence_key('person',native,FILTERS,True)[0]
        payloads={key(1):{'cast':[], 'complete':True},key(2):{'cast':[film()], 'complete':True}}
        with patch.object(search,'result_filters',AsyncMock(return_value=FILTERS)),patch.object(
            search,'local_evidence',AsyncMock(return_value={})),patch.object(
            search,'cached_evidence',AsyncMock(return_value=(payloads,True))),patch.object(
            search.tmdb,'get_person',AsyncMock(side_effect=AssertionError('No inline verification'))):
            pending=await search.verified_people(SimpleNamespace(get=AsyncMock(return_value=None)),SimpleNamespace(id=1),groups,'key')
        self.assertTrue(pending)
        self.assertEqual([r['name'] for r in groups['actor']],['Beyond shortlist','Known'])

    async def test_quick_people_verification_bounds_jobs_and_local_candidates(self):
        from core import contributor_search as search
        groups = {section: [{'key': f'tmdb:{i}', '_native': i, '_known_for': [film(job='Director')]}
                           for i in range(1, 31)] for section in ('actor', 'staff')}
        with patch.object(search, 'result_filters', AsyncMock(return_value=FILTERS)), \
             patch.object(search, 'local_evidence', AsyncMock(return_value={})) as local, \
             patch.object(search, 'cached_evidence', AsyncMock(return_value=({}, True))) as cached:
            await search.verified_people(SimpleNamespace(get=AsyncMock(return_value=None)),
                SimpleNamespace(id=1), groups, 'key')
        self.assertEqual(len(cached.await_args.args[1]), 8)
        self.assertEqual([len(call.args[1]) for call in local.await_args_list], [8, 8])
        self.assertEqual([len(group) for group in groups.values()], [8, 8])
