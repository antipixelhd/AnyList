"""Country-aware theatrical release evidence; premieres alone do not qualify."""
from datetime import date,datetime,timedelta,timezone
import uuid


def theatrical_dates(data, country=None):
    stored = ((data.get('theatrical_release_dates_by_country') or {}).get(country, [])
        if country and isinstance(data.get('theatrical_release_dates_by_country'),dict)
        else None if country else data.get('theatrical_release_dates'))
    if stored is None and not country and isinstance(data.get('theatrical_release_dates_by_country'), dict):
        stored = [value for dates in data['theatrical_release_dates_by_country'].values()
                  if isinstance(dates, list) for value in dates]
    if isinstance(stored,list):
        values=stored
    else:
        release=data.get('release_dates') or {}
        values=[r.get('release_date') for entry in release.get('results',[]) if isinstance(entry,dict)
            and (not country or entry.get('iso_3166_1')==country)
            for r in entry.get('release_dates',[]) if isinstance(r,dict) and type(r.get('type')) is int and r['type'] in (2,3)]
    dates=set()
    for value in values:
        if not isinstance(value,str):continue
        try:dates.add(date.fromisoformat(value[:10]).isoformat())
        except ValueError:continue
    return sorted(dates)


def theatrical_country_dates(data):
    releases=data.get('release_dates') or {}
    if isinstance(releases,dict) and isinstance(releases.get('results'),list):
        countries={entry.get('iso_3166_1') for entry in releases['results'] if isinstance(entry,dict)}
        return {country:theatrical_dates({'release_dates':releases},country) for country in sorted(
            value for value in countries if isinstance(value,str) and len(value)==2 and value.isupper())}
    return data.get('theatrical_release_dates_by_country')


def has_cinema_release(data, country=None):
    dates=theatrical_dates(data,country)
    return bool(dates and dates[0]<=date.today().isoformat())


async def backfill_cinema_releases(db,providers,*,limit=50):
    """Fill missing release evidence, reusing provider snapshots and durable retries."""
    from sqlalchemy import select,func,or_,String
    from sqlalchemy.dialects.postgresql import insert
    from models import Media
    from models.catalogue import MetadataSnapshot
    from core import catalogue,tmdb
    from core.catalogue_providers import ProviderError
    now=datetime.now(timezone.utc).replace(tzinfo=None)
    kind='cinema_releases'
    rows=list(await db.scalars(select(Media.id).outerjoin(MetadataSnapshot,
        (MetadataSnapshot.provider=='tmdb')&(MetadataSnapshot.kind==kind)&(MetadataSnapshot.external_id==Media.tmdb_id.cast(String)))
        .where(Media.media_type=='movie',Media.tmdb_id.is_not(None),
            or_(func.jsonb_typeof(Media.tmdb_data['theatrical_release_dates']).is_distinct_from('array'),
                func.jsonb_typeof(Media.tmdb_data['theatrical_release_dates_by_country']).is_distinct_from('object')),
            or_(MetadataSnapshot.next_attempt_at.is_(None),MetadataSnapshot.next_attempt_at<=now),
            or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now))
        .order_by(Media.id).limit(limit)))
    result={'examined':len(rows),'updated':0,'cinema':0,'failed':0}
    for media_id in rows:
        media=await db.get(Media,media_id,populate_existing=True)
        if media is None:continue
        now=datetime.now(timezone.utc).replace(tzinfo=None)
        native=str(media.tmdb_id);token=uuid.uuid4().hex
        await catalogue.write_lock(db)
        await db.execute(insert(MetadataSnapshot).values(provider='tmdb',kind=kind,external_id=native).on_conflict_do_nothing())
        row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='tmdb',
            MetadataSnapshot.kind==kind,MetadataSnapshot.external_id==native).with_for_update())
        if row.lease_until and row.lease_until>now or row.next_attempt_at and row.next_attempt_at>now:
            await db.commit();continue
        row.lease_token=token;row.lease_until=now+timedelta(minutes=5)
        await db.commit()
        try:
            raw=row.payload if row.expires_at and row.expires_at>now else None
            if raw is None:
                cached=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='tmdb',
                    MetadataSnapshot.kind=='movie',MetadataSnapshot.external_id==native))
                if cached and cached.expires_at and cached.expires_at>now:
                    raw=(cached.payload or {}).get('release_dates')
            if raw is None:
                if not providers.tmdb_key:raise ProviderError('not_configured',3600)
                raw=await providers.http.request('tmdb','GET',tmdb.TMDB_BASE+'/movie/'+native+'/release_dates',
                    headers=tmdb.get_headers(providers.tmdb_key))
            if not isinstance(raw,dict) or not isinstance(raw.get('results'),list):raise ProviderError('invalid_json')
            dates=theatrical_dates({'release_dates':raw})
            await catalogue.write_lock(db)
            await db.refresh(row,with_for_update=True)
            if row.lease_token!=token:await db.commit();continue
            await db.refresh(media,with_for_update=True)
            media.tmdb_data={**(media.tmdb_data or {}),'theatrical_release_dates':dates,
                'theatrical_release_dates_by_country':theatrical_country_dates({'release_dates':raw})}
            row.payload=raw;row.fetched_at=now;row.expires_at=now+timedelta(days=30)
            row.error_code=None;row.next_attempt_at=None;row.attempts=0
            result['updated']+=1;result['cinema']+=has_cinema_release(media.tmdb_data)
        except Exception as error:
            await db.rollback()
            _,owned=await catalogue.record_refresh_failure(db,row,token,error,60)
            if not owned:await db.commit();continue
            result['failed']+=1
        row.lease_token=None;row.lease_until=None
        await db.commit()
    return result
