"""Best-effort playback delivery to enabled scrobble providers.

Inbound handlers select the event and progress; this module owns provider gates,
credential refresh, and completion fallbacks. Failures must not fail local playback.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from core import bingebase, trakt_auth
from core import trakt as trakt_client, simkl as simkl_client, mdblist as mdblist_client
from models.base import MediaType
from models.media import Media
from models.show import Show
from models.users import UserSettings

# Completed Simkl stops must reach history even when live scrobbling fails.
_SIMKL_WATCHED_PROGRESS = 80.0


async def forward(
    settings: UserSettings | None,
    media: Media,
    action: str,
    progress_percent: float,
    db: AsyncSession | None = None,
) -> None:
    """Deliver in the existing provider order; Simkl has no pause action."""
    await trakt_scrobble(settings, media, action, progress_percent, db=db)
    await mdblist_scrobble(settings, media, action, progress_percent, db=db)
    if action != "pause":
        await simkl_scrobble(settings, media, action, progress_percent, db=db)
    await bingebase.scrobble(settings, media, action, progress_percent, db=db)


async def trakt_scrobble(
    settings: UserSettings | None,
    media: "Media",
    action: str,
    progress_percent: float,
    db: AsyncSession | None = None,
) -> None:
    """Forward a play/pause/stop event to Trakt's scrobble API. Errors are swallowed."""
    if not (settings and settings.trakt_scrobble and settings.trakt_access_token and settings.trakt_client_id):
        return

    from sqlalchemy import inspect as sa_inspect

    progress = min(100.0, round(progress_percent * 100, 1))

    # Refresh the token if needed before scrobbling - real-time scrobbles used
    # the stored token as-is and broke for a week at a time when it expired
    # (#326). Uses its own session so a refresh's commit can't touch the
    # webhook request's in-flight transaction.
    try:
        access_token = await trakt_auth.ensure_valid_trakt_token_for_user(settings.user_id)
    except Exception as exc:  # scrobbles are best-effort - never raise
        import logging
        logging.getLogger(__name__).warning("[Trakt scrobble] %s skipped: %s", action, exc)
        return

    try:
        if media.media_type == MediaType.movie:
            year: int | None = None
            if media.release_date:
                try:
                    year = int(str(media.release_date)[:4])
                except (ValueError, TypeError):
                    pass
            await trakt_client.scrobble_movie(
                settings.trakt_client_id, access_token,
                action=action,
                tmdb_id=media.tmdb_id,
                progress=progress,
                title=media.title,
                year=year,
            )
        elif media.media_type == MediaType.episode and media.season_number is not None and media.episode_number is not None:
            state = sa_inspect(media)
            if "show" in state.unloaded:
                show = await db.get(Show, media.show_id) if db and media.show_id else None
            else:
                show = media.show
            await trakt_client.scrobble_episode(
                settings.trakt_client_id, access_token,
                action=action,
                season_number=media.season_number,
                episode_number=media.episode_number,
                progress=progress,
                show_tmdb_id=show.tmdb_id if show else None,
                show_title=show.title if show else None,
                episode_tmdb_id=media.tmdb_id,
            )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[Trakt scrobble] %s failed: %s", action, exc)



