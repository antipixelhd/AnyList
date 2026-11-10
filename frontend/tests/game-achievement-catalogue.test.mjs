import test from 'node:test';
import assert from 'node:assert/strict';
import {watchAchievementCatalogue} from '../src/lib/game-achievement-catalogue.ts';

function fixture(fetcher){
  const original={fetch:globalThis.fetch,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout};
  const timers=[],applied=[],controller=new AbortController();
  globalThis.fetch=fetcher;
  globalThis.setTimeout=(callback,ms)=>{const timer={callback,ms};timers.push(timer);return timer;};
  globalThis.clearTimeout=timer=>{const index=timers.indexOf(timer);if(index>=0)timers.splice(index,1);};
  const events=new EventTarget();
  watchAchievementCatalogue(7,controller.signal,data=>applied.push(data),events);
  const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
  return {applied,timers,controller,settle,primary(id=7){events.dispatchEvent(new CustomEvent('anylist:game-primary',{detail:{type:'game',id}}));},async tick(){timers.shift()?.callback();await settle();},cleanup(){controller.abort();Object.assign(globalThis,original);}};
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

test('pending source errors respect cooldowns and healthy acquisition continues beyond twelve polls',async()=>{
  let calls=0;
  const f=fixture(async()=>{calls++;return Response.json({catalogue:{work_id:7,total:0,definitions_available:true},status:{pending:true,error:'unavailable',retry_after:900}});});
  try{await f.settle();assert.equal(f.applied[0].total,0);assert.equal(f.timers[0].ms,900000);f.primary();await f.settle();assert.equal(calls,1);}finally{f.cleanup();}
  calls=0;
  const pending=fixture(async()=>{calls++;return Response.json({catalogue:{work_id:7,total:calls===15?42:null,definitions_available:calls===15},status:{pending:calls<15}});});
  try{
    await pending.settle();for(let i=0;i<14;i++)await pending.tick();
    assert.equal(calls,15);assert.equal(pending.applied.at(-1).total,42);assert.equal(pending.timers.length,0);
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

test('transport and HTTP failures back off, respect Retry-After, and recover automatically',async()=>{
  let calls=0;
  const f=fixture(async()=>{
    calls++;
    if(calls===1)throw new Error('Offline');
    if(calls===2)return new Response('',{status:503});
    if(calls===3)return new Response('',{status:429,headers:{'Retry-After':'900'}});
    return Response.json({catalogue:{work_id:7,total:42},status:{pending:false}});
  });
  try{
    await f.settle();assert.equal(f.timers[0].ms,5000);
    await f.tick();assert.equal(f.timers[0].ms,10000);
    await f.tick();assert.equal(f.timers[0].ms,900000);
    await f.tick();assert.equal(f.applied.at(-1).total,42);assert.equal(f.timers.length,0);
  }finally{f.cleanup();}
});

test('primary publication restarts an unavailable catalogue and queues behind an older in-flight read',async()=>{
  let calls=0,finish;
  const f=fixture(()=>{calls++;return calls===1?new Promise(resolve=>{finish=resolve;}):Response.json({catalogue:{work_id:7,total:42},status:{pending:false}});});
  try{
    f.primary(8);assert.equal(calls,1);
    f.primary();f.primary();assert.equal(calls,1);
    finish(Response.json({catalogue:{work_id:7,total:null},status:{state:'unavailable',pending:false}}));
    await f.settle();assert.equal(calls,2);assert.equal(f.applied.at(-1).total,42);
    f.controller.abort();f.primary();await f.settle();assert.equal(calls,2);
  }finally{f.cleanup();}
  calls=0;
  const unavailable=fixture(()=>Response.json({catalogue:{work_id:7,total:++calls===1?null:42},status:{pending:false}}));
  try{
    await unavailable.settle();assert.equal(unavailable.timers.length,0);
    unavailable.primary();await unavailable.settle();assert.equal(unavailable.applied.at(-1).total,42);
  }finally{unavailable.cleanup();}
});

test('authorization failures stop and navigation cancels scheduled retries',async()=>{
  for(const status of [401,403]){
    let calls=0;const f=fixture(()=>{calls++;return new Response('',{status});});
    try{await f.settle();f.primary();await f.settle();assert.equal(calls,1);assert.equal(f.timers.length,0);}finally{f.cleanup();}
  }
  const f=fixture(()=>{throw new Error('Offline');});
  try{await f.settle();assert.equal(f.timers.length,1);f.controller.abort();assert.equal(f.timers.length,0);}finally{f.cleanup();}
});
