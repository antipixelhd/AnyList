"""Import existing playback evidence into the independent tracked lists.

Library membership is deliberately not consulted. This does not infer removals.
"""
from datetime import date, datetime, timezone
from sqlalchemy import select, or_
from models import Media, WatchEvent, PlaybackProgress, Rating, Show, User
from models.base import MediaType
from models.tracking import TrackedEntry, TrackingDeletion
from core.tracking_rules import observed_status
from core.status_provenance import mark_status_change


async def import_tracking_history(db, user_id: int, added_media_ids: set[int] | None = None,
                                  *, initial_import: bool = False):
    await db.execute(select(User.id).where(User.id==user_id).with_for_update())
    deleted=set((await db.execute(select(TrackingDeletion.media_id).where(TrackingDeletion.user_id==user_id))).scalars())
    existing={e.media_id:e for e in (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id==user_id))).scalars()}
    watched=(await db.execute(select(WatchEvent,Media).join(Media,Media.id==WatchEvent.media_id).where(WatchEvent.user_id==user_id))).all()
    progress=(await db.execute(select(PlaybackProgress,Media).join(Media,Media.id==PlaybackProgress.media_id).where(PlaybackProgress.user_id==user_id))).all()
    ratings=(await db.execute(select(Rating,Media).join(Media,Media.id==Rating.media_id).where(Rating.user_id==user_id,Media.media_type.in_([MediaType.movie,MediaType.series]),Rating.episode_order.is_(None)))).all()
    show_ids={m.show_id for _,m in [*watched,*progress] if m.media_type==MediaType.episode and m.show_id}
    shows={s.id:s for s in (await db.execute(select(Show).where(Show.id.in_(show_ids)))).scalars()}
    series=(await db.execute(select(Media).where(Media.media_type==MediaType.series))).scalars().all()
    series_by_tmdb={m.tmdb_id:m for m in series if m.tmdb_id}
    series_by_tvdb={m.tvdb_id:m for m in series if m.tvdb_id}
    def parent(media):
        if media.media_type!=MediaType.episode:return media
        show=shows.get(media.show_id)
        if not show:return None
        return series_by_tmdb.get(show.tmdb_id) or series_by_tvdb.get(show.tvdb_id)
    evidence={}
    for event,media in watched:
        root=parent(media)
        if root is None:continue
        if root.id in deleted:
            await db.delete(event)
            continue
        item=evidence.setdefault(root.id,{'media':root,'watched':[],'playing':False,'dates':[],'scores':{}})
        if event.completed:item['watched'].append(media)
        else:item['playing']=True
        if event.watched_at:item['dates'].append(event.watched_at.date())
    for event,media in progress:
        root=parent(media)
        if root is None:continue
        if root.id in deleted:
            await db.delete(event)
            continue
        item=evidence.setdefault(root.id,{'media':root,'watched':[],'playing':False,'dates':[],'scores':{}})
        item['playing']=True
        # Observation timestamps are not historical watch dates on first import.
    for rating,media in ratings:
        if media.id in deleted:
            await db.delete(rating)
            continue
        item=evidence.setdefault(media.id,{'media':media,'watched':[],'playing':False,'dates':[],'scores':{}})
        if rating.rating and rating.rating>0:item['scores'][rating.season_number]=rating.rating
    added=0
    for media_id,item in evidence.items():
        # Existing manual choices are authoritative during this explicit import.
        if media_id in existing:continue
        media=item['media']; watched_items=item['watched']; count=len({m.id for m in watched_items})
        complete=media.media_type==MediaType.movie and count>0
        if media.media_type==MediaType.series and watched_items:
            show_id=next((m.show_id for m in watched_items if m.show_id),None)
            episodes=(await db.execute(select(Media).where(Media.show_id==show_id,Media.media_type==MediaType.episode,Media.season_number>0,Media.release_date.is_not(None),Media.release_date<=date.today().isoformat()).order_by(Media.season_number,Media.episode_number))).scalars().all() if show_id else []
            watched_ids={m.id for m in watched_items}
            last=max((i for i,m in enumerate(episodes) if m.id in watched_ids),default=-1)
            for episode in episodes[:last+1]:
                if episode.id not in watched_ids:db.add(WatchEvent(user_id=user_id,media_id=episode.id,completed=True,watched_at=None,provisional=True))
            count=last+1 if last>=0 else count
            complete=bool((media.tmdb_data or {}).get('tracking_catalogue_refreshed_at') and episodes and count==len(episodes))
        status=observed_status(None,complete,item['playing'] or count>0)
        entry=TrackedEntry(user_id=user_id,media_id=media_id,status=status,progress=count,
            manual_score=item['scores'].get(None),rating_mode='manual' if None in item['scores'] or not item['scores'] else 'average',
            season_scores={str(k):v for k,v in item['scores'].items() if k is not None},
            start_date=min(item['dates']) if item['dates'] and not (media.media_type==MediaType.movie and complete) else None,
            finish_date=max(item['dates']) if complete and item['dates'] else None)
        mark_status_change(entry,'history-import')
        if initial_import and status == 'completed':
            entry.initial_import_completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(entry)
        if added_media_ids is not None:
            added_media_ids.add(media_id)
        added+=1
    await db.commit()
    return added
