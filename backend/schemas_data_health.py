"""Admin diagnostics deliberately exclude credentials and raw provider payloads."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

HealthAction = Literal['metadata', 'characters', 'statistics']


class HealthExample(BaseModel):
    id: int
    name: str
    detail: str = ''
    href: str | None = None


class HealthCheck(BaseModel):
    code: str
    label: str
    description: str
    severity: Literal['warning', 'info']
    count: int
    unit: str
    action: HealthAction | None = None
    examples: list[HealthExample] = Field(default_factory=list)


class HealthJob(BaseModel):
    id: int
    action: HealthAction
    status: Literal['pending', 'running', 'completed', 'failed', 'interrupted']
    step: str | None = None
    completed_steps: int
    total_steps: int
    results: dict[str, dict[str, int]] = Field(default_factory=dict)
    error: str | None = None
    updated_at: datetime


class DataHealth(BaseModel):
    checked_at: datetime
    tracked_titles: int
    catalogue_records: int
    checks: list[HealthCheck]
    job: HealthJob | None = None


class RepairRequest(BaseModel):
    action: HealthAction
    model_config = {'extra': 'forbid'}
