import test from 'node:test';
import assert from 'node:assert/strict';
import {contributorSourceRefresh} from '../src/lib/contributor-source-refresh.ts';

async function fixture(run){
  const original={fetch:globalThis.fetch,set:globalThis.setTimeout,clear:globalThis.clearTimeout};
  const timers=[],calls=[],paint=[],controller=new AbortController();
  let reply={id:'nm1',pending:false,complete:true,fetched_at:'new'};
  globalThis.setTimeout=(callback,delay)=>{const timer={callback,delay};timers.push(timer);return timer;};
  globalThis.clearTimeout=timer=>{const index=timers.indexOf(timer);if(index>=0)timers.splice(index,1);};
  globalThis.fetch=async(path,options)=>{calls.push({path,options});return Response.json(reply);};
  const resource=contributorSourceRefresh(controller.signal,value=>paint.push(value));
  const tick=async()=>{timers.shift()?.callback();await new Promise(resolve=>setImmediate(resolve));};
  try{await run({resource,timers,calls,paint,controller,tick,reply:value=>reply=value});}
  finally{controller.abort();globalThis.fetch=original.fetch;globalThis.setTimeout=original.set;globalThis.clearTimeout=original.clear;}
}
test('game people use small private status polls and offer refresh without replacing works',()=>fixture(async f=>{
  f.resource.update({id:'nm1',pending:true,complete:false,fetched_at:'old'});assert.equal(f.calls.length,0);
  await f.tick();assert.deepEqual(f.paint,[false,true]);assert.equal(f.timers.length,0);
  assert.equal(f.calls[0].path,'/api/proxy/tracking/games/people/nm1/imdb');assert.equal(f.calls[0].options.cache,'no-store');
}));
test('cached, missing and invalid identities never trigger provider status polling',()=>fixture(async f=>{
  for(const initial of [null,{id:'nm1',pending:false,complete:true},{id:'tt1',pending:true,complete:false}])f.resource.update(initial);
  assert.equal(f.timers.length,0);assert.equal(f.calls.length,0);
}));
test('domain changes and aborted pages cancel timers and hide stale refresh actions',()=>fixture(async f=>{
  f.resource.update({id:'nm1',pending:true,complete:false});f.resource.update(null);
  assert.equal(f.timers.length,0);assert.equal(f.paint.at(-1),false);
  f.resource.update({id:'nm1',pending:true,complete:false});f.controller.abort();assert.equal(f.timers.length,0);
}));
test('provider failures and an unchanged component do not claim new usable data',()=>fixture(async f=>{
  f.resource.update({id:'nm1',pending:true,complete:false});f.reply({id:'nm1',pending:true,complete:false,error:'forbidden'});
  await f.tick();assert.equal(f.timers.length,0);assert.equal(f.paint.at(-1),false);
  f.resource.update({id:'nm1',pending:true,complete:false,fetched_at:'same'});f.reply({id:'nm1',pending:false,complete:true,fetched_at:'same'});
  await f.tick();assert.equal(f.paint.at(-1),false);
}));
test('new mappings offer a refresh even when the cached source timestamp stays fixed',()=>fixture(async f=>{
  f.resource.update({id:'nm1',pending:true,complete:false,fetched_at:'same',revision:'old'});
  f.reply({id:'nm1',pending:false,complete:true,fetched_at:'same',revision:'new'});
  await f.tick();assert.equal(f.paint.at(-1),true);
}));

test('identity resolution can finish after complete credits without replacing a scrolled portfolio',()=>fixture(async f=>{
  f.resource.update({id:'nm1',pending:false,complete:true,identity_pending:true,fetched_at:'same',revision:'old'});
  f.reply({id:'nm1',pending:false,complete:true,identity_pending:true,retry_after:60,fetched_at:'same',revision:'old'});
  await f.tick();assert.equal(f.paint.at(-1),false);assert.equal(f.timers[0].delay,60000);
  f.reply({id:'nm1',pending:false,complete:true,fetched_at:'same',revision:'joined'});
  await f.tick();assert.equal(f.paint.at(-1),true);assert.equal(f.timers.length,0);
}));

test('new usable works can be refreshed while later mappings still wait and retries preserve the offer',()=>fixture(async f=>{
  f.resource.update({id:'nm1',pending:true,complete:false,fetched_at:'same',revision:'old'});
  f.reply({id:'nm1',pending:true,complete:false,error:'rate_limited',retry_after:60,fetched_at:'same',revision:'more-works'});
  await f.tick();assert.equal(f.paint.at(-1),true);assert.equal(f.timers[0].delay,60000);
  f.reply({id:'nm1',pending:true,complete:false,error:'unavailable',retry_after:300,fetched_at:'same',revision:'more-works'});
  await f.tick();assert.equal(f.paint.at(-1),true);assert.equal(f.timers[0].delay,300000);
  f.reply({id:'nm1',pending:false,complete:true,fetched_at:'same',revision:'more-works'});
  await f.tick();assert.equal(f.paint.at(-1),true);assert.equal(f.timers.length,0);
  f.resource.update(null);assert.equal(f.paint.at(-1),false);
}));

test('transient discovery failures respect cooldown and offer recovered works without reshuffling',()=>fixture(async f=>{
  f.resource.update({id:'nm1',pending:true,complete:false,error:'rate_limited',retry_after:180,fetched_at:'same',revision:'old'});
  assert.equal(f.timers[0].delay,180000);assert.equal(f.calls.length,0);
  f.reply({id:'nm1',pending:true,complete:false,error:'rate_limited',retry_after:300});
  await f.tick();assert.equal(f.paint.at(-1),false);assert.equal(f.timers[0].delay,300000);
  f.reply({id:'nm1',pending:false,complete:true,fetched_at:'same',revision:'new'});
  await f.tick();assert.equal(f.paint.at(-1),true);assert.equal(f.timers.length,0);
}));

test('persistent retry states stop after twenty status reads and navigation clears long cooldowns',()=>fixture(async f=>{
  f.resource.update({id:'nm1',pending:true,complete:false});
  f.reply({id:'nm1',pending:true,complete:false,error:'unavailable',retry_after:3600});
  for(let index=0;index<20;index++)await f.tick();
  assert.equal(f.calls.length,20);assert.equal(f.timers.length,0);assert.equal(f.paint.at(-1),false);
  f.resource.update({id:'nm1',pending:true,complete:false,error:'rate_limited',retry_after:3600});
  assert.equal(f.timers[0].delay,3600000);f.controller.abort();assert.equal(f.timers.length,0);
}));

test('company portfolios poll an approval-protected compact status and preserve the shared refresh behavior',()=>fixture(async f=>{
  f.resource.update({id:'42',source:'igdb-company',pending:true,complete:false,fetched_at:'old'});
  f.reply({id:'42',source:'igdb-company',pending:false,complete:true,fetched_at:'new'});
  await f.tick();assert.equal(f.calls[0].path,'/api/proxy/tracking/games/companies/42/works/status');
  assert.equal(f.paint.at(-1),true);
  f.resource.update({id:'42/../names',source:'igdb-company',pending:true,complete:false});
  assert.equal(f.timers.length,0);
}));
