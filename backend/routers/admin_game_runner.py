from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from core.game_background import state, summary, wake_runner
from core.game_work import wake
from db import get_db
from dependencies import require_admin

router=APIRouter(prefix='/admin/game-runner',tags=['admin'],dependencies=[Depends(require_admin)])
DB=Annotated[AsyncSession,Depends(get_db)]


class RunnerControl(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool


@router.get('')
async def status(db:DB,response:Response)->dict:
    response.headers['Cache-Control']='private, no-store'
    result=await summary(db)
    await db.commit()
    return result


@router.patch('')
async def control(body:RunnerControl,db:DB,response:Response)->dict:
    runner=await state(db)
    runner.enabled=body.enabled
    await db.commit()
    wake()
    wake_runner()
    return await status(db,response)
