import test from 'node:test';
import assert from 'node:assert/strict';
import {detailPeopleLoading} from '../src/lib/detail-people-loading.ts';

test('missing credits load immediately and do not poll for optional character artwork',async()=>{
  const nativeFetch=globalThis.fetch,controller=new AbortController(),calls=[],published=[];
  globalThis.fetch=async path=>{calls.push(path);return Response.json({people:[{kind:'actor'}],credits_status:'updated',character_status:{state:'pending',pending:true}});};
  try {
    const resource=detailPeopleLoading(7,controller.signal,data=>{published.push(data);return data.character_status;},()=>{},()=>false);
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
    const resource=detailPeopleLoading(7,controller.signal,data=>data.character_status,()=>{},()=>selected);
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
    const resource=detailPeopleLoading(7,controller.signal,data=>data.character_status,()=>{},()=>selected);
    const demand=resource.characters();
    if(reason==='navigation')controller.abort();else selected=false;
    release();await demand;assert.equal(calls.length,1);
  }finally{controller.abort();globalThis.fetch=nativeFetch;}
});
