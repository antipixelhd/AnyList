import test from 'node:test';
import assert from 'node:assert/strict';
import {detailPeopleLoading,waitForDetailPortraits} from '../src/lib/detail-people-loading.ts';

test('a game connection polls its own progress without a second character request',async()=>{
  const originalFetch=globalThis.fetch,originalSet=globalThis.setTimeout,originalClear=globalThis.clearTimeout;
  const controller=new AbortController(),calls=[],timers=[];
  globalThis.setTimeout=(callback,ms)=>{const timer={callback,ms};timers.push(timer);return timer;};
  globalThis.clearTimeout=()=>{};
  globalThis.fetch=async path=>{
    calls.push(path);
    return Response.json({people:[{kind:'actor'}],character_status:calls.length===1
      ?{state:'loading',pending:true,retry_after:2}:{state:'ready',pending:false}});
  };
  try{
    const resource=detailPeopleLoading(7,controller.signal,data=>data.character_status,()=>{},
      {selected:()=>true,needed:()=>true,primaryReady:async()=>{},warm:()=>{},
        source:{people:'/api/proxy/tracking/games/works/7/credits'}});
    resource.start(true);await resource.characters();
    assert.deepEqual(calls,['/api/proxy/tracking/games/works/7/credits']);
    assert.equal(timers[0].ms,2000);
    timers.shift().callback();await resource.characters();
    assert.deepEqual(calls,Array(2).fill('/api/proxy/tracking/games/works/7/credits'));
    assert.equal(timers.length,0);
  }finally{
    controller.abort();globalThis.fetch=originalFetch;globalThis.setTimeout=originalSet;globalThis.clearTimeout=originalClear;
  }
});

test('missing credits load immediately and do not poll for optional character artwork',async()=>{
  const nativeFetch=globalThis.fetch,controller=new AbortController(),calls=[],published=[];
  globalThis.fetch=async path=>{calls.push(path);return Response.json({people:[{kind:'actor'}],credits_status:'updated',character_status:{state:'pending',pending:true}});};
  try {
    const resource=detailPeopleLoading(7,controller.signal,data=>{published.push(data);return data.character_status;},()=>{},{selected:()=>false,needed:()=>false,primaryReady:async()=>{},warm:()=>{}});
    resource.start(true);
    // Share the urgent request rather than issuing another read for a consumer.
    await resource.characters();
    assert.deepEqual(calls,['/api/proxy/tracking/title/7/people']);
    assert.equal(published.length,1);
  }finally{controller.abort();globalThis.fetch=nativeFetch;}
});

test('character selection shares in-flight credit repair, then enriches only on demand',async()=>{
  const nativeFetch=globalThis.fetch,controller=new AbortController(),calls=[];
  let release,selected=true;
  globalThis.fetch=path=>{
    calls.push(path);
    const data={people:[{kind:'actor'}],credits_status:'cached',character_status:{state:'ready'}};
    return path.includes('?')?Promise.resolve(Response.json(data)):new Promise(resolve=>release=()=>resolve(Response.json(data)));
  };
  try {
    const resource=detailPeopleLoading(7,controller.signal,data=>data.character_status,()=>{},{selected:()=>selected,needed:()=>true,primaryReady:async()=>{},warm:()=>{}});
    resource.start(true);
    const first=resource.characters(),second=resource.characters();
    assert.equal(calls.length,1);
    release();await Promise.all([first,second]);
    assert.deepEqual(calls,['/api/proxy/tracking/title/7/people','/api/proxy/tracking/title/7/people?characters=true']);
    await resource.characters();assert.equal(calls.length,2);
  }finally{controller.abort();globalThis.fetch=nativeFetch;}
});

