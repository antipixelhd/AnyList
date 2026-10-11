"""Quota-conscious catalog score enrichment."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import mdblist
from models import GlobalSettings, Media, UserSettings
from models.base import MediaType

CACHE_TTL = timedelta(hours=24)


async def effective_mdblist_key(db: AsyncSession, user_id: int | None) -> str | None:
    if user_id is not None:
        personal = (await db.execute(
            select(UserSettings.mdblist_api_key).where(UserSettings.user_id == user_id)
        )).scalar_one_or_none()
        if personal:
            return personal
    global_key = (await db.execute(
        select(GlobalSettings.mdblist_api_key).where(GlobalSettings.id == 1)
    )).scalar_one_or_none()
    return global_key or None


async def refresh_external_scores(
    db: AsyncSession,
    media: Media,
    user_id: int | None,
    *,
    force: bool = False,
    raise_errors: bool = False,
) -> bool:
    """Refresh a title at most daily; failures leave the prior cache intact."""
    if not media.tmdb_id or media.media_type not in (MediaType.movie, MediaType.series):
        return False
    now = datetime.utcnow()
    if not force and media.external_scores_updated_at and now - media.external_scores_updated_at < CACHE_TTL:
        return False
    key = await effective_mdblist_key(db, user_id)
    if not key:
        return False
    kind = "movie" if media.media_type == MediaType.movie else "show"
    values: dict[str, float | None] = {}
    for source, field in (
        ("imdb", "imdb_rating"),
        ("tomatoes", "rt_critic_score"),
        ("audience", "rt_audience_score"),
    ):
        try:
            values[field] = await mdblist.get_catalog_rating(key, kind, media.tmdb_id, source)
        except mdblist.MDBListAPIError:
            if raise_errors:
                raise
            return False
    for field, value in values.items():
        setattr(media, field, value)
    media.external_scores_updated_at = now
    await db.commit()
    return True


async def refresh_score_status(db, media, user_id):
    """Keep optional score failures/retries durable without delaying first render."""
    import uuid
    import hashlib
    from sqlalchemy.dialects.postgresql import insert
    from core import catalogue
    from models.catalogue import MetadataSnapshot
    now = datetime.utcnow()
    key=await effective_mdblist_key(db,user_id)
    if not media.tmdb_id or not key:
        return {'state': 'unavailable', 'retry_after': 0}
    if media.external_scores_updated_at and now-media.external_scores_updated_at < CACHE_TTL:
        return {'state': 'ready', 'retry_after': 0}
    token = uuid.uuid4().hex
    await catalogue.write_lock(db)
    key_id=hashlib.sha256(key.encode()).hexdigest()
    quota=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='mdblist',
        MetadataSnapshot.kind=='score_quota',MetadataSnapshot.external_id==key_id))
    if quota and quota.next_attempt_at and quota.next_attempt_at>now:
        await db.commit()
        return catalogue.background_status(quota,'deferred',now)
    await db.execute(insert(MetadataSnapshot).values(provider='mdblist',kind='scores',external_id=str(media.id)).on_conflict_do_nothing())
    row = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='mdblist',
        MetadataSnapshot.kind=='scores',MetadataSnapshot.external_id==str(media.id)).with_for_update())
    if catalogue.cached_refresh_status(row,now):
        await db.commit()
        return catalogue.background_status(row,'deferred',now)
    row.lease_token=token;row.lease_until=now+timedelta(minutes=5)
    await db.commit()
    try:
        await refresh_external_scores(db,media,user_id,raise_errors=True)
        await catalogue.write_lock(db)
        await db.refresh(row, with_for_update=True)
        if row.lease_token != token:
            await db.commit()
            return catalogue.background_status(row,'deferred')
        row.expires_at=now+CACHE_TTL;row.fetched_at=now;row.error_code=None;row.next_attempt_at=None;row.attempts=0
        state='ready'
    except mdblist.MDBListAPIError as error:
        await catalogue.write_lock(db)
        await db.refresh(row, with_for_update=True)
        if row.lease_token != token:
            await db.commit()
            return catalogue.background_status(row,'deferred')
        row.attempts+=1
        daily=isinstance(error,mdblist.MDBListDailyLimitError)
        row.error_code='daily_budget' if daily else 'unavailable'
        row.next_attempt_at=(now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)) if daily else now+timedelta(seconds=max(error.retry_after or 0,min(3600,60*2**min(row.attempts,6))))
        if daily or error.retry_after:
            await db.execute(insert(MetadataSnapshot).values(provider='mdblist',kind='score_quota',external_id=key_id,
                error_code=row.error_code,next_attempt_at=row.next_attempt_at).on_conflict_do_update(
                    index_elements=['provider','kind','external_id'],set_={
                        'error_code':row.error_code,'next_attempt_at':row.next_attempt_at}))
        state='deferred'
    except Exception:
        await db.rollback()
        await db.refresh(media)
        await catalogue.write_lock(db)
        await db.refresh(row, with_for_update=True)
        if row.lease_token != token:
            await db.commit()
            return catalogue.background_status(row,'deferred')
        row.attempts+=1;row.error_code='unavailable'
        row.next_attempt_at=now+timedelta(seconds=min(3600,60*2**min(row.attempts,6)))
        state='deferred'
    row.lease_token=None;row.lease_until=None
    await db.commit()
    return catalogue.background_status(row,state)
