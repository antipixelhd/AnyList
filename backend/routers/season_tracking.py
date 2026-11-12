"""Owner-only season tracking and focused editor reads."""
import asyncio
from datetime import date
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from db import get_db
from dependencies import get_tracking_write_user
from models import Media, Show, User
from models.base import MediaType
from models.seasons import SeriesSeason, SeasonTracking
from models.tracking import TrackedEntry
from core.season_completion import reconcile
from core.series_editor import editor_context, editor_summary
from core.tracking_access import anime_is_visible
from core.tracking_projection import is_anime

router = APIRouter(prefix='/tracking/entry', tags=['tracking'])
Database = Annotated[AsyncSession, Depends(get_db)]
Viewer = Annotated[User, Depends(get_tracking_write_user)]


def presentation(season, tracking):
    return {'id': season.id, 'season_number': season.season_number, 'episode_order': season.episode_order,
            'total_episodes': len(season.episode_ids), 'watched_episodes': tracking.watched_episodes,
            'completed': tracking.completed, 'completion_date': tracking.completion_date,
            'start_date': tracking.start_date, 'start_source': tracking.start_source,
            'start_manual': tracking.start_manual,
            'date_source': tracking.date_source, 'date_manual': tracking.date_manual,
            'date_inferred': tracking.date_source == 'inferred'}


async def editor_media(db, media_id):
    media = await db.get(Media, media_id)
    if not media or media.media_type != MediaType.series or (is_anime(media) and not await anime_is_visible(db)):
        raise HTTPException(404, 'Series not found')
    return media


@router.get('/{media_id}/series-editor')
async def read_series_editor(media_id: int, response: Response, db: Database, viewer: Viewer):
    response.headers['Cache-Control'] = 'private, no-store'
    media = await editor_media(db, media_id)
    from core.tracking_episode_loading import complete, ensure
    if not complete(media) and (media.tmdb_id or media.tvdb_id):
        try:
            state = await asyncio.wait_for(ensure(db, media, viewer.id), timeout=.25)
        except TimeoutError:
            state = {'pending': True, 'retry_after': 2}
        if state.get('ready'):
            await db.refresh(media)
        if not complete(media):
            return {'ready': False, 'retry_after': state.get('retry_after', 10), 'seasons': [],
                    'pending': state.get('pending', False)}
    context = await editor_context(db, media, viewer.id)
    return await editor_summary(db, media, viewer.id, context)


@router.get('/{media_id}/series-editor/episodes/{episode_id}')
async def read_editor_episode(media_id: int, episode_id: int, response: Response, db: Database, viewer: Viewer):
    response.headers['Cache-Control'] = 'private, no-store'
    media = await editor_media(db, media_id)
    identities = [column == value for column, value in
                  ((Show.tmdb_id, media.tmdb_id), (Show.tvdb_id, media.tvdb_id)) if value]
    if not identities:
        raise HTTPException(404, 'Episode not found')
    row = (await db.execute(select(Media.id, Media.title, Media.runtime, Media.release_date,
        Media.poster_path, Media.tmdb_data['still_path'].as_string()).join(Show, Media.show_id == Show.id)
        .where(Media.id == episode_id, Media.media_type == MediaType.episode, Media.season_number > 0,
               or_(*identities), or_(Media.release_date <= date.today().isoformat(),
                   Media.tmdb_data['tracking_import_released'].as_boolean().is_(True))))).first()
    if row is None:
        raise HTTPException(404, 'Episode not found')
    return {'id': row[0], 'title': row[1], 'runtime': row[2], 'air_date': row[3], 'image': row[4] or row[5]}


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
