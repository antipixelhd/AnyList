"""Public Overview contract; keeps cached data independent of profile permissions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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


class GenreTitle(BaseModel):
    key: str
    title: str
    poster: str | None
    href: str
    score: float


class GenreGroup(MetricGroup):
    browse_filters: dict[Literal["movie", "series"], str]
    top_titles: list[GenreTitle]
    runtime_missing_plays: int = 0


class ActorTitle(GenreTitle):
    score: float | None
    character: str | None
    character_image: str | None


class StudioGroup(MetricGroup):
    href: str
    top_titles: list[GenreTitle]
    runtime_missing_plays: int = 0


class StudioDetail(BaseModel):
    key: str
    name: str
    image: str | None
    description: str | None
    countries: list[str]


class ActorGroup(MetricGroup):
    image: str | None
    href: str
    top_titles: list[ActorTitle]
    runtime_missing_plays: int = 0


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
    genres: list[GenreGroup] = Field(default_factory=list)
    actors: list[ActorGroup] = Field(default_factory=list)
    studios: list[StudioGroup] = Field(default_factory=list)


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
