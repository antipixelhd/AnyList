"""Shared Steam achievement catalogue; personal progress remains untouched."""
from datetime import timedelta

from sqlalchemy import case,select,update,cast,Text,all_
from sqlalchemy.dialects.postgresql import insert,ARRAY

from core import catalogue
from core.config import settings
from core.game_achievement_source import GameAchievementSource
from core.game_imdb_enrichment import now,request as admit,status as job_status
from core.game_ratings import GameRatings,app_id,run_scheduler
from core.game_tracking import achievement_metadata
from core.game_types import capabilities
from models.catalogue import MetadataSnapshot
from models.games import GameAchievement

JOB='game_achievement_job'
PREFIX='game_achievement_'


async def view(db,work,*,enqueue=False):
    app=await app_id(db,work) if capabilities(work.attributes).independent else None
    availability,total,definitions=achievement_metadata(work)
    previous=(work.attributes or {}).get('achievement_catalogue') or {}
    valid=bool(app and (not previous.get('steam_app_id') or previous['steam_app_id']==app))
    data={'work_id':work.id,'steam_app_id':app,'availability':availability if valid else 'unknown',
        'total':total if valid else None,'definitions_available':definitions if valid else False}
    if not app:return {'catalogue':data,'status':{'state':'unavailable','pending':False,'complete':False}}
    # Installing a key enables schema enrichment immediately, without waiting
    # for a previous keyless capability observation to expire.
    if enqueue and settings.steam_web_api_key and not previous.get('schema_enabled'):
        await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='steam',
            MetadataSnapshot.entity_id==work.id,MetadataSnapshot.kind.in_((JOB,PREFIX+'schema')),
            MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.next_attempt_at.is_(None))
            .values(expires_at=None).execution_options(synchronize_session=False))
    if enqueue and previous.get('steam_app_id') and previous['steam_app_id']!=app:
        await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='steam',
            MetadataSnapshot.entity_id==work.id,MetadataSnapshot.kind.in_((JOB,PREFIX+'globals',PREFIX+'schema')),
            MetadataSnapshot.lease_until.is_(None),MetadataSnapshot.next_attempt_at.is_(None))
            .values(expires_at=None).execution_options(synchronize_session=False))
    state=await (admit(db,work.id,kind=JOB,provider='steam') if enqueue else job_status(db,work.id,kind=JOB,provider='steam'))
    return {'catalogue':data,'status':state}


class GameAchievements(GameRatings):
    job=JOB
    parts=('globals','schema')
    component_prefix=PREFIX
    source_type=GameAchievementSource

    def __init__(self,factory,source=None):
        super().__init__(factory,source)
        self.parts=('globals','schema') if self.source.key else ('globals',)

    def retention(self,part):return timedelta(hours=6) if part=='globals' else timedelta(days=7)

    async def publish_component(self,db,work,app,part,value):
        if not capabilities(work.attributes).independent:raise catalogue.IdentityConflict('Achievements require an independent game')
        if 'attributes.achievement_catalogue' in (work.protected_fields or []):return
        if part=='schema' and value.get('available'):
            rows=value['definitions'];names=[row['api_name'] for row in rows]
            for offset in range(0,len(rows),200):
                batch=[{**row,'sort_order':offset+index,'work_id':work.id,'app_id':app,'active':True,'fetched_at':now()}
                    for index,row in enumerate(rows[offset:offset+200])]
                statement=insert(GameAchievement).values(batch)
                updated=await db.execute(statement.on_conflict_do_update(constraint='uq_game_achievement_identity',
                    set_={field:getattr(statement.excluded,field) for field in ('name','description','hidden','icon_url','locked_icon_url','sort_order','active','fetched_at')},
                    where=GameAchievement.work_id==work.id).returning(GameAchievement.id))
                if len(updated.all())!=len(batch):raise catalogue.IdentityConflict('Achievement belongs to a different game')
            await db.execute(update(GameAchievement).where(GameAchievement.work_id==work.id,
                GameAchievement.app_id==app,GameAchievement.api_name!=all_(cast(names,ARRAY(Text)))).values(active=False))
        records=list(await db.scalars(select(MetadataSnapshot).where(
            MetadataSnapshot.provider=='steam',MetadataSnapshot.entity_id==work.id,
            MetadataSnapshot.kind.in_((PREFIX+'globals',PREFIX+'schema')),
            MetadataSnapshot.coverage['steam_app_id'].astext==app)))
        components={row.kind.removeprefix(PREFIX):row.payload for row in records}
        components[part]=value
        global_data=components.get('globals') or {};schema=components.get('schema') or {}
        percentages=global_data.get('percentages') or {}
        names=list(percentages)
        for offset in range(0,len(names),500):
            batch={name:percentages[name] for name in names[offset:offset+500]}
            await db.execute(update(GameAchievement).where(GameAchievement.work_id==work.id,GameAchievement.app_id==app,
                GameAchievement.api_name.in_(batch)).values(global_percentage=case(batch,value=GameAchievement.api_name)))
        known_schema=schema.get('available') is True
        total=schema.get('total') if known_schema else global_data.get('total')
        metadata={'status':'none' if total==0 else 'complete' if known_schema else 'partial',
            'total':total,'definitions_complete':known_schema,'steam_app_id':app,'source':'steam',
            'schema_enabled':bool(settings.steam_web_api_key),'language':'english',
            'fetched_at':value['fetched_at'],'global_fetched_at':global_data.get('fetched_at'),
            'schema_fetched_at':schema.get('fetched_at') if known_schema else None,
            'definitions_stale':known_schema and any(row.kind==PREFIX+'schema' and (not row.expires_at or row.expires_at<=now()) for row in records),
            'total_source':'schema' if known_schema else 'global_percentages'}
        catalogue.merge_fields(work,{'attributes':{'achievement_catalogue':metadata}},'steam')


async def scheduler():
    from db import AsyncSessionLocal
    await run_scheduler(GameAchievements(AsyncSessionLocal))
