"""Admin diagnostics deliberately exclude credentials and raw provider payloads."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

HealthAction = Literal['metadata', 'characters', 'statistics', 'game_scan', 'game_reconcile']


class GameScanCandidate(BaseModel):
    id: int
    name: str
    steam_app_id: str | None = None
    igdb_id: str | None = None
    duplicate_id: int | None = None
    game_class: str | None = None
    status: Literal['unknown', 'match', 'ambiguous', 'unmatched', 'excluded', 'failed', 'conflict', 'reconciled', 'already_linked']
    detail: str


class GameScan(BaseModel):
    checked_at: datetime
    next_after_id: int | None = None
    candidates: list[GameScanCandidate]


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
    game_scan: GameScan | None = None


class DataHealth(BaseModel):
    checked_at: datetime
    tracked_titles: int
    catalogue_records: int
    checks: list[HealthCheck]
    job: HealthJob | None = None


class RepairRequest(BaseModel):
    action: HealthAction
    scan_job_id: int | None = Field(default=None, gt=0)
    after_id: int = Field(default=0, ge=0)
    confirm: Literal['reconcile'] | None = None
    model_config = {'extra': 'forbid'}

    @model_validator(mode='after')
    def validate_operation(self):
        if self.action == 'game_reconcile':
            if self.scan_job_id is None or self.confirm != 'reconcile' or self.after_id:
                raise ValueError('Reconciliation requires a completed scan and explicit confirmation.')
        elif self.scan_job_id is not None or self.confirm is not None or (self.after_id and self.action != 'game_scan'):
            raise ValueError('Unexpected scan or confirmation parameters.')
        return self
