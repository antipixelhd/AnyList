import os
import json
import unittest
from datetime import datetime,timezone
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY','game-rating-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_rating_source import GameRatingSource,review_query,summary,critic
from core.catalogue_providers import ProviderError


class GameRatingSourceTests(unittest.TestCase):
    def test_english_recent_window_is_explicit_and_matches_all_time_purchase_policy(self):
        stamp=datetime(2026,10,23,tzinfo=timezone.utc)
        whole=review_query('292030','all_time',stamp);recent=review_query('292030','recent',stamp)
        self.assertEqual(recent['date_range_end']-recent['date_range_start'],30*86400)
        self.assertNotIn('day_range',recent);self.assertEqual(recent['filter'],1)
        self.assertEqual({key:value for key,value in recent.items() if not key.startswith('date_range')},whole)
        self.assertEqual((whole['languages'],whole['review_type'],whole['purchase_type'],whole['num_per_page']),(['english'],0,0,1))

    def test_percentage_comes_from_counts_never_steam_score_category_and_no_reviews_are_unknown(self):
        raw={'query_summary':{'total_positive':7,'total_negative':3,'total_reviews':10,'review_score':9,'review_score_desc':'Positive'},
            'reviews':[{'review':'PRIVATE_REVIEW_SENTINEL','author':{'steamid':'SECRET_AUTHOR'}}]}
        data=summary(raw);self.assertEqual(data['percentage'],70)
        self.assertNotIn('PRIVATE_',str(data));self.assertNotIn('SECRET_',str(data))
        self.assertIsNone(summary({'query_summary':{'total_positive':0,'total_negative':0,'total_reviews':0}})['percentage'])
        self.assertEqual(summary({'query_summary':{'total_positive':0,'total_negative':10,'total_reviews':10}})['percentage'],0)
        for value in [None,{}, {'query_summary':{}},{'query_summary':{'total_positive':True,'total_negative':0,'total_reviews':1}},
            {'query_summary':{'total_positive':4,'total_negative':2,'total_reviews':10}}]:
            with self.assertRaises(ProviderError):summary(value)

    def test_critic_is_bound_to_exact_app_and_verified_absence_is_distinct_from_failure(self):
        def raw(value):return {'42':{'success':True,'data':{'steam_appid':42,'metacritic':value}}}
        self.assertEqual(critic(raw({'score':0,'url':'https://www.metacritic.com/game/pc/game/'}),'42')['score'],0)
        self.assertIsNone(critic(raw(None),'42')['score'])
        for value in [None,{}, {'42':{'success':False}},{'42':{'success':True,'data':{'steam_appid':99}}},raw({'score':True}),
            raw({'score':101}),raw({'score':80,'url':'https://evil.test/game/pc/game/'})]:
            with self.assertRaises(ProviderError):critic(value,'42')


class GameRatingTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_page_summary_only_uses_standard_api_and_explicit_input_json(self):
        source=GameRatingSource(None);source.http.request=AsyncMock(return_value={'query_summary':{'total_positive':8,'total_negative':2,'total_reviews':10}})
        data=await source.fetch('42','recent',datetime(2026,10,23,tzinfo=timezone.utc))
        source.http.request.assert_awaited_once()
        args,kwargs=source.http.request.call_args
        self.assertEqual(args,('steam','GET','https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/'))
        self.assertFalse(kwargs['cache']);query=json.loads(kwargs['params']['input_json'])
        self.assertEqual(query['num_per_page'],1);self.assertEqual(data['percentage'],80)
        self.assertNotIn('key',query);self.assertNotIn('reviews',data)