for(const reason of ['navigation','actor selection'])test(`${reason} during credit repair cancels pending character demand`,async()=>{
  const nativeFetch=globalThis.fetch,controller=new AbortController(),calls=[];
  let release,selected=true;
  globalThis.fetch=path=>{calls.push(path);return new Promise(resolve=>release=()=>resolve(Response.json({people:[{kind:'actor'}],character_status:{state:'pending'}})));};
  try {
    const resource=detailPeopleLoading(7,controller.signal,data=>data.character_status,()=>{},{selected:()=>selected,needed:()=>true,primaryReady:async()=>{},warm:()=>{}});
    const demand=resource.characters();
    if(reason==='navigation')controller.abort();else selected=false;
    release();await demand;assert.equal(calls.length,1);
  }finally{controller.abort();globalThis.fetch=nativeFetch;}
});

test('secondary character enrichment starts after primary portraits settle without selection',async()=>{
  const nativeFetch=globalThis.fetch,controller=new AbortController(),calls=[];
  let release,warmed=0;
  const ready=new Promise(resolve=>release=resolve);
  globalThis.fetch=async path=>{calls.push(path);return Response.json({people:[{kind:'actor'}],credits_status:'cached',character_status:{state:path.includes('?')?'ready':'pending'}});};
  try {
    const resource=detailPeopleLoading(7,controller.signal,data=>data.character_status,()=>{},
      {selected:()=>false,needed:()=>true,primaryReady:()=>ready,warm:()=>warmed++});
    resource.start(false);
    await Promise.resolve();assert.equal(calls.length,0);assert.equal(warmed,0);
    release();await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(calls,['/api/proxy/tracking/title/7/people','/api/proxy/tracking/title/7/people?characters=true']);
    assert.equal(warmed,2);
  }finally{controller.abort();globalThis.fetch=nativeFetch;}
});

test('cached character art warms after portraits without another enrichment read',async()=>{
  const nativeFetch=globalThis.fetch,controller=new AbortController(),calls=[];
  let release,warmed=0;
  const ready=new Promise(resolve=>release=resolve);
  globalThis.fetch=async path=>{calls.push(path);return Response.json({people:[{kind:'actor'}],character_status:{state:'ready'}});};
  try {
    const resource=detailPeopleLoading(7,controller.signal,data=>data.character_status,()=>{},
      {selected:()=>false,needed:()=>false,primaryReady:()=>ready,warm:()=>warmed++});
    resource.start(false);assert.equal(warmed,0);
    release();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls.length,1);assert.equal(warmed,1);
  }finally{controller.abort();globalThis.fetch=nativeFetch;}
});

test('primary portrait wait settles independently on image success/failure and cleans up on navigation',async()=>{
  const controller=new AbortController(),first=new EventTarget(),second=new EventTarget();
  first.complete=false;second.complete=false;
  let done=false;
  const pending=waitForDetailPortraits([first,second],controller.signal).then(()=>done=true);
  first.dispatchEvent(new Event('load'));await Promise.resolve();assert.equal(done,false);
  second.dispatchEvent(new Event('error'));await pending;assert.equal(done,true);
  const aborted=new AbortController();
  const wait=waitForDetailPortraits([first],aborted.signal);aborted.abort();await wait;
});

test('a stalled portrait cannot leave secondary enrichment waiting forever',async()=>{
  const controller=new AbortController(),image=new EventTarget();image.complete=false;
  await waitForDetailPortraits([image],controller.signal,5);
  controller.abort();
});


test('usable cast does not prematurely finish pending primary metadata',async()=>{
  const nativeFetch=globalThis.fetch,controller=new AbortController(),states=[];
  let calls=0;
  globalThis.fetch=async()=>Response.json({people:[{kind:'actor'}],credits_status:'cached',character_status:{state:'ready'},
    primary_status:++calls===1?{state:'pending',pending:true,retry_after:60}:{state:'ready',pending:false}});
  try{
    const resource=detailPeopleLoading(7,controller.signal,data=>data.character_status,state=>states.push(state),
      {selected:()=>false,needed:()=>false,primaryReady:async()=>{},warm:()=>{}});
    await resource.characters();assert.equal(states.at(-1),'loading');
    await resource.retry();assert.equal(states.at(-1),'ready');assert.equal(calls,2);
  }finally{controller.abort();globalThis.fetch=nativeFetch;}
});
