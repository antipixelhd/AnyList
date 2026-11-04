"""Account-owned, transactional erasure of a media category's private facts."""
from fastapi import HTTPException
from sqlalchemy import delete, select, update

from models import (
    Base, Comment, GameActivity, GameChildState, GameEntry, GameFavorite,
    GameStatsState, List, ListItem, Media, MediaRequest, MediaType,
    SteamConnection, SteamPlayerGame, SyncJob, SyncStatus, TrackingPreferences,
    User, UserProfileData, UserStatsSnapshot, UserStatsState,
)


async def delete_list_data(db, user_id: int, category: str) -> None:
    # Same account boundary as game edits/import publication. Refuse active
    # syncs rather than letting their already-fetched observations undo erasure.
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    active = await db.scalar(select(SyncJob.id).where(
        SyncJob.user_id == user_id,
        SyncJob.status.in_((SyncStatus.pending, SyncStatus.running)),
    ).limit(1))
    if active is not None:
        raise HTTPException(409, 'Wait for account syncs and imports to finish before deleting a list.')

    if category == 'games':
        for model in (GameChildState, GameEntry, GameFavorite, GameActivity, SteamPlayerGame, GameStatsState):
            await db.execute(delete(model).where(model.user_id == user_id))
        # Unlocks cascade from entries. Retain credentials, but invalidate old
        # picker drafts and remove all imported selections/checkpoints.
        await db.execute(update(SteamConnection).where(SteamConnection.user_id == user_id).values(
            library=[], selection=[], checkpoints={}, draft={}, known_apps=[],
            imported=False, auto_add_games=False, generation=SteamConnection.generation + 1,
            fetched_at=None,
        ))
        await db.execute(update(UserProfileData).where(UserProfileData.user_id == user_id).values(game_banners={}))
        return

    kinds = (MediaType.movie,) if category == 'movies' else (MediaType.series, MediaType.episode)
    media_ids = select(Media.id).where(Media.media_type.in_(kinds))
    # Explicit private-data manifest: never delete shared Media/catalogue facts.
    for name in (
        'tracking_watch_intents', 'tracking_stream_actions', 'tracking_cloud_actions',
        'tracking_delivery_jobs', 'tracking_reviews', 'tracking_deletions',
        'tracking_provider_matches', 'tracking_season_release_observations',
        'streaming_library_intents', 'plex_pending_pushes', 'playback_sessions',
        'playback_progress', 'collections', 'ratings', 'watch_events',
        'media_favorites', 'tracking_activity', 'tracked_entries',
    ):
        table = Base.metadata.tables[name]
        await db.execute(delete(table).where(table.c.user_id == user_id, table.c.media_id.in_(media_ids)))
    await db.execute(delete(ListItem).where(
        ListItem.list_id.in_(select(List.id).where(List.user_id == user_id)),
        ListItem.media_id.in_(media_ids),
    ))
    comment_types = ('movie',) if category == 'movies' else ('series', 'season', 'episode')
    await db.execute(delete(Comment).where(Comment.user_id == user_id, Comment.media_type.in_(comment_types)))
    await db.execute(delete(MediaRequest).where(
        MediaRequest.user_id == user_id, MediaRequest.media_type == kinds[0].value))
    if category == 'series':
        for name in ('show_rewatches', 'show_season_overrides', 'user_show_episode_orders'):
            table = Base.metadata.tables[name]
            await db.execute(delete(table).where(table.c.user_id == user_id))
    prefs = await db.get(TrackingPreferences, user_id)
    if prefs:
        removed = set(await db.scalars(media_ids.where(Media.id.in_(prefs.favorite_order or []))))
        prefs.favorite_order = [key for key in (prefs.favorite_order or []) if key not in removed]
    # These mixed-category records are derived/private import snapshots. Drop
    # them as a whole so they cannot retain erased history in JSON payloads.
    for name in ('tracking_baselines', 'tracking_cloud_baselines', 'netflix_import_sessions', 'user_calendar_cache'):
        table = Base.metadata.tables[name]
        await db.execute(delete(table).where(table.c.user_id == user_id))
    await db.execute(delete(UserStatsState).where(UserStatsState.user_id == user_id))
    await db.execute(delete(UserStatsSnapshot).where(UserStatsSnapshot.user_id == user_id))
