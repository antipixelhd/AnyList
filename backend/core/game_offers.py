"""Country-scoped Steam offers backed by the existing shared ITAD price cache."""
import asyncio
import logging
import re
from datetime import datetime,timezone
from urllib.parse import urlsplit

from sqlalchemy import or_,select,update
from sqlalchemy.dialects.postgresql import insert

from core import catalogue
from core.config import settings
from core.catalogue_providers import ProviderError
from core.countries import country_code
from core.game_types import capabilities
from models import UserProfileData
from models.catalogue import CatalogueEntity,CatalogueIdentity,MetadataSnapshot,SteamPriceSnapshot

log=logging.getLogger(__name__)
_wake=asyncio.Event()


def notify_refresh(result):
    """Wake only after the caller committed admission; the durable queue owns work."""
    state=result.get('status') or {}
    if state.get('pending') and state.get('state')=='pending' and not state.get('error'):_wake.set()


def now():return datetime.now(timezone.utc).replace(tzinfo=None)


async def price_header(db,key):
    return (await db.execute(select(MetadataSnapshot.entity_id,MetadataSnapshot.expires_at,MetadataSnapshot.fetched_at,
        MetadataSnapshot.next_attempt_at,MetadataSnapshot.lease_until,MetadataSnapshot.error_code).where(
        MetadataSnapshot.provider=='itad',MetadataSnapshot.kind=='price',MetadataSnapshot.external_id==key))).one_or_none()


def status_view(row):
    if not row:return {'state':'unavailable','pending':False,'retry_after':0}
    active=bool(row.lease_until and row.lease_until>now());pending=active or bool(row.next_attempt_at)
    delay=max(1,int((row.next_attempt_at-now()).total_seconds())+1) if row.next_attempt_at else 1
    return {'state':'loading' if active else 'retry' if row.error_code and pending else 'pending' if pending else 'ready',
        'pending':pending,'retry_after':min(3600,delay),'error':row.error_code,'complete':bool(row.fetched_at) and not pending}


async def admit(db,key,work_id):
    row=await price_header(db,key);stamp=now()
    active=bool(row and row.lease_until and row.lease_until>stamp)
    if row and (active or row.entity_id==work_id and (row.next_attempt_at or row.expires_at and row.expires_at>stamp)):
        return status_view(row)
    statement=insert(MetadataSnapshot).values(provider='itad',kind='price',external_id=key,entity_id=work_id,next_attempt_at=stamp)
    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',
        set_={'next_attempt_at':stamp,'entity_id':work_id},where=(
            or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=stamp)
            & or_(MetadataSnapshot.entity_id.is_distinct_from(work_id),
                MetadataSnapshot.next_attempt_at.is_(None)&or_(MetadataSnapshot.expires_at.is_(None),MetadataSnapshot.expires_at<=stamp)))))
    return {'state':'pending','pending':True,'retry_after':1,'error':None,'complete':False}


async def country(db,user_id,hint=None):
    saved=await db.scalar(select(UserProfileData.country).where(UserProfileData.user_id==user_id)) if user_id else None
    account=country_code(saved) if isinstance(saved,str) else None
    detected=country_code(hint) if isinstance(hint,str) else None
    if hint is not None and not detected:raise ProviderError('invalid_request')
    return {'country':account or detected or 'US','source':'account' if account else 'device' if detected else 'fallback'}


def steam_identity(identities):
    values={row.external_id for row in identities if row.namespace=='steam.app' and re.fullmatch(r'[1-9][0-9]{0,18}',row.external_id)}
    return next(iter(values)) if len(values)==1 else None


def public_link(value):
    if not isinstance(value,str):return None
    try:
        parsed=urlsplit(value)
        return value if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:return None


