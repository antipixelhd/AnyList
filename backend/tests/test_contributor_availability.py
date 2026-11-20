import os
import unittest
from unittest.mock import AsyncMock,patch

os.environ.setdefault('SECRET_KEY','availability-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.contributor_availability import person_domains,movie_job
from core.contributor_evidence import load_evidence


class AvailabilitySourceTests(unittest.IsolatedAsyncioTestCase):
    def test_person_roles_types_and_unknown_rows_do_not_invent_empty_domains(self):
        film={'id':1,'media_type':'movie','title':'Film','character':'Role'}
        series={'id':2,'media_type':'tv','name':'Show','job':'Director'}
        result=person_domains({'cast':[film],'crew':[series,{**film,'job':'Actor'}]})
        self.assertEqual(result,{'actor':{'movie':'available','series':'empty'},'staff':{'movie':'empty','series':'available'}})
        for broken in (None,{},[{}],[{**film,'media_type':'unknown'}]):
            result=person_domains({'cast':broken,'crew':[]})
            self.assertEqual(result['actor'],{'movie':'unknown','series':'unknown'})
        self.assertEqual(person_domains({'cast':[{**film,'character':'Self (archive footage)'}],'crew':[]})['actor']['movie'],'empty')

    async def test_company_verification_has_no_release_or_popularity_cutoff_and_requires_real_zero(self):
        _,config=movie_job('studio',429)
        movie=AsyncMock(return_value={'results':[],'total_results':0})
        shows=AsyncMock(return_value={'results':[{'id':1}],'total_results':10000})
        with patch('core.tmdb.discover_movies',movie),patch('core.tmdb.discover_shows',shows):
            result=await load_evidence({'kind':'studio','config':config},'key')
        self.assertEqual(result['domain_availability']['studio'],{'movie':'empty','series':'available'})
        self.assertNotIn('released_before',movie.call_args.kwargs)
        self.assertEqual(movie.call_args.kwargs['vote_count_min'],0)
        self.assertEqual(movie.await_count+shows.await_count,2)
        for bad in ({'results':[]},{'results':[],'total_results':1},{'results':[{}],'total_results':0}):
            with patch('core.tmdb.discover_movies',AsyncMock(return_value=bad)),patch('core.tmdb.discover_shows',shows):
                with self.assertRaises(ValueError):await load_evidence({'kind':'studio','config':config},'key')
