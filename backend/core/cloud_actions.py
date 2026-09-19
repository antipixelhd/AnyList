"""Durable destructive writes to tracking providers.

These jobs are created only by an explicit local deletion. They never touch a
provider collection/library and cannot run before the provider's first import
has been reviewed and approved.
"""
from sqlalchemy import select
from fastapi import HTTPException

from core import mdblist, simkl, trakt
from core.cloud_reconciliation import require_cloud_reconciliation
from models import User, UserSettings
from models.tracking import CloudAction, SyncReview, TrackedEntry, TrackingDeletion


def deletion_payload(media, episodes=()):
    payload = {"media_type": media.media_type.value, "tmdb_id": media.tmdb_id}
    if media.media_type.value == "series":
        payload["episodes"] = [
            {"season": row.season_number, "episode": row.episode_number}
            for row in episodes
            if row.season_number is not None and row.episode_number is not None
            and (row.season_number or 0) > 0
        ]
    return payload


async def queue_cloud_resets(db, user_id, media, episodes=()):
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one_or_none()
    if not settings:
        return []
    connected = []
    if settings.trakt_access_token and settings.trakt_client_id:
        connected.append("trakt")
    if settings.simkl_access_token and settings.simkl_client_id:
        connected.append("simkl")
    if settings.mdblist_api_key:
        connected.append("mdblist")
    payload = deletion_payload(media, episodes)
    for provider in connected:
        db.add(CloudAction(user_id=user_id, provider=provider, media_id=media.id,
            action="reset", payload=payload))
    return connected


def _mdblist_payload(payload):
    tmdb_id = payload.get("tmdb_id")
    if payload.get("media_type") == "movie":
        return {"movies": [{"ids": {"tmdb": tmdb_id}}]}
    episodes = payload.get("episodes") or []
    seasons = {}
    for row in episodes:
        seasons.setdefault(row["season"], []).append({"number": row["episode"]})
    show = {"ids": {"tmdb": tmdb_id}}
    if seasons:
        show["seasons"] = [{"number": season, "episodes": rows} for season, rows in sorted(seasons.items())]
    return {"shows": [show]}


async def _apply_reset(settings, provider, payload):
    tmdb_id = payload.get("tmdb_id")
    if not tmdb_id:
        raise ValueError("The deleted title has no TMDB identity")
    is_movie = payload.get("media_type") == "movie"
    episodes = payload.get("episodes") or []
    if provider == "trakt":
        cid, token = settings.trakt_client_id, settings.trakt_access_token
        if is_movie:
            await trakt.remove_movie_from_history(cid, token, tmdb_id)
            await trakt.remove_movie_rating(cid, token, tmdb_id)
            await trakt.remove_from_watchlist(cid, token, "movies", tmdb_id)
        else:
            for row in episodes:
                await trakt.remove_episode_from_history(cid, token, tmdb_id, row["season"], row["episode"])
            await trakt.remove_show_rating(cid, token, tmdb_id)
            await trakt.remove_from_watchlist(cid, token, "shows", tmdb_id)
            await trakt.remove_from_hidden(cid, token, "dropped", tmdb_id)
    elif provider == "simkl":
        cid, token = settings.simkl_client_id, settings.simkl_access_token
        if is_movie:
            await simkl.remove_movie_from_history(cid, token, tmdb_id)
            await simkl.remove_movie_rating(cid, token, tmdb_id)
        else:
            for row in episodes:
                await simkl.remove_episode_from_history(cid, token, tmdb_id, row["season"], row["episode"])
            await simkl.remove_show_rating(cid, token, tmdb_id)
    elif provider == "mdblist":
        body = _mdblist_payload(payload)
        await mdblist.remove_watched(settings.mdblist_api_key, body)
        await mdblist.remove_ratings(settings.mdblist_api_key, body)
        await mdblist.remove_watchlist(settings.mdblist_api_key, body)
        if not is_movie:
            await mdblist.remove_dropped(settings.mdblist_api_key, tmdb_id)
    else:
        raise ValueError("Unsupported cloud provider")


async def dispatch_cloud_actions(db, user_id):
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one_or_none()
    actions = (await db.execute(select(CloudAction).where(
        CloudAction.user_id == user_id, CloudAction.state == "pending"
    ).order_by(CloudAction.id).limit(25).with_for_update(skip_locked=True))).scalars().all()
    for action in actions:
        entry = (await db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == user_id, TrackedEntry.media_id == action.media_id))).scalar_one_or_none()
        marker = (await db.execute(select(TrackingDeletion).where(
            TrackingDeletion.user_id == user_id, TrackingDeletion.media_id == action.media_id))).scalar_one_or_none()
        if entry or not marker or action.action != "reset":
            action.state = "cancelled"
            action.payload = {}
            continue
        try:
            await require_cloud_reconciliation(db, user_id, action.provider)
        except HTTPException:
            continue
        action.attempts += 1
        try:
            if not settings:
                raise ValueError("Provider connection no longer exists")
            await _apply_reset(settings, action.provider, action.payload)
            action.state = "applied"
            action.last_error = None
            action.payload = {}
            marker.pending_connections = [item for item in marker.pending_connections if item != action.provider]
            if not marker.pending_connections:
                reviews = (await db.execute(select(SyncReview).where(
                    SyncReview.user_id == user_id, SyncReview.media_id == action.media_id,
                    SyncReview.kind == "outbound_pending"))).scalars()
                for review in reviews:
                    review.state = "confirmed"
        except Exception as error:
            action.last_error = type(error).__name__
    await db.commit()