async def simkl_scrobble(
    settings: UserSettings | None,
    media: "Media",
    action: str,
    progress_percent: float = 0.0,
    db: AsyncSession | None = None,
) -> None:
    """Send start/stop events; Simkl has no pause action.

    A failed completed stop falls back to history. Simkl can reject either
    endpoint when its season layout differs, so failed fallback is logged.
    """
    if not (settings and settings.simkl_scrobble and settings.simkl_access_token and settings.simkl_client_id):
        return

    from sqlalchemy import inspect as sa_inspect
    import logging
    log = logging.getLogger(__name__)

    progress = min(100.0, round(progress_percent * 100, 1))
    cid, token = settings.simkl_client_id, settings.simkl_access_token

    is_movie = media.media_type == MediaType.movie and media.tmdb_id
    is_episode = (
        media.media_type == MediaType.episode
        and media.season_number is not None
        and media.episode_number is not None
    )

    show = None
    if is_episode:
        try:
            unloaded = sa_inspect(media).unloaded
        except Exception:
            unloaded = ()
        if "show" in unloaded:
            show = await db.get(Show, media.show_id) if db and media.show_id else None
        else:
            show = getattr(media, "show", None)
        if not (show and show.tmdb_id):
            return
    elif not is_movie:
        return

    try:
        if is_movie:
            if action == "start":
                year: int | None = None
                if media.release_date:
                    try:
                        year = int(str(media.release_date)[:4])
                    except (ValueError, TypeError):
                        pass
                await simkl_client.checkin_movie(
                    cid, token, tmdb_id=media.tmdb_id, title=media.title, year=year, progress=progress,
                )
            elif action == "stop":
                await simkl_client.stop_scrobble_movie(cid, token, tmdb_id=media.tmdb_id, progress=progress)
        else:
            if action == "start":
                await simkl_client.checkin_episode(
                    cid, token,
                    show_tmdb_id=show.tmdb_id,
                    season_number=media.season_number,
                    episode_number=media.episode_number,
                    show_title=show.title,
                    progress=progress,
                )
            elif action == "stop":
                await simkl_client.stop_scrobble_episode(
                    cid, token,
                    show_tmdb_id=show.tmdb_id,
                    season_number=media.season_number,
                    episode_number=media.episode_number,
                    progress=progress,
                )
    except Exception as exc:
        if action == "stop" and progress >= _SIMKL_WATCHED_PROGRESS:
            try:
                if is_movie:
                    await simkl_client.add_movie_to_history(cid, token, media.tmdb_id)
                else:
                    await simkl_client.add_episode_to_history(
                        cid, token, show.tmdb_id, media.season_number, media.episode_number,
                    )
                log.info(
                    "[Simkl scrobble] stop call failed (%s) - recorded the watch via /sync/history instead", exc,
                )
            except Exception as fallback_exc:
                log.warning(
                    "[Simkl scrobble] stop failed (%s) and the /sync/history fallback also failed: %s",
                    exc, fallback_exc,
                )
        else:
            log.warning("[Simkl scrobble] %s failed: %s", action, exc)



async def mdblist_scrobble(
    settings: UserSettings | None,
    media: "Media",
    action: str,
    progress_percent: float,
    db: AsyncSession | None = None,
) -> None:
    """Forward a play/pause/stop event to MDBList's scrobble API. Errors are swallowed."""
    if not (settings and settings.mdblist_scrobble and settings.mdblist_api_key):
        return

    from sqlalchemy import inspect as sa_inspect

    progress = min(100.0, round(progress_percent * 100, 1))

    # MDBList's /scrobble/stop downgrades any sub-80% stop into a resumable "paused"
    # session (same threshold Trakt uses). Below our own "did they actually watch
    # anything" cutoff, also clear the session so a barely-started play doesn't leave
    # a phantom continue-watching entry.
    actions = [action]
    if action == "stop" and progress_percent <= 0.05:
        actions.append("clear")

    try:
        show = None
        if media.media_type == MediaType.episode and media.season_number is not None and media.episode_number is not None:
            state = sa_inspect(media)
            if "show" in state.unloaded:
                show = await db.get(Show, media.show_id) if db and media.show_id else None
            else:
                show = media.show

        for act in actions:
            act_progress = progress if act != "clear" else None
            if media.media_type == MediaType.movie and media.tmdb_id:
                await mdblist_client.scrobble_movie(
                    settings.mdblist_api_key,
                    action=act,
                    tmdb_id=media.tmdb_id,
                    progress=act_progress,
                )
            elif media.media_type == MediaType.episode and media.season_number is not None and media.episode_number is not None:
                if show and show.tmdb_id:
                    await mdblist_client.scrobble_episode(
                        settings.mdblist_api_key,
                        action=act,
                        show_tmdb_id=show.tmdb_id,
                        season_number=media.season_number,
                        episode_number=media.episode_number,
                        progress=act_progress,
                    )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[MDBList scrobble] %s failed: %s", action, exc)
