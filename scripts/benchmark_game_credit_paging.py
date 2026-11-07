"""Compare complete credit ingestion with legacy and adaptive foreground paging.

Uses the guarded disposable catalogue fixture; never a preview/production DB.
Provider delay is controlled, not a claim about live network or browser latency.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import AsyncMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]

from test_game_imdb_enrichment_pg import IMDbEnrichmentTests,actor,page
from core.game_imdb_enrichment import JOB
from core.game_work import demand,job_key,work_context,Priority
from sqlalchemy import event,select
from models.games import GameEntry


async def measure(rows,adaptive,delay):
    fixture=IMDbEnrichmentTests();await fixture.asyncSetUp()
    sql={'reads':0,'writes':0};sizes=[];publications=[]
    def counted(conn,cursor,statement,parameters,context,many):
        sql['reads' if statement.lstrip().startswith('SELECT') else 'writes']+=1
    async def credits(native,cursor,*,page_size=100):
        sizes.append(page_size);await asyncio.sleep(delay/1000)
        offset=int(cursor or 0);end=min(offset+page_size,len(rows))
        return page(rows[offset:end],len(rows),end<len(rows),str(end) if end<len(rows) else None)
    original=fixture.service.publish
    async def published(*args):
        start=perf_counter();result=await original(*args)
        publications.append(round((perf_counter()-start)*1000,2));return result
    fixture.service.publish=published;fixture.source.credits=credits;fixture.service.page_budget=1
    try:
        async with fixture.Session() as db:
            with work_context(Priority.DIRECT):await demand(db,job_key('imdb',JOB,fixture.work_id))
            await db.commit()
        event.listen(fixture.engine.sync_engine,'before_cursor_execute',counted)
        start=perf_counter()
        # This reference changes only sizing in this benchmark instance.
        with patch.object(fixture.service,'page_size',wraps=fixture.service.page_size) if adaptive else \
                patch.object(fixture.service,'page_size',AsyncMock(return_value=100)):
            for _ in range(len(rows)+1):
                state=await fixture.service.run(fixture.work_id)
                if state=='ready':break
                if state!='partial':raise RuntimeError(f'Acquisition failed: {state}')
        if state!='ready':raise RuntimeError('Incomplete acquisition')
        elapsed=round((perf_counter()-start)*1000,2)
        records=await fixture.active()
        fingerprint=hashlib.sha256(json.dumps([(r.source_key,r.role,r.position,r.character_label)
            for r in records],ensure_ascii=False).encode()).hexdigest()
        async with fixture.Session() as db:
            saved=await db.scalar(select(GameEntry).where(GameEntry.work_id==fixture.work_id))
            assert saved.notes=='Private draft' and saved.total_seconds==12345
        before=len(sizes);assert await fixture.service.run(fixture.work_id)=='cached'
        assert len(sizes)==before
        return {'adaptive':adaptive,'provider_calls':len(sizes),'page_sizes':sizes,'ms':elapsed,
            'sql':sql,'publication_ms':publications,'credits':len(records),'fingerprint':fingerprint,
            'warm_provider_calls':len(sizes)-before}
    finally:
        event.remove(fixture.engine.sync_engine,'before_cursor_execute',counted)
        await fixture.asyncTearDown();fixture.doCleanups()


async def main(args):
    if args.input:
        pages=json.loads(args.input.read_text(encoding='utf-8'))
        rows=[row for stored in pages for row in stored['rows']]
    else:
        rows=[actor(f'nm{n+1}') for n in range(1041)]
        rows[100]=rows[0]  # Repeated evidence across the old page boundary.
    results=[await measure(rows,adaptive,args.provider_delay_ms) for adaptive in (False,True)]
    assert results[0]['fingerprint']==results[1]['fingerprint']
    assert results[0]['credits']==results[1]['credits']==len(rows)
    args.output.write_text(json.dumps({'environment':'controlled provider + disposable PostgreSQL',
        'provider_delay_ms':args.provider_delay_ms,'results':results},indent=2),encoding='utf-8')
    for result in results:print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider-delay-ms',type=float,default=50)
    parser.add_argument('--input',type=Path,help='Optional exported public source-page array')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 0<=args.provider_delay_ms<=5000:parser.error('Delay must be between 0 and 5000ms')
    asyncio.run(main(args))
