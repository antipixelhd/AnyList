"""Best-effort Bingebase playback event delivery."""
from sqlalchemy.ext.asyncio import AsyncSession

from models.base import MediaType
from models.media import Media
from models.show import Show
from models.users import UserSettings


async def scrobble(
    settings: UserSettings | None,
    media: "Media",
    action: str,
    progress_percent: float,
    db: AsyncSession | None = None,
) -> None:
    """Forward a play/pause/stop event to Bingebase Webhook URL. Errors are swallowed."""
    if not (settings and settings.bingebase_scrobble and settings.bingebase_webhook_url):
        return

    try:
        import httpx
        from sqlalchemy import inspect as sa_inspect

        progress = min(100.0, round(progress_percent * 100, 1))
        event_name = "playback.stop" if action == "stop" else ("playback.pause" if action == "pause" else "playback.start")

        provider_ids = {}
        if media.tmdb_id:
            provider_ids["Tmdb"] = str(media.tmdb_id)
        if media.imdb_id:
            provider_ids["Imdb"] = media.imdb_id

        item_data = {
            "Name": media.title,
            "Type": "Episode" if media.media_type == MediaType.episode else "Movie",
            "ProviderIds": provider_ids,
        }

        if media.media_type == MediaType.episode:
            item_data["ParentIndexNumber"] = media.season_number
            item_data["IndexNumber"] = media.episode_number
            if media.show_id and db:
                state = sa_inspect(media)
                show = await db.get(Show, media.show_id) if "show" in state.unloaded else media.show
                if show:
                    item_data["SeriesName"] = show.title
                    if show.tmdb_id:
                        provider_ids["ShowTmdb"] = str(show.tmdb_id)

        payload = {
            "Event": event_name,
            "NotificationType": "PlaybackStop" if action == "stop" else "PlaybackStart",
            "Item": item_data,
            "Percentage": progress,
        }

        headers = {"User-Agent": "AnyList/1.0", "Content-Type": "application/json"}
        if settings.bingebase_api_key:
            headers["Authorization"] = f"Bearer {settings.bingebase_api_key}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(settings.bingebase_webhook_url, json=payload, headers=headers)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[Bingebase scrobble] %s failed: %s", action, exc)