async def view(db,work,user_id,*,hint=None,identities=None,enqueue=False):
    effective=await country(db,user_id,hint)
    base={'country':effective['country'],'country_source':effective['source'],'steam_app_id':None,'offers':[],
        'status':{'state':'unavailable','pending':False,'retry_after':0}}
    if work.kind!='game' or not capabilities(work.attributes).searchable:return base
    if identities is None:
        identities=list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id==work.id,
            CatalogueIdentity.namespace=='steam.app').limit(2)))
    app=steam_identity(identities)
    if not app:return base
    base['steam_app_id']=app
    # Never hydrate raw provider responses just to render one regional offer.
    rows=(await db.execute(select(SteamPriceSnapshot.steam_app_id,SteamPriceSnapshot.country,SteamPriceSnapshot.current,
        SteamPriceSnapshot.historical_low,SteamPriceSnapshot.historical_low_at,SteamPriceSnapshot.url,
        SteamPriceSnapshot.fetched_at).where(SteamPriceSnapshot.work_id==work.id,
            SteamPriceSnapshot.steam_app_id==app,SteamPriceSnapshot.country==effective['country']))).mappings()
    base['offers']=[{**dict(row),'url':public_link(row['url'])} for row in rows]
    key=f'{app}:{effective["country"]}'
    if enqueue and settings.itad_api_key and effective['source']!='fallback':
        state=await admit(db,key,work.id)
    elif settings.itad_api_key:
        state=status_view(await price_header(db,key))
    else:
        state={'state':'ready' if base['offers'] else 'unavailable','pending':False,'retry_after':0}
    base['status']=state
    return base


async def run_due(factory,*,client=None,limit=2):
    from core.catalogue_providers import CatalogueProviders,ProviderHTTP
    if client is None:
        if not settings.itad_api_key:return 0
        client=CatalogueProviders(ProviderHTTP(session_factory=factory))
    stamp=now()
    async with factory() as db:
        jobs=(await db.execute(select(MetadataSnapshot.external_id,MetadataSnapshot.entity_id).where(
            MetadataSnapshot.provider=='itad',MetadataSnapshot.kind=='price',MetadataSnapshot.next_attempt_at<=stamp,
            or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=stamp))
            .order_by(MetadataSnapshot.next_attempt_at,MetadataSnapshot.id).limit(limit))).all()
    async def refresh(key,work_id):
        match=re.fullmatch(r'([1-9][0-9]{0,18}):([A-Z]{2})',key)
        async with factory() as db:
            work=await db.get(CatalogueEntity,work_id) if work_id else None
            if not match or not country_code(match[2]) or not work or work.kind!='game' or not capabilities(work.attributes).searchable:
                await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='itad',
                    MetadataSnapshot.kind=='price',MetadataSnapshot.external_id==key).values(next_attempt_at=None,error_code='identity_conflict'))
                await db.commit();return 0
            # refresh_prices owns the existing durable app/country lease and
            # provider budget, also shared with explicit refresh requests.
            try:
                result=await catalogue.refresh_prices(db,client,work_id,match[1],match[2])
                return int(result['status']=='updated')
            except catalogue.IdentityConflict:
                await db.rollback()
                # Identity changed: retire admission, never invent a new app.
                await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='itad',
                    MetadataSnapshot.kind=='price',MetadataSnapshot.external_id==key).values(
                        next_attempt_at=None,error_code='identity_conflict'))
                await db.commit()
                return 0
    # One worker owns at most `limit` independent app/country leases. A slow
    # regional price must not hold up the other admitted price in the batch.
    async with asyncio.TaskGroup() as group:
        tasks=[group.create_task(refresh(key,work_id)) for key,work_id in jobs]
    return sum(task.result() for task in tasks)


async def scheduler():
    from db import AsyncSessionLocal
    while True:
        _wake.clear()
        completed=0
        try:completed=await run_due(AsyncSessionLocal)
        except asyncio.CancelledError:raise
        except Exception:log.warning('Steam offer refresh is temporarily unavailable')
        if completed>=2:continue
        try:await asyncio.wait_for(_wake.wait(),timeout=5)
        except TimeoutError:pass
