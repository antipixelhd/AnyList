"""Public link-preview data; authentication never grants preview access."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from core.share_preview import card_data, description, image_bytes
from db import get_db

router = APIRouter(prefix='/sharing', tags=['sharing'])
DB = Annotated[AsyncSession, Depends(get_db)]
SharePath = Annotated[str, Query(min_length=1, max_length=250)]


@router.get('/metadata')
async def metadata(db: DB, path: SharePath, response: Response) -> dict:
    response.headers['Cache-Control'] = 'no-store'
    try:
        card, _ = await card_data(db, path)
    except TimeoutError:
        raise HTTPException(503, 'Preview temporarily unavailable') from None
    return {'title':card['title'], 'description':description(card)}


@router.get('/image')
async def image(db: DB, path: SharePath) -> Response:
    try:
        card, avatar = await card_data(db, path)
        content = await image_bytes(card, avatar)
    except TimeoutError:
        raise HTTPException(503, 'Preview temporarily unavailable') from None
    # Recheck access on every origin fetch. External messaging caches are independent.
    return Response(content, media_type='image/jpeg', headers={'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'})
