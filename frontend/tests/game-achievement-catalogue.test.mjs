import test from 'node:test';
import assert from 'node:assert/strict';
import {watchAchievementCatalogue} from '../src/lib/game-achievement-catalogue.ts';

function fixture(fetcher){
  const original={fetch:globalThis.fetch,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout};
  const timers=[],applied=[],controller=new AbortController();
  globalThis.fetch=fetcher;
  globalThis.setTimeout=(callback,ms)=>{const timer={callback,ms};timers.push(timer);return timer;};
  globalThis.clearTimeout=timer=>{const index=timers.indexOf(timer);if(index>=0)timers.splice(index,1);};
  watchAchievementCatalogue(7,controller.signal,data=>applied.push(data));
  const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
  return {applied,timers,controller,settle,async tick(){timers.shift()?.callback();await settle();},cleanup(){controller.abort();Object.assign(globalThis,original);}};
}

test('catalogue capabilities arrive independently while the personal draft stays owned by the editor',async()=>{
  let calls=0;
  const f=fixture(async()=>Response.json({catalogue:{work_id:7,total:++calls===1?null:42,definitions_available:false},status:{pending:calls===1,retry_after:2}}));
  try{
    await f.settle();assert.equal(f.applied[0].total,null);assert.equal(f.timers[0].ms,3000);
    await f.tick();assert.equal(f.applied[1].total,42);assert.equal(f.timers.length,0);
    assert(f.applied.every(data=>!('notes' in data)&&!('achievement_mode' in data)&&!('status' in data)));
  }finally{f.cleanup();}
});

test('empty catalogue is zero, failed source is not retried inside the editor, and pending polling is bounded',async()=>{
  let calls=0;
  const f=fixture(async()=>{calls++;return Response.json({catalogue:{work_id:7,total:0,definitions_available:true},status:{pending:true,error:'unavailable'}});});
  try{await f.settle();assert.equal(f.applied[0].total,0);assert.equal(f.timers.length,0);assert.equal(calls,1);}finally{f.cleanup();}
  calls=0;
  const pending=fixture(async()=>{calls++;return Response.json({catalogue:{work_id:7,total:null,definitions_available:false},status:{pending:true}});});
  try{
    await pending.settle();for(let i=0;i<12;i++)await pending.tick();
    assert.equal(calls,12);assert.equal(pending.timers.length,0);
  }finally{pending.cleanup();}
});

test('wrong-work and late aborted responses cannot alter a new editor',async()=>{
  const f=fixture(async()=>Response.json({catalogue:{work_id:8,total:99},status:{pending:false}}));
  try{await f.settle();assert.equal(f.applied.length,0);}finally{f.cleanup();}
  let finish;
  const late=fixture(()=>new Promise(resolve=>{finish=resolve;}));
  try{
    late.controller.abort();finish(Response.json({catalogue:{work_id:7,total:42},status:{pending:false}}));await late.settle();
    assert.equal(late.applied.length,0);assert.equal(late.timers.length,0);
  }finally{late.cleanup();}
});
