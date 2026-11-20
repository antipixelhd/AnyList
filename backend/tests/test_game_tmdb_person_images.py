"""TMDB portraits minimize requests without weakening canonical identity merges."""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock,patch

os.environ.setdefault('SECRET_KEY','portrait-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')

from core import game_tmdb_person_images as portraits


class TMDBPortraitTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_supplies_image_without_profile_or_filmography_request(self):
        source=portraits.TMDBPersonSource('fixture')
        with patch('core.tmdb.find_by_external_id',AsyncMock(return_value={
            'person_results':[{'id':7,'profile_path':'/photo.jpg'}]})) as find, \
            patch('core.tmdb.get_person_profile',AsyncMock()) as profile:
            result=await source.portraits(['imdb:nm1'])
        self.assertEqual(result['imdb:nm1']['image'],'https://image.tmdb.org/t/p/w185/photo.jpg')
        find.assert_awaited_once_with('nm1','imdb_id',api_key='fixture');profile.assert_not_awaited()

    async def test_known_identity_skips_find_and_checks_response_identity(self):
        with patch('core.tmdb.find_by_external_id',AsyncMock()) as find, \
            patch('core.tmdb.get_person_profile',AsyncMock(side_effect=[
                {'id':7,'profile_path':'/photo.jpg'},{'id':99,'profile_path':'/wrong.jpg'}])):
            result=await portraits.TMDBPersonSource('fixture').portraits(['tmdb:7','tmdb:8'])
        self.assertTrue(result['tmdb:7']['image']);self.assertEqual(result['tmdb:8']['error'],'identity_conflict')
        find.assert_not_awaited()

    async def test_four_slots_are_shared_between_batches(self):
        active=maximum=0
        async def find(native,*args,**kwargs):
            nonlocal active,maximum
            active+=1;maximum=max(maximum,active)
            await asyncio.sleep(.01);active-=1
            return {'person_results':[{'id':int(native[2:]),'profile_path':'/photo.jpg'}]}
        with patch('core.tmdb.find_by_external_id',find):
            first,second=await asyncio.gather(*[portraits.TMDBPersonSource('fixture').portraits(
                [f'imdb:nm{i}' for i in range(start,start+8)]) for start in (1,9)])
        self.assertEqual(maximum,4);self.assertEqual(len(first)+len(second),16)

    async def test_missing_ambiguous_malformed_and_failed_are_distinct_per_actor(self):
        responses=[{'person_results':[]},{'person_results':[{'id':1,'profile_path':None}]},
            {'person_results':[{'id':1},{'id':2}]},{'person_results':[{'id':1,'profile_path':'https://wrong.test/x'}]},
            RuntimeError('failure'),{'person_results':[{'id':1,'profile_path':'/ok.jpg'}]}]
        with patch('core.tmdb.find_by_external_id',AsyncMock(side_effect=responses)):
            result=await portraits.TMDBPersonSource('fixture').portraits([f'imdb:nm{i}' for i in range(6)])
        self.assertEqual([result[f'imdb:nm{i}'].get('outcome') for i in range(3)],['unresolved','missing','ambiguous'])
        self.assertEqual(result['imdb:nm3']['error'],'bad_response')
        self.assertEqual(result['imdb:nm4']['error'],'unavailable');self.assertTrue(result['imdb:nm5']['image'])

    async def test_time_budget_includes_slot_wait_and_does_not_leave_provider_tasks(self):
        cancelled=0
        async def stalled(*args,**kwargs):
            nonlocal cancelled
            try:await asyncio.Event().wait()
            finally:cancelled+=1
        with patch.object(portraits,'TIMEOUT_SECONDS',.03),patch('core.tmdb.find_by_external_id',stalled):
            result=await portraits.TMDBPersonSource('fixture').portraits([f'imdb:nm{i}' for i in range(8)])
        self.assertTrue(all(value=={'error':'timeout'} for value in result.values()))
        self.assertEqual(cancelled,4)
