"""Stored Trakt token validation and refresh for routes and outbound delivery."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import trakt as trakt_client
from db import engine
from models.users import UserSettings

logger = logging.getLogger(__name__)
_TRAKT_TOKEN_REFRESH_SKEW = timedelta(days=1)


class TraktTokenError(Exception):
    """The stored Trakt token is unusable and can't be refreshed automatically -
    the user needs to reconnect Trakt in Settings. The message is safe to show."""


async def ensure_valid_trakt_token(
    db: AsyncSession, settings: UserSettings | None, *, force_check: bool = False
) -> str:
    """Validate stored credentials before use, refreshing and persisting near expiry.

    force_check always validates with Trakt, including tokens revoked before
    expiry. Unconnected or unrefreshable accounts raise TraktTokenError.
    """
    if not settings or not settings.trakt_access_token or not settings.trakt_client_id:
        raise TraktTokenError("Trakt is not connected.")

    now = int(datetime.now(timezone.utc).timestamp())
    expires_at = settings.trakt_token_expires_at
    # Comfortably before a known expiry: trust the token, skip the round trip.
    if not force_check and expires_at and expires_at - now > _TRAKT_TOKEN_REFRESH_SKEW.total_seconds():
        return settings.trakt_access_token

    if await trakt_client.validate_token(settings.trakt_client_id, settings.trakt_access_token):
        return settings.trakt_access_token

    if not (settings.trakt_refresh_token and settings.trakt_client_secret):
        raise TraktTokenError("Trakt token expired. Please reconnect Trakt in Settings.")

    try:
        token_data = await trakt_client.refresh_access_token(
            settings.trakt_client_id,
            settings.trakt_client_secret,
            settings.trakt_refresh_token,
        )
    except Exception as exc:
        logger.warning("Trakt token refresh failed for user %s: %s", settings.user_id, exc)
        raise TraktTokenError(f"Trakt token expired and the automatic refresh failed: {exc}") from exc

    settings.trakt_access_token = token_data["access_token"]
    settings.trakt_refresh_token = token_data["refresh_token"]
    settings.trakt_token_expires_at = token_data.get("expires_in", 0) + int(
        datetime.now(timezone.utc).timestamp()
    )
    await db.commit()
    logger.info("Refreshed Trakt access token for user %s", settings.user_id)
    return settings.trakt_access_token


async def ensure_valid_trakt_token_for_user(user_id: int) -> str:
    """Resolve a token in its own session for concurrent outbound delivery.

    Refreshing must not commit an unrelated caller's request transaction.
    """
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as db:
        settings = (
            await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
        ).scalar_one_or_none()
        return await ensure_valid_trakt_token(db, settings)
