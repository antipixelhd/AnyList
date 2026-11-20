import test from 'node:test';
import assert from 'node:assert/strict';
import {detailBackground} from '../src/lib/detail-background.ts';

function clock() {
  const nativeSet=globalThis.setTimeout,nativeClear=globalThis.clearTimeout;
  const tasks=new Map();let id=0;
  globalThis.setTimeout=(callback,delay)=>{tasks.set(++id,{callback,delay});return id;};
  globalThis.clearTimeout=id=>tasks.delete(id);
  return {tasks,restore(){globalThis.setTimeout=nativeSet;globalThis.clearTimeout=nativeClear;}};
}

test('revision subscriptions apply partial data then wait immediately with one request',async()=>{
  const timer=clock(),nativeFetch=globalThis.fetch,controller=new AbortController(),calls=[],applied=[];
  let release;
  globalThis.fetch=async path=>{
    calls.push(path);
    if(calls.length===1)return Response.json({state:'partial',pending:true,credit_updates:{revision:'first',waitable:true}});
    return await new Promise(resolve=>release=()=>resolve(Response.json({state:'ready',credit_updates:{revision:'next',waitable:false}})));
  };
  try{
    const resource=detailBackground('/game/credits?mode=actors',controller.signal,row=>{applied.push(row.state);return row;},()=>{},row=>row.credit_updates);
    await resource.load();assert.deepEqual(applied,['partial']);
    const [id,task]=[...timer.tasks.entries()][0];timer.tasks.delete(id);assert.equal(task.delay,0);
    task.callback();const wait=resource.load();assert.equal(resource.load(),wait);
    assert.deepEqual(calls,['/game/credits?mode=actors','/game/credits?mode=actors&revision=first']);
    release();await wait;assert.deepEqual(applied,['partial','ready']);assert.equal(timer.tasks.size,0);
  }finally{controller.abort();globalThis.fetch=nativeFetch;timer.restore();}
});

test('revision fallback and errors retain retry cooldowns',async()=>{
  const timer=clock(),nativeFetch=globalThis.fetch,controller=new AbortController();
  globalThis.fetch=async()=>Response.json({pending:true,error:'unavailable',retry_after:60,credit_updates:{revision:'same',waitable:true}});
  try{
    const resource=detailBackground('/game/credits',controller.signal,row=>row,()=>{},row=>row.credit_updates);
    await resource.load();assert.equal([...timer.tasks.values()][0].delay,60000);
  }finally{controller.abort();globalThis.fetch=nativeFetch;timer.restore();}
});

test('automatic retries retain the error state until usable data arrives',async()=>{
  const timer=clock(),nativeFetch=globalThis.fetch,controller=new AbortController(),states=[];
  let release,calls=0;
  globalThis.fetch=async()=>++calls===1?new Response('',{status:503}):
    await new Promise(resolve=>release=()=>resolve(Response.json({state:'ready'})));
  try{
    const resource=detailBackground('/fixture',controller.signal,row=>row,state=>states.push(state));
    await resource.load();assert.deepEqual(states,['loading','retry']);
    const next=resource.load();assert.deepEqual(states,['loading','retry']);
    release();await next;assert.equal(states.at(-1),'ready');
  }finally{controller.abort();globalThis.fetch=nativeFetch;timer.restore();}
});

test('background details start after paint without an extra timer and cancel on navigation',async()=>{
  const timer=clock(),nativeFetch=globalThis.fetch,nativeFrame=globalThis.requestAnimationFrame;
  const frames=[];let calls=0;
  globalThis.requestAnimationFrame=callback=>frames.push(callback);
  globalThis.fetch=async()=>{calls++;return Response.json({state:'ready'});};
  try{
    const controller=new AbortController();
    const resource=detailBackground('/fixture',controller.signal,row=>row,()=>{});
    resource.start();assert.equal(calls,0);
    frames.shift()();assert.equal(calls,0);frames.shift()();await resource.load();
    assert.equal(calls,1);assert.equal(timer.tasks.size,0);
    const cancelled=new AbortController();
    detailBackground('/cancelled',cancelled.signal,row=>row,()=>{}).start();
    cancelled.abort();frames.shift()();frames.shift()();assert.equal(calls,1);
  }finally{globalThis.fetch=nativeFetch;globalThis.requestAnimationFrame=nativeFrame;timer.restore();}
});

test('rate-limit cooldown is honored, automatically retried, and navigation cancels waiting',async()=>{
  const timer=clock(),nativeFetch=globalThis.fetch,signal=new AbortController();
  const statuses=[],data=[];let calls=0;
  globalThis.fetch=async()=>++calls===1?new Response('',{status:429,headers:{'Retry-After':'60'}}):Response.json({state:'ready',posters:['cached']});
  try {
    const resource=detailBackground('/fixture',signal.signal,row=>{data.push(row);return row;},(state)=>statuses.push(state));
    await resource.load();
    assert.deepEqual(statuses,['loading','retry']);assert.equal(data.length,0);
    const [id,task]=[...timer.tasks.entries()][0];assert.equal(task.delay,60000);timer.tasks.delete(id);
    task.callback();await resource.load();
    assert.equal(calls,2);assert.equal(statuses.at(-1),'ready');assert.equal(data[0].posters[0],'cached');
    assert.equal(timer.tasks.size,0);
    await resource.retry();signal.abort();assert.equal(timer.tasks.size,0);
  }finally{globalThis.fetch=nativeFetch;timer.restore();}
});

test('partial data is published before another batch, with one request in flight',async()=>{
  const timer=clock(),nativeFetch=globalThis.fetch,signal=new AbortController();
  let release,calls=0;const received=[];
  globalThis.fetch=()=>{calls++;return new Promise(resolve=>release=()=>resolve(Response.json({state:'partial',pending:true,retry_after:20,images:3})));};
  try {
    const resource=detailBackground('/fixture',signal.signal,row=>{received.push(row.images);return row;},()=>{});
    const first=resource.load();assert.equal(resource.load(),first);assert.equal(calls,1);
    release();await first;assert.deepEqual(received,[3]);assert.equal([...timer.tasks.values()][0].delay,20000);
    signal.abort();assert.equal(timer.tasks.size,0);
  }finally{globalThis.fetch=nativeFetch;timer.restore();}
});

test('a primary identity update waits for a stale optional read before retrying it',async()=>{
  const nativeFetch=globalThis.fetch,controller=new AbortController();let release,calls=0;
  globalThis.fetch=()=>{calls++;return calls===1?new Promise(resolve=>release=()=>resolve(Response.json({state:'unavailable'}))):Promise.resolve(Response.json({state:'ready'}));};
  try{
    const resource=detailBackground('/fixture',controller.signal,row=>row,()=>{});
    const first=resource.load(),refresh=resource.retry(true);assert.equal(calls,1);
    release();await first;await refresh;assert.equal(calls,2);
  }finally{controller.abort();globalThis.fetch=nativeFetch;}
});

test('async publication shares its request and navigation suppresses late ready state',async()=>{
  const nativeFetch=globalThis.fetch,controller=new AbortController(),statuses=[];
  let release,entered;const applying=new Promise(resolve=>entered=resolve);
  globalThis.fetch=async()=>Response.json({state:'ready'});
  try{
    const resource=detailBackground('/fixture',controller.signal,async()=>{
      entered();await new Promise(resolve=>release=resolve);return {state:'ready'};
    },state=>statuses.push(state));
    const first=resource.load();await applying;assert.equal(resource.load(),first);
    controller.abort();release();await first;
    assert.deepEqual(statuses,['loading']);
  }finally{controller.abort();globalThis.fetch=nativeFetch;}
});
