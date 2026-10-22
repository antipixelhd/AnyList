"""Independent private Games generations; shared public metadata, no providers."""
import asyncio
import logging
import uuid
from datetime import datetime,timedelta,timezone

from sqlalchemy import or_,select,update,text,func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import defer

from core.game_statistics_facts import load_facts,build_statistics
from models.game_statistics import GameStatsState

CONTRACT=2
SECTIONS=('overview','genres','developers','publishers','actors','staff')
log=logging.getLogger(__name__)


def now():return datetime.now(timezone.utc).replace(tzinfo=None)


async def read(db,user_id,section='overview',*,sort='count',page=1,generation=None):
    if section not in SECTIONS:raise ValueError('Unknown Games statistics section')
    table=GameStatsState
    field={'count':'games','mean_score':'mean_score','time':'recorded_seconds'}.get(sort)
    if not field or type(page) is not int or not 1<=page<=5000:raise ValueError('Invalid Games statistics page')
    # Project only the requested section/page in PostgreSQL. Sorting must cover
    # the full stored collection; never hydrate a full contributor snapshot to
    # display its first 24 groups or sort a truncated browser subset.
    row=(await db.execute(text("""SELECT s.user_id,s.generation,s.source_revision,s.computed_source_revision,
        s.metadata_revision,s.contract_version,s.computed_at,s.lease_until,s.next_due_at,s.error_code,
        jsonb_typeof(s.payload)='object' AS cached,s.metadata_source_revision AS current_metadata,
        CASE WHEN :section='overview' THEN s.payload->'overview' ELSE
          (SELECT coalesce(jsonb_agg(p.value),'[]'::jsonb) FROM
            (SELECT value FROM jsonb_array_elements(CASE WHEN jsonb_typeof(s.payload->:section)='array'
              THEN s.payload->:section ELSE '[]'::jsonb END)
             ORDER BY (value->>:sort_field)::numeric DESC NULLS LAST,lower(value->>'label'),value->>'key'
             LIMIT 24 OFFSET :offset) p) END AS data,
        CASE WHEN jsonb_typeof(s.payload->:section)='array' THEN jsonb_array_length(s.payload->:section) ELSE 0 END AS total
        FROM users u LEFT JOIN game_stats_state s ON s.user_id=u.id WHERE u.id=:user_id"""),
        {'user_id':user_id,'section':section,'sort_field':field,'offset':(page-1)*24})).one_or_none()
    if row is None:raise RuntimeError('Statistics account is unavailable')
    ready=bool(row.user_id is not None and row.cached and row.data is not None and row.contract_version==CONTRACT
        and row.source_revision==row.computed_source_revision and row.metadata_revision==row.current_metadata)
    if ready and generation is not None and generation!=row.generation:raise GameStatisticsChanged()
    if row.user_id is None:
        await db.execute(insert(table).values(user_id=user_id).on_conflict_do_nothing())
    elif not ready:
        # Only invalidate the generation we observed. A concurrent successful
        # publication or tracking write owns its newer generation/revision.
        await db.execute(update(table).where(table.user_id==user_id,table.generation==row.generation,
            table.source_revision==row.source_revision).values(payload=None,
                next_due_at=now() if not row.error_code else table.next_due_at))
    await db.commit()
    return {'domain':'game','section':section,'status':'ready' if ready else 'error' if row.error_code else 'pending',
        'generation':row.generation if ready else None,'computed_at':row.computed_at.replace(tzinfo=timezone.utc) if row.computed_at and ready else None,
        'refreshing':bool(row.lease_until and row.lease_until>now()),'data':row.data if ready else None,
        'page':page,'total':row.total if ready and section!='overview' else None,
        'has_more':bool(ready and section!='overview' and page*24<row.total),
        'retry_after':max(3,min(60,int((row.next_due_at-now()).total_seconds())+1)) if row.next_due_at else 3}


class GameStatisticsChanged(ValueError):
    """A new generation requires restarting pagination."""


async def claim(factory):
    async with factory() as db,db.begin():
        found=(await db.execute(select(GameStatsState,(func.jsonb_typeof(GameStatsState.payload)=='object').label('cached')).options(defer(GameStatsState.payload)).where(
            GameStatsState.next_due_at<=now(),or_(GameStatsState.lease_until.is_(None),GameStatsState.lease_until<=now()))
            .order_by(GameStatsState.payload.is_(None).desc(),GameStatsState.next_due_at,GameStatsState.user_id).with_for_update(skip_locked=True).limit(1))).first()
        if not found:return None
        row,cached=found
        if cached and row.contract_version==CONTRACT and row.source_revision==row.computed_source_revision and row.metadata_source_revision==row.metadata_revision:
            row.next_due_at=now()+timedelta(hours=24,seconds=row.user_id%300);return {'skipped':True}
        token=str(uuid.uuid4());row.lease_token=token;row.lease_until=now()+timedelta(minutes=5)
        return {'user_id':row.user_id,'token':token}


async def compute(job,factory):
    async with factory() as db:
        await db.connection(execution_options={'isolation_level':'REPEATABLE READ'})
        row=await db.get(GameStatsState,job['user_id'],options=[defer(GameStatsState.payload)])
        if not row or row.lease_token!=job['token']:return None
        revision=row.metadata_source_revision
        source=row.source_revision
        facts,credits=await load_facts(db,row.user_id)
        payload=await asyncio.to_thread(build_statistics,facts,credits)
        return {'source_revision':source,'metadata_revision':revision,'payload':payload}


async def publish(job,result,factory):
    async with factory() as db,db.begin():
        row=await db.scalar(select(GameStatsState).options(defer(GameStatsState.payload)).where(
            GameStatsState.user_id==job['user_id']).with_for_update())
        if not row or row.lease_token!=job['token'] or row.lease_until<=now():return False
        revision=row.metadata_source_revision
        row.lease_token=row.lease_until=None
        if row.source_revision!=result['source_revision'] or revision!=result['metadata_revision']:
            row.next_due_at=now()+timedelta(seconds=1);return False
        row.payload=result['payload'];row.source_revision=result['source_revision']
        row.computed_source_revision=result['source_revision'];row.metadata_revision=revision
        row.generation+=1;row.contract_version=CONTRACT;row.computed_at=now()
        row.next_due_at=now()+timedelta(hours=24,seconds=row.user_id%300)
        row.attempts=0;row.error_code=None
        return True


async def release(job,factory,*,failed=False):
    async with factory() as db,db.begin():
        row=await db.scalar(select(GameStatsState).options(defer(GameStatsState.payload)).where(
            GameStatsState.user_id==job['user_id']).with_for_update())
        if row and row.lease_token==job['token']:
            row.lease_token=row.lease_until=None
            if failed:row.attempts+=1;row.error_code='calculation_failed'
            row.next_due_at=now()+timedelta(seconds=min(3600,30*2**min(row.attempts,6)) if failed else 1)


async def run_due(factory,limit=2):
    completed=0
    for _ in range(limit):
        job=await claim(factory)
        if not job:break
        if job.get('skipped'):continue
        try:
            result=await compute(job,factory)
            if result is not None and await publish(job,result,factory):completed+=1
        except asyncio.CancelledError:
            await release(job,factory);raise
        except Exception:
            log.warning('Game statistics calculation failed; source data retained')
            await release(job,factory,failed=True)
    return completed


async def scheduler():
    from db import AsyncSessionLocal
    while True:
        try:await run_due(AsyncSessionLocal)
        except asyncio.CancelledError:raise
        except Exception:log.warning('Game statistics queue is temporarily unavailable')
        await asyncio.sleep(3)
