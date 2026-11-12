"""Shared on-demand PC requirements and explicitly identified age-rating boards."""
from datetime import timedelta

from sqlalchemy import or_,update

from core import catalogue
from core.game_ratings import GameRatings,app_id,run_scheduler
from core.game_store_source import GameStoreSource
from core.game_imdb_enrichment import now,request as admit,status as job_status
from core.game_types import capabilities
from models.catalogue import MetadataSnapshot

JOB='game_info_job'


def projection(work,app,identities):
    stored=(work.attributes or {}).get('game_info')
    data=dict(stored) if app and isinstance(stored,dict) and stored.get('steam_app_id')==app else {}
    # Explicit IGDB work identity and field provenance are required, never a
    # name match. IGDB remains primary; Steam fills missing named boards.
    if (work.field_sources or {}).get('attributes.age_ratings')=='igdb' and any(row.namespace=='igdb.game' for row in identities):
        rows=[row for row in (work.attributes or {}).get('age_ratings') or [] if isinstance(row,dict) and row.get('source')=='igdb']
        existing=data.get('age_ratings') or [];boards={row.get('board') for row in rows}
        data['age_ratings']=[*rows,*(row for row in existing if row.get('board') not in boards)]
    return {**data,'steam_app_id':app} if data else None


async def view(db,work,*,enqueue=False,identities=None):
    if not capabilities(work.attributes).searchable:return {'info':None,'status':{'state':'unavailable','pending':False,'complete':False}}
    if identities is None:
        from sqlalchemy import select
        from models.catalogue import CatalogueIdentity
        identities=list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id==work.id)))
    app=await app_id(db,work,identities);data=projection(work,app,identities)
    if not app:return {'info':data,'status':{'state':'ready' if data else 'unavailable','pending':False,'complete':bool(data)}}
    previous=(work.attributes or {}).get('game_info') or {}
    if enqueue and isinstance(previous,dict) and previous.get('steam_app_id') and previous['steam_app_id']!=app:
        await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='steam',MetadataSnapshot.kind==JOB,
            MetadataSnapshot.external_id==str(work.id),MetadataSnapshot.coverage['steam_app_id'].astext.is_distinct_from(app),
            MetadataSnapshot.next_attempt_at.is_(None),or_(MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.lease_until<=now()))
            .values(expires_at=None).execution_options(synchronize_session=False))
    state=await (admit(db,work.id,kind=JOB,provider='steam') if enqueue else job_status(db,work.id,kind=JOB,provider='steam'))
    return {'info':data,'status':state}


class GameInfo(GameRatings):
    job=JOB
    parts=('info',)
    component_prefix='game_store_'
    source_type=GameStoreSource

    def retention(self,part):return timedelta(days=7)

    def publish(self,work,app,part,value):
        catalogue.merge_fields(work,{'attributes':{'game_info':{**value,'steam_app_id':app}}},'steam')


async def scheduler():
    from db import AsyncSessionLocal
    await run_scheduler(GameInfo(AsyncSessionLocal))
