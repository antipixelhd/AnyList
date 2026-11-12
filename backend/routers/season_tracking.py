"""Owner-only season tracking; the list editor remains independent."""
from datetime import date
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db import get_db
from dependencies import get_tracking_write_user
from models import User
from models.seasons import SeriesSeason, SeasonTracking
from models.tracking import TrackedEntry
from core.season_completion import reconcile

router = APIRouter(prefix='/tracking/entry', tags=['tracking'])
Database = Annotated[AsyncSession, Depends(get_db)]
Viewer = Annotated[User, Depends(get_tracking_write_user)]


def presentation(season, tracking):
    return {'id': season.id, 'season_number': season.season_number, 'episode_order': season.episode_order,
            'total_episodes': len(season.episode_ids), 'watched_episodes': tracking.watched_episodes,
            'completed': tracking.completed, 'completion_date': tracking.completion_date,
            'date_source': tracking.date_source, 'date_manual': tracking.date_manual,
            'date_inferred': tracking.date_source == 'inferred'}


@router.get('/{media_id}/seasons')
async def seasons(media_id: int, db: Database, viewer: Viewer):
    entry = await db.scalar(select(TrackedEntry.id).where(TrackedEntry.user_id == viewer.id, TrackedEntry.media_id == media_id))
    if entry is None:
        raise HTTPException(404, 'Tracked series not found')
    rows = (await db.execute(select(SeriesSeason, SeasonTracking).join(SeasonTracking,
        SeasonTracking.season_id == SeriesSeason.id).where(SeriesSeason.media_id == media_id,
        SeasonTracking.user_id == viewer.id, SeasonTracking.current.is_(True))
        .order_by(SeriesSeason.season_number))).all()
    return [presentation(season, tracking) for season, tracking in rows]


class SeasonDateUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    completion_date: date | None = None
    automatic: bool = False


@router.patch('/{media_id}/seasons/{season_id}')
async def update_season_date(media_id: int, season_id: int, body: SeasonDateUpdate, db: Database, viewer: Viewer):
    if not body.automatic and 'completion_date' not in body.model_fields_set:
        raise HTTPException(422, 'Provide completion_date or enable automatic dates')
    rows = await reconcile(db, viewer.id, media_id)
    selected = next(((s, t) for s, t in rows if s.id == season_id), None)
    if selected is None:
        raise HTTPException(404, 'Tracked season not found')
    season, tracking = selected
    if not tracking.completed:
        raise HTTPException(409, 'Season is not completed')
    tracking.date_manual = not body.automatic
    if not body.automatic:
        tracking.completion_date = body.completion_date
        tracking.date_source = 'manual'
        tracking.evidence = {'rule': 'manual'}
    await db.flush()
    await reconcile(db, viewer.id, media_id)
    await db.commit()
    return presentation(season, tracking)
