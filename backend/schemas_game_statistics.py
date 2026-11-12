"""Games-only statistics; movie/series scopes cannot include this contract."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel,Field

GameStatsSection=Literal['overview','genres','developers','publishers','actors','staff']


class GameStatsMetric(BaseModel):
    key:str
    label:str
    games:int
    recorded_seconds:int|None
    known_time_games:int
    mean_score:float|None
    rated_games:int


class GameStatsTitle(BaseModel):
    key:str
    title:str
    poster:str|None
    href:str
    score:float|None


class GameStatsGroup(GameStatsMetric):
    href:str|None=None
    image:str|None=None
    roles:list[str]=Field(default_factory=list)
    top_titles:list[GameStatsTitle]=Field(default_factory=list)


class GameHoursBucket(GameStatsMetric):
    lower_seconds:int
    upper_seconds:int|None


class GameComparison(BaseModel):
    key:str
    label:str
    games:int
    mean_difference_seconds:float|None
    mean_absolute_difference_seconds:float|None
    mean_relative_percent:float|None


class GameStatsTotals(BaseModel):
    listed_games:int
    played_games:int
    completed_games:int
    recorded_seconds:int|None
    known_time_games:int
    mean_score:float|None
    rated_games:int
    standard_deviation:float|None
    replays:int
    replay_games:int
    achievement_percentage:float|None
    achievement_progress_games:int
    achievement_count:int|None
    achievement_count_games:int


class GamePlannedStats(BaseModel):
    games:int
    estimated_seconds:int|None
    known_estimate_games:int


class GameStatusStats(BaseModel):
    key:str
    label:str
    games:int


class GameOverview(BaseModel):
    domain:Literal['game']='game'
    totals:GameStatsTotals
    planned:GamePlannedStats
    statuses:list[GameStatusStats]
    scores:list[GameStatsMetric]
    hours:list[GameHoursBucket]
    release_years:list[GameStatsMetric]
    completion_years:list[GameStatsMetric]
    comparisons:list[GameComparison]
    coverage:dict[str,int]


class GameStatsResponse(BaseModel):
    profile:dict
    owner:bool
    following:bool
    follows_you:bool
    combine_lists:bool
    domain:Literal['game']='game'
    section:GameStatsSection
    status:Literal['pending','ready','error']
    generation:int|None
    computed_at:datetime|None
    refreshing:bool
    data:GameOverview|list[GameStatsGroup]|None
    page:int=1
    total:int|None=None
    has_more:bool=False
    retry_after:int=3
