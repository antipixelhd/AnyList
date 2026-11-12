import test from 'node:test';
import assert from 'node:assert/strict';
import {readVerifiedGamePage,pauseGameVerification,gameBrowseCacheable} from '../src/lib/game-browse-verification.ts';

test('pending bootstrap and failed pages cannot poison the discovery cache',()=>{
  assert.equal(gameBrowseCacheable({results:[],verification_pending:true}),false);
  assert.equal(gameBrowseCacheable({results:[1],verification_error:'unavailable'}),false);
  assert.equal(gameBrowseCacheable({results:[]}),true);
  assert.equal(gameBrowseCacheable({results:[1],partial:true}),true);
});

test('publishes verified ordered prefixes and settles on the same completed page',async()=>{
  const controller=new AbortController(),updates=[],delays=[];
  const pages=[{results:[],verification_pending:true,retry_after:2},
    {results:[1],verification_pending:true},{results:[1,2],verification_pending:true},{results:[1,2]}];
  const final=await readVerifiedGamePage(async()=>pages.shift(),page=>updates.push(page.results),controller.signal,
    async ms=>{delays.push(ms);});
  assert.deepEqual(updates,[[],[1],[1,2],[1,2]]);
  assert.deepEqual(final.results,[1,2]);assert.deepEqual(delays,[2000,2000,2000]);
});

test('provider errors and bounded waiting preserve the last published prefix',async()=>{
  const updates=[],signal=new AbortController().signal;
  await assert.rejects(readVerifiedGamePage(async()=>({results:[1],verification_error:'unavailable'}),
    page=>updates.push(page.results),signal),/Unable to verify/);
  assert.deepEqual(updates,[[1]]);
  let calls=0;
  await assert.rejects(readVerifiedGamePage(async()=>{calls++;return {results:[1],verification_pending:true};},
    ()=>{},signal,async()=>{},3),/still being checked/);
  assert.equal(calls,3);
});

test('an obsolete delayed query never publishes into the new view',async()=>{
  const controller=new AbortController();let finish;
  const held=new Promise(resolve=>{finish=resolve;});let updates=0;
  const pending=readVerifiedGamePage(()=>held,()=>updates++,controller.signal);
  controller.abort();finish({results:[999]});
  await assert.rejects(pending,{name:'AbortError'});assert.equal(updates,0);
});

test('aborting polling cancels its delay without requesting another page',async()=>{
  const controller=new AbortController();let calls=0;
  const pending=readVerifiedGamePage(async()=>{calls++;return {verification_pending:true};},
    ()=>queueMicrotask(()=>controller.abort()),controller.signal);
  await assert.rejects(pending,{name:'AbortError'});assert.equal(calls,1);
  await assert.rejects(pauseGameVerification(1,controller.signal),{name:'AbortError'});
});

test('warm complete pages perform one read and no polling',async()=>{
  let calls=0;
  const result=await readVerifiedGamePage(async()=>{calls++;return {results:[1]};},()=>{},
    new AbortController().signal,async()=>assert.fail('Unexpected delay'));
  assert.equal(calls,1);assert.deepEqual(result.results,[1]);
});
