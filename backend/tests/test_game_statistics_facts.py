import os
import unittest
from datetime import date
from copy import deepcopy

os.environ.setdefault('SECRET_KEY','game-stats-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_statistics_facts import build_statistics


def fact(native,**values):
    return {'id':native,'title':f'Game {native}','poster':None,'status':'playing','score':None,'finish_date':None,
        'total_seconds':None,'completion_seconds':None,'replay_count':None,'achievement_mode':None,
        'achievement_count':None,'achievement_percentage':None,'exact_count':0,'achievements':{},
        'estimates':{},'genres':['RPG'],'release_date':'2020-01-01',**values}


class GameStatisticsFactsTests(unittest.TestCase):
    def test_parent_totals_planned_unknown_achievements_dates_and_three_comparison_cohorts(self):
        rows=[fact(1,status='completed',score=8,finish_date=date(2024,1,2),total_seconds=20*3600,completion_seconds=10*3600,
                replay_count=2,achievement_mode='count',achievement_count=1,achievements={'total':10},
                estimates={'main_story_seconds':5*3600,'main_sides_seconds':10*3600,'completionist_seconds':20*3600}),
            fact(2,status='completed',score=6,total_seconds=100*3600,completion_seconds=20*3600,
                achievement_mode='percentage',achievement_percentage=50),
            fact(3,total_seconds=0,exact_count=3),
            fact(4,status='planning',score=10,total_seconds=999*3600,replay_count=99,achievement_mode='percentage',
                achievement_percentage=100,genres=['Planned genre'],estimates={'main_sides_seconds':2*3600}),
            fact(5,status='completed',score=7,finish_date=date(2025,1,1),total_seconds=5*3600,completion_seconds=10*3600,
                achievement_mode='targeted',exact_count=1,achievements={'total':2}),fact(6,status='paused')]
        result=build_statistics(rows,[]);overview=result['overview'];totals=overview['totals']
        self.assertEqual((totals['listed_games'],totals['played_games'],totals['completed_games']),(6,5,3))
        self.assertEqual(totals['recorded_seconds'],125*3600);self.assertEqual(totals['known_time_games'],4)
        self.assertEqual((totals['mean_score'],totals['rated_games'],totals['replays']),(7,3,2))
        self.assertAlmostEqual(totals['achievement_percentage'],(10+50+50)/3)
        self.assertEqual((totals['achievement_progress_games'],totals['achievement_count'],totals['achievement_count_games']),(3,2,2))
        self.assertEqual(overview['planned'],{'games':1,'estimated_seconds':7200,'known_estimate_games':1})
        self.assertEqual(overview['coverage']['undated_completions'],1)
        self.assertEqual(sum(row['games'] for row in overview['completion_years']),2)
        self.assertTrue(all(row['recorded_seconds'] is None for row in overview['completion_years']))
        self.assertEqual([row['mean_relative_percent'] for row in overview['comparisons']],[100,0,-50])
        self.assertEqual([row['games'] for row in overview['comparisons']],[1,1,1])
        self.assertEqual([row['label'] for row in result['genres']],['RPG'])
        updated=deepcopy(rows);updated[0]['total_seconds']=200*3600;updated[0]['replay_count']=8
        after=build_statistics(updated,[])['overview']
        self.assertEqual(after['comparisons'],overview['comparisons'])
        self.assertEqual(after['totals']['recorded_seconds'],305*3600)

    def test_disjoint_hour_boundaries_and_contributor_deduplication_without_name_merges(self):
        rows=[fact(i,total_seconds=hours*3600,score=8 if i%2 else None) for i,hours in enumerate([0,1,5,10,20,50,100,200],1)]
        credits=[{'work_id':1,'id':10,'name':'Same name','image':None,'kind':'person','role':role,'role_label':None} for role in ('actor','voice_actor')]
        credits += [{'work_id':2,'id':11,'name':'Same name','image':None,'kind':'person','role':'actor','role_label':None},
            {'work_id':1,'id':20,'name':'Company','image':None,'kind':'organization','role':'developer','role_label':None},
            {'work_id':1,'id':20,'name':'Company','image':None,'kind':'organization','role':'publisher','role_label':None},
            {'work_id':999,'id':10,'name':'Same name','image':None,'kind':'person','role':'actor','role_label':None}]
        result=build_statistics(rows,credits)
        self.assertEqual([row['games'] for row in result['overview']['hours']],[1]*8)
        self.assertEqual([row['games'] for row in result['actors']],[1,1])
        self.assertEqual((result['developers'][0]['games'],result['publishers'][0]['games']),(1,1))
        unknown=build_statistics([fact(1)],[])['overview']['totals']
        self.assertIsNone(unknown['recorded_seconds']);self.assertIsNone(unknown['achievement_percentage']);self.assertIsNone(unknown['achievement_count'])
