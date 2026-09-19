from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.web_push import vapid_public_key
from db import get_db
from dependencies import get_current_user
from models.users import User
from models.tracking import WebPushSubscription

router = APIRouter()


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=255)
    auth: str = Field(min_length=8, max_length=255)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=16, max_length=4096)
    keys: PushKeys


@router.get("/vapid-public-key")
async def public_key(_: User = Depends(get_current_user)):
    return {"public_key": vapid_public_key()}


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    count = (await db.execute(select(func.count()).select_from(WebPushSubscription).where(
        WebPushSubscription.user_id == user.id,
    ))).scalar_one()
    return {"enabled": count > 0, "subscriptions": count}


@router.post("/subscriptions")
async def subscribe(body: PushSubscriptionIn, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    row = (await db.execute(select(WebPushSubscription).where(
        WebPushSubscription.endpoint == body.endpoint,
    ))).scalar_one_or_none()
    if row is None:
        row = WebPushSubscription(user_id=user.id, endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth)
        db.add(row)
    else:
        row.user_id = user.id
        row.p256dh = body.keys.p256dh
        row.auth = body.keys.auth
    row.user_agent = (request.headers.get("user-agent") or "")[:500] or None
    await db.commit()
    return {"enabled": True}


@router.delete("/subscriptions")
async def unsubscribe(body: PushSubscriptionIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await db.execute(delete(WebPushSubscription).where(
        WebPushSubscription.user_id == user.id,
        WebPushSubscription.endpoint == body.endpoint,
    ))
    await db.commit()
    return {"enabled": False}
