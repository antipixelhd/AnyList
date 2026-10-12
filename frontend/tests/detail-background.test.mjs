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
