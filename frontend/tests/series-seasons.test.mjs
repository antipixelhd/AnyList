import assert from 'node:assert/strict';
import test from 'node:test';
import {createSeasonLoader} from '../src/lib/series-seasons.ts';

const ready=()=>({data:{id:1,type:'series',seasons:[],catalogue_revision:'one',season_status:{ready:true,pending:false}}});
const deferred=()=>{let resolve;const promise=new Promise(yes=>resolve=yes);return {promise,resolve};};

test('background episode acquisition is shared with an early tab click and stays ready',async()=>{
  const pending=deferred(),lifetime=new AbortController();let reads=0,applies=0;
  const loader=createSeasonLoader(()=>{reads++;return pending.promise;},()=>{applies++;return true;},lifetime.signal,()=>{});
  const background=loader.load(),click=loader.load();
  assert.equal(reads,1);assert.equal(applies,0);
  pending.resolve(ready());await Promise.all([background,click]);
  await loader.load();assert.equal(reads,1);assert.equal(applies,1);
  lifetime.abort();
});

test('late episode responses cannot replace data after navigation or a private edit',async()=>{
  const original=globalThis.setTimeout,schedules=[];
  globalThis.setTimeout=(fn,delay)=>{schedules.push(delay);return 0;};
  try{
    for(const action of ['navigate','edit','pending-write']){
      const pending=deferred(),lifetime=new AbortController();let revision=0,applies=0;
      const loader=createSeasonLoader(()=>pending.promise,()=>{applies++;return action!=='pending-write';},lifetime.signal,()=>{},()=>revision);
      const read=loader.load();
      if(action==='navigate')lifetime.abort();
      if(action==='edit')revision++;
      pending.resolve(ready());await read;
      assert.equal(applies,action==='pending-write'?1:0);
      lifetime.abort();
    }
    assert.deepEqual(schedules,[250,250]);
  }finally{globalThis.setTimeout=original;}
});

test('usable seasons survive a refresh failure, honor cooldowns and install a later revision',async()=>{
  const original=globalThis.setTimeout,schedules=[];
  globalThis.setTimeout=(fn,delay)=>{schedules.push(delay);return 0;};
  const lifetime=new AbortController();
  try{
    const value=ready(),installed=[],statuses=[];
    value.data.season_status={ready:true,pending:true,error:'provider_failure',retry_after:60};
    const loader=createSeasonLoader(async()=>structuredClone(value),result=>{installed.push(result.data.catalogue_revision);return true;},lifetime.signal,error=>statuses.push(error));
    await loader.load();await loader.load();
    assert.deepEqual(installed,['one']);assert.deepEqual(schedules,[60000,60000]);
    value.data.catalogue_revision='two';value.data.season_status={ready:true,pending:false};
    await loader.load();assert.deepEqual(installed,['one','two']);assert.equal(statuses.at(-1),undefined);
  }finally{lifetime.abort();globalThis.setTimeout=original;}
});

test('an open editor can defer installation without repeating the metadata read',async()=>{
  const original=globalThis.setTimeout;
  globalThis.setTimeout=()=>0;
  const lifetime=new AbortController();let reads=0,canApply=false,installed=0;
  try{
    const loader=createSeasonLoader(async()=>{reads++;return ready();},()=>{if(!canApply)return false;installed++;return true;},lifetime.signal,()=>{});
    await loader.load();await loader.load();assert.equal(reads,1);assert.equal(installed,0);
    canApply=true;await loader.load();assert.equal(reads,1);assert.equal(installed,1);
  }finally{lifetime.abort();globalThis.setTimeout=original;}
});

test('a local stale refresh immediately joins publication with its known revision',async()=>{
  const original=globalThis.setTimeout,delays=[],revisions=[],published=deferred(),lifetime=new AbortController();
  globalThis.setTimeout=(fn,delay)=>{delays.push(delay);return 0;};
  try{
    let reads=0,applies=0;
    const stale=ready();stale.data.season_status={ready:true,pending:true,waitable:true,retry_after:2};
    const loader=createSeasonLoader(revision=>{revisions.push(revision);return ++reads===1?Promise.resolve(stale):published.promise;},()=>{applies++;return true;},lifetime.signal,()=>{});
    await loader.load();assert.deepEqual(delays,[0]);
    const waiting=loader.load();assert.equal(applies,1);
    const fresh=ready();fresh.data.catalogue_revision='two';published.resolve(fresh);await waiting;
    assert.deepEqual(revisions,[undefined,'one']);assert.equal(applies,2);
  }finally{lifetime.abort();globalThis.setTimeout=original;}
});
