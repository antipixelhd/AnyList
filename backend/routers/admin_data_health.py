"""Admin-only diagnostics and durable, non-destructive repair jobs."""
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from core.data_health import enqueue, job_view, latest_job, report
from core.data_health_repairs import run_job
from db import get_db
from dependencies import require_admin
from models.users import User
from schemas_data_health import DataHealth, HealthJob, RepairRequest

router = APIRouter(prefix='/admin/data-health', tags=['admin'], dependencies=[Depends(require_admin)])
DB = Annotated[AsyncSession, Depends(get_db)]
Admin = Annotated[User, Depends(require_admin)]


@router.get('', response_model=DataHealth)
async def get_data_health(db: DB, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return await report(db)


@router.get('/job', response_model=HealthJob | None)
async def get_repair_job(db: DB, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return job_view(await latest_job(db))


@router.post('/repair', response_model=HealthJob, status_code=202)
async def repair_data_health(body: RepairRequest, db: DB, admin: Admin, background: BackgroundTasks, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    job = await enqueue(db, admin.id, body.action)
    if job is None:
        raise HTTPException(status_code=409, detail='A data health repair is already running.')
    background.add_task(run_job, job.id)
    return job_view(job)
