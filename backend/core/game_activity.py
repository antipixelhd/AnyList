"""Public game activity is a projection of verified changes, never editor input."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, func

from core.catalogue_normalize import image
from models import User, UserProfileData, Follow
from models.base import PrivacyLevel
from models.catalogue import CatalogueEntity
from models.games import GameActivity, GameEntry, GameChildState, GameAchievement, GameAchievementUnlock

PUBLIC_FLAGS = ('status_changed', 'rating_changed', 'hours_changed', 'achievements_changed', 'added_to_list')
PUBLIC_FIELDS = (*PUBLIC_FLAGS, 'previous_score', 'rating_first', 'total_seconds', 'achievement_percentage')


async def snapshot(db, entity, row, *, achievements=False):
    from core.game_tracking import achievement_summary
    exact = 0
    if achievements and isinstance(row, GameEntry):
        exact = await db.scalar(select(func.count()).select_from(GameAchievementUnlock).join(
            GameAchievement,GameAchievement.id==GameAchievementUnlock.achievement_id).where(GameAchievement.active.is_(True),
            GameAchievementUnlock.user_id == row.user_id, GameAchievementUnlock.work_id == row.work_id))
    progress = achievement_summary(entity, row, exact)
    return dict(status=row.status if row else None, score=row.score if row else None,
        total_seconds=row.total_seconds if isinstance(row, GameEntry) else None,
        achievements=[progress.mode, progress.count, progress.percentage] if achievements else None,
        listed=isinstance(row, GameEntry) or bool(row and row.base_entry_id is not None))


async def record(db, row, before, after):
    if before == after or not after['listed']:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    activity = await db.scalar(select(GameActivity).where(
        GameActivity.user_id == row.user_id, GameActivity.work_id == row.work_id,
        GameActivity.created_at > now-timedelta(hours=24), GameActivity.created_at <= now,
        GameActivity.payload['window_started_at'].astext.is_not(None),
    ).order_by(GameActivity.created_at.desc(), GameActivity.id.desc()).limit(1))
    if activity:
        start = datetime.fromisoformat(activity.payload['window_started_at'])
        if not start <= now < start+timedelta(hours=24):
            activity = None
    details = dict(activity.payload) if activity else {'window_started_at': now.isoformat()}
    for field in ('status', 'score', 'total_seconds', 'achievements', 'listed'):
        if field == 'achievements' and after[field] is None:
            continue
        details.setdefault(f'{field}_start', before[field])
    details.update(status_changed=details['status_start'] != after['status'],
        rating_changed=details['score_start'] != after['score'],
        previous_score=details['score_start'], rating_first=details['score_start'] is None,
        hours_changed=details['total_seconds_start'] != after['total_seconds'],
        achievements_changed=details['achievements_start'] != after['achievements'] if after['achievements'] is not None else details.get('achievements_changed', False),
        added_to_list=not details['listed_start'] and after['listed'],
        total_seconds=after['total_seconds'])
    if after['achievements'] is not None:
        details['achievement_percentage'] = after['achievements'][2]
    if not any(details[flag] for flag in PUBLIC_FLAGS):
        if activity:
            await db.delete(activity)
        return
    if activity is None:
        activity = GameActivity(user_id=row.user_id, work_id=row.work_id)
        db.add(activity)
    activity.status, activity.score, activity.payload, activity.created_at = after['status'], after['score'], details, now


async def rows(db, user_ids, *, limit=60, work_id=None, public_only=False):
    query = (select(GameActivity, CatalogueEntity.id, CatalogueEntity.name, CatalogueEntity.image_url,
        User.username, UserProfileData.display_name, UserProfileData.avatar_path)
        .join(CatalogueEntity, CatalogueEntity.id == GameActivity.work_id)
        .join(User, User.id == GameActivity.user_id)
        .outerjoin(UserProfileData, UserProfileData.user_id == User.id)
        .outerjoin(GameEntry, and_(GameEntry.user_id == GameActivity.user_id, GameEntry.work_id == GameActivity.work_id))
        .outerjoin(GameChildState, and_(GameChildState.user_id == GameActivity.user_id, GameChildState.work_id == GameActivity.work_id))
        .where(GameActivity.user_id.in_(user_ids), or_(GameEntry.id.is_not(None), GameChildState.base_entry_id.is_not(None)),
            or_(*(GameActivity.payload[flag].as_boolean().is_(True) for flag in PUBLIC_FLAGS))))
    if public_only:
        query = query.where(UserProfileData.privacy_level == PrivacyLevel.public)
    if work_id is not None:
        query = query.where(GameActivity.work_id == work_id)
    result = []
    for activity, work_id, title, poster, username, display_name, avatar_path in (await db.execute(query.order_by(
        GameActivity.created_at.desc(), GameActivity.id.desc()).limit(limit))).all():
        details = activity.payload or {}
        result.append(dict(key=f'game:{activity.user_id}:{work_id}:{details.get("window_started_at", activity.id)}',
            user_id=activity.user_id, username=username, display_name=display_name or username,
            has_avatar=bool(avatar_path), status=activity.status, score=activity.score,
            payload={key: details[key] for key in PUBLIC_FIELDS if key in details}, created_at=activity.created_at,
            media=dict(id=work_id, type='game', title=title, poster=image(poster))))
    return result


def following_ids(viewer_id):
    return select(Follow.following_id).where(Follow.follower_id == viewer_id)


async def social_people(db, viewer_id, work_id):
    from sqlalchemy import union_all
    membership = union_all(
        select(GameEntry.user_id, GameEntry.status, GameEntry.score).where(GameEntry.work_id == work_id),
        select(GameChildState.user_id, GameChildState.status, GameChildState.score).where(
            GameChildState.work_id == work_id, GameChildState.base_entry_id.is_not(None)),
    ).subquery()
    visible = or_(User.id == viewer_id, and_(User.id.in_(following_ids(viewer_id)), UserProfileData.privacy_level == PrivacyLevel.public))
    data = (await db.execute(select(User.id, User.username, UserProfileData.display_name, UserProfileData.avatar_path, membership.c.status, membership.c.score)
        .join(membership, membership.c.user_id == User.id)
        .outerjoin(UserProfileData, UserProfileData.user_id == User.id).where(visible))).all()
    rank = {status: index for index, status in enumerate(('completed', 'playing', 'paused', 'dropped', 'planning'))}
    people = [dict(id=user_id, username=username, display_name=display_name or username,
        has_avatar=bool(avatar_path), status=status, score=score, self=user_id == viewer_id)
        for user_id, username, display_name, avatar_path, status, score in data]
    people.sort(key=lambda row: (rank.get(row['status'], 99), row['score'] is None, -(row['score'] or 0), not row['self'], row['display_name'].casefold()))
    return people
