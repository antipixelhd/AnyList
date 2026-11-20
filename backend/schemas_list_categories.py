"""Stable category identities with account-specific presentation."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class ListCategory(BaseModel):
    id: str
    label: str
    default_label: str
    description: str
    hidden: bool = False
    can_rename: bool = True
    can_hide: bool = True


class CategoryPresentation(BaseModel):
    revision: int = 0
    categories: list[ListCategory] = Field(default_factory=list)


class CategoryPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: Annotated[int, Field(strict=True, ge=0)]
    order: list[str] | None = Field(None, min_length=1, max_length=20)
    labels: dict[str, str | None] | None = Field(None, max_length=20)
    hidden: list[str] | None = Field(None, max_length=20)
