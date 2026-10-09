"""Public Overview contract; keeps cached data independent of profile permissions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MediaScope = Literal["all", "movie", "series"]


class MetricGroup(BaseModel):
    key: str
    label: str
    titles: int
    minutes: float
    mean_score: float | None
    rated_titles: int


class Distribution(BaseModel):
    key: str
    label: str
    titles: int


class CountryDistribution(Distribution):
    share: float


class OverviewTotals(BaseModel):
    listed_titles: int
    watched_titles: int
    episode_plays: int
    distinct_episodes: int
    watch_minutes: float
    watch_days: float
    planned_minutes: float
    planned_days: float
    mean_score: float | None
    standard_deviation: float | None
    rated_titles: int


class Overview(BaseModel):
    media_type: MediaScope
    totals: OverviewTotals
    scores: list[MetricGroup]
    episode_counts: list[MetricGroup]
    statuses: list[Distribution]
    formats: list[Distribution]
    countries: list[CountryDistribution]
    release_years: list[MetricGroup]
    watch_years: list[MetricGroup]
    coverage: dict[str, int]


class OverviewResponse(BaseModel):
    profile: dict
    owner: bool
    following: bool
    follows_you: bool
    combine_lists: bool
    status: Literal["pending", "ready", "error"]
    generation: int | None
    computed_at: datetime | None
    next_update_at: datetime
    refreshing: bool
    overview: Overview | None
