"""Game tracking contracts use catalogue identity, never a legacy Media id."""
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GameStatus = Literal['planning', 'playing', 'paused', 'completed', 'dropped']
AchievementMode = Literal['percentage', 'count', 'targeted']
Seconds = Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]


class GameSubject(BaseModel):
    kind: Literal['game'] = 'game'
    catalogue_id: int


class ChildPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int | None = Field(None, ge=1)
    status: GameStatus | None = None
    score: float | None = Field(None, ge=0, le=10, allow_inf_nan=False)
    start_date: date | None = None
    finish_date: date | None = None

    @field_validator('score')
    @classmethod
    def score_increment(cls, value):
        from core.tracking_rules import normalize_score
        return normalize_score(value)


class GamePatch(ChildPatch):
    favorite: bool | None = None
    notes: str | None = Field(None, max_length=20000)
    total_seconds: Seconds | None = None
    completion_seconds: Seconds | None = None
    replay_count: int | None = Field(None, strict=True, ge=0, le=2147483647)
    achievement_mode: AchievementMode | None = None
    achievement_count: int | None = Field(None, strict=True, ge=0, le=2147483647)
    achievement_percentage: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    unlocked_ids: list[Annotated[int, Field(strict=True, gt=0)]] | None = Field(None, max_length=20000)
    unlock_updates: dict[Annotated[int, Field(gt=0)], bool] | None = Field(None, max_length=20000)
    expected_achievement_total: int | None = Field(None, strict=True, ge=0)
    confirm_simplified: bool = False


class AchievementSummary(BaseModel):
    availability: Literal['unknown', 'partial', 'complete', 'none'] = 'unknown'
    total: int | None = None
    definitions_available: bool = False
    mode: AchievementMode | None = None
    count: int | None = None
    percentage: float | None = None
    exact_count: int = 0


class GameEntryOut(BaseModel):
    subject: GameSubject
    title: str
    year: str | None = None
    genres: list[str] = Field(default_factory=list)
    image_url: str | None = None
    game_class: str
    base_work_id: int | None = None
    listed: bool = False
    status: GameStatus | None = None
    score: float | None = None
    notes: str | None = None
    start_date: date | None = None
    finish_date: date | None = None
    total_seconds: int | None = None
    completion_seconds: int | None = None
    completion_observed_at: datetime | None = None
    completion_source: str | None = None
    completion_comparison_eligible: bool = False
    replay_count: int | None = None
    favorite: bool = False
    revision: int | None = None
    updated_at: datetime | None = None
    achievements: AchievementSummary = Field(default_factory=AchievementSummary)


class GameListProfile(BaseModel):
    id: int
    username: str
    display_name: str
    profile_color: str = '#3db4f2'
    background_url: str | None = None
    has_avatar: bool = False
    owner: bool = False
    following: bool = False
    follows_you: bool = False
    combine_lists: bool = True


class GamePlayingOut(BaseModel):
    results: list[GameEntryOut]
    total: int


class GameListOut(BaseModel):
    results: list[GameEntryOut]
    total: int
    offset: int
    limit: int
    profile: GameListProfile
    status_counts: dict[GameStatus, int] = Field(default_factory=dict)
    genres: list[str] = Field(default_factory=list)
    years: list[str] = Field(default_factory=list)


class FavoritePatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    favorite: bool


class GameStateBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    work_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(max_length=250)


class AchievementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    app_id: str
    api_name: str
    name: str
    description: str | None = None
    icon_url: str | None = None
    locked_icon_url: str | None = None
    hidden: bool
    global_percentage: float | None = None
    unlocked: bool = False


class AchievementPage(BaseModel):
    results: list[AchievementOut]
    total: int
    offset: int
    limit: int
