"""Shared person and production-company pages; private state belongs to the viewer."""

from typing import Literal

from pydantic import BaseModel, Field

ContributorKind = Literal["actor", "staff", "studio"]
ListScope = Literal["all", "in", "out"]


class ContributorLink(BaseModel):
    label: str
    href: str


class ContributorWork(BaseModel):
    key: str
    id: int | None = None
    tmdb_id: int | None = None
    type: Literal["movie", "series"]
    title: str
    poster: str | None = None
    backdrop: str | None = None
    href: str | None = None
    year: str = ""
    release_date: str | None = None
    roles: list[str] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    list_status: str | None = None
    score: float | None = None
    rating_mode: str = "manual"
    entry: dict | None = None
    editor_library: dict | None = None


class ContributorPage(BaseModel):
    kind: ContributorKind
    key: str
    name: str
    image: str | None = None
    banner: str | None = None
    description: str | None = None
    birthday: str | None = None
    deathday: str | None = None
    place_of_birth: str | None = None
    department: str | None = None
    aliases: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    headquarters: str | None = None
    links: list[ContributorLink] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    works: list[ContributorWork]
    known_works: int
    list_count: int | None = None
    total: int | None = None
    page: int
    has_more: bool
    next_cursor: str | None = None
    source: Literal["local", "provider"] = "local"
    notice: str | None = None
