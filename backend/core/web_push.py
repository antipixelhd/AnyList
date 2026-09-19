"""Durable Web Push delivery for sync-completion rating prompts."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from sqlalchemy import select

from core.config import settings
from core.tracking_rules import effective_score
from models import Media, Rating
from models.tracking import SyncReview, TrackedEntry, WebPushSubscription

_KEY_FILE = Path(settings.data_dir) / "web-push-vapid-private.pem"


def _private_key() -> ec.EllipticCurvePrivateKey:
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _KEY_FILE.exists():
        return serialization.load_pem_private_key(_KEY_FILE.read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    _KEY_FILE.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    try:
        os.chmod(_KEY_FILE, 0o600)
    except OSError:
        pass
    return key


def vapid_public_key() -> str:
    point = _private_key().public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(point).rstrip(b"=").decode("ascii")


def _poster_url(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith(("https://", "http://")):
        return path
    if path.startswith("/"):
        return f"https://image.tmdb.org/t/p/w500{path}"
    return None


async def queue_sync_completion_rating(db, *, user_id: int, media: Media, entry: TrackedEntry, source: str) -> None:
    """Create one in-app/native rating prompt for a newly synced completion."""
    if entry.status != "completed" or effective_score(entry.rating_mode, entry.manual_score, entry.season_scores) is not None:
        return
    existing = (await db.execute(select(SyncReview.id).where(
        SyncReview.user_id == user_id,
        SyncReview.media_id == media.id,
        SyncReview.kind == "rating_needed",
        SyncReview.dismissed_at.is_(None),
    ).limit(1))).first()
    if existing:
        return
    db.add(SyncReview(
        user_id=user_id,
        media_id=media.id,
        provider=source,
        kind="rating_needed",
        state="confirmed",
        proposed_status="completed",
        priority="low",
        payload={"push_state": "pending", "delivered_subscription_ids": []},
        message=f"{media.title} completed. Rate now!",
    ))


async def resolve_rating_prompts(db, *, user_id: int, media_id: int) -> None:
    prompts = (await db.execute(select(SyncReview).where(
        SyncReview.user_id == user_id,
        SyncReview.media_id == media_id,
        SyncReview.kind == "rating_needed",
        SyncReview.dismissed_at.is_(None),
    ))).scalars()
    now = datetime.utcnow()
    for prompt in prompts:
        prompt.dismissed_at = now
        prompt.payload = {**(prompt.payload or {}), "push_state": "rated"}


def _send(subscription: WebPushSubscription, payload: dict) -> None:
    webpush(
        subscription_info={
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
        },
        data=json.dumps(payload),
        vapid_private_key=str(_KEY_FILE),
        vapid_claims={"sub": settings.server_url},
        ttl=86400,
    )


async def dispatch_rating_pushes(db) -> dict[str, int]:
    stats = {"sent": 0, "expired": 0, "failed": 0}
    rows = (await db.execute(
        select(SyncReview, Media, TrackedEntry)
        .join(Media, Media.id == SyncReview.media_id)
        .join(TrackedEntry, (TrackedEntry.user_id == SyncReview.user_id) & (TrackedEntry.media_id == SyncReview.media_id))
        .where(
            SyncReview.kind == "rating_needed",
            SyncReview.dismissed_at.is_(None),
            SyncReview.state == "confirmed",
        )
        .order_by(SyncReview.created_at)
        .limit(100)
    )).all()
    for review, media, entry in rows:
        if (review.payload or {}).get("push_state") == "sent":
            continue
        if effective_score(entry.rating_mode, entry.manual_score, entry.season_scores) is not None:
            await resolve_rating_prompts(db, user_id=review.user_id, media_id=media.id)
            continue
        subscriptions = list((await db.execute(select(WebPushSubscription).where(
            WebPushSubscription.user_id == review.user_id,
        ))).scalars())
        if not subscriptions:
            continue
        data = dict(review.payload or {})
        delivered = {int(value) for value in data.get("delivered_subscription_ids", [])}
        expired: set[int] = set()
        payload = {
            "title": media.title,
            "body": f"{media.title} completed. Rate now!",
            "icon": _poster_url(media.poster_path) or f"{settings.server_url.rstrip('/')}/web-app-manifest-192x192.png",
            "image": _poster_url(media.backdrop_path or media.poster_path),
            "badge": f"{settings.server_url.rstrip('/')}/favicon-96x96.png",
            "tag": f"rating-needed-{review.id}",
            "url": f"/title/{media.id}?rate=1",
        }
        for subscription in subscriptions:
            if subscription.id in delivered:
                continue
            try:
                await asyncio.to_thread(_send, subscription, payload)
                delivered.add(subscription.id)
                stats["sent"] += 1
            except WebPushException as error:
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status in (404, 410):
                    await db.delete(subscription)
                    expired.add(subscription.id)
                    stats["expired"] += 1
                else:
                    stats["failed"] += 1
        data["delivered_subscription_ids"] = sorted(delivered)
        active_ids = {subscription.id for subscription in subscriptions} - expired
        if active_ids and active_ids.issubset(delivered):
            data["push_state"] = "sent"
            data["push_sent_at"] = datetime.utcnow().isoformat()
        review.payload = data
    await db.commit()
    return stats
