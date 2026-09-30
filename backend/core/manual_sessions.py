"""Completion of manual playback sessions after their duration expires."""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import watch_delivery
from core.rewatch import record_rewatch_progress
from core.watch_dedup import find_duplicate_watch_event, get_dedup_window_minutes
from models.events import WatchEvent
from models.media import Media
from models.playback_progress import PlaybackProgress
from models.playback_session import PlaybackSession


async def auto_complete_manual_sessions(db: AsyncSession) -> None:
    """Complete any manual sessions where enough time has elapsed since the last heartbeat."""
    now = datetime.utcnow()
    result = await db.execute(
        select(PlaybackSession, Media)
        .join(Media, Media.id == PlaybackSession.media_id)
        .where(PlaybackSession.source == "manual", PlaybackSession.state == "playing")
    )
    completed: list[tuple[int, int]] = []  # (user_id, media_id)
    new_events: list[WatchEvent] = []
    for session, media in result.all():
        runtime_seconds = (media.runtime or 0) * 60
        if runtime_seconds <= 0:
            continue
        elapsed = session.progress_seconds + (now - session.updated_at).total_seconds()
        if elapsed < runtime_seconds:
            continue
        await db.execute(delete(PlaybackSession).where(PlaybackSession.id == session.id))
        await db.execute(
            delete(PlaybackProgress).where(
                PlaybackProgress.user_id == session.user_id,
                PlaybackProgress.media_id == session.media_id,
            )
        )
        window_minutes = await get_dedup_window_minutes(db, session.user_id)
        if await find_duplicate_watch_event(db, session.user_id, session.media_id, now, window_minutes) is not None:
            continue
        event = WatchEvent(
            user_id=session.user_id,
            media_id=session.media_id,
            watched_at=now,
            completed=True,
            play_count=1,
            progress_percent=1.0,
        )
        db.add(event)
        new_events.append(event)
        completed.append((session.user_id, session.media_id))
    if completed:
        await db.commit()
        for event in new_events:
            await record_rewatch_progress(db, event.user_id, event.media_id, event.id)
        await db.commit()
        for user_id, media_id in completed:
            await watch_delivery.push_watch_state(db, user_id, [media_id], watched=True)
