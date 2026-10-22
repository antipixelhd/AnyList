import test from 'node:test';
import assert from 'node:assert/strict';
import {contributorAvailability,disabledContributorOption} from '../src/lib/contributor-availability.ts';

test('only verified empty options disable; merged movies/series needs both negatives',()=>{
  for(const movie of ['unknown','available','empty'])for(const series of ['unknown','available','empty']){
    const domains={movie,series,game:'unknown'};
    assert.equal(disabledContributorOption('domain','movies-series',domains),movie==='empty'&&series==='empty');
    assert.equal(disabledContributorOption('media_type','movie',domains),movie==='empty');
    assert.equal(disabledContributorOption('media_type','series',domains),series==='empty');
    assert.equal(disabledContributorOption('domain','game',domains),false);
  }
  assert.equal(disabledContributorOption('domain','game',{movie:'available',series:'empty',game:'empty'}),true);
});

async function fixture(run){
  const previous={fetch:globalThis.fetch,set:globalThis.setTimeout,clear:globalThis.clearTimeout};
  const timers=[],calls=[],paints=[],controller=new AbortController();
  let reply={key:'catalogue:1',domains:{movie:'available',series:'empty',game:'unknown'},pending:true};
  globalThis.setTimeout=(callback,delay)=>{const timer={callback,delay};timers.push(timer);return timer;};
  globalThis.clearTimeout=timer=>{const i=timers.indexOf(timer);if(i>=0)timers.splice(i,1);};
  globalThis.fetch=async(path,options)=>{calls.push({path,options});return Response.json(reply);};
  const start=(endpoint='contributors/actor/catalogue%3A1')=>contributorAvailability(endpoint,'catalogue:1',controller.signal,value=>paints.push(value));
  const tick=async()=>{timers.shift()?.callback();await new Promise(resolve=>setImmediate(resolve));};
  try{await run({timers,calls,paints,controller,start,tick,reply:value=>reply=value});}
  finally{controller.abort();Object.assign(globalThis,{fetch:previous.fetch,setTimeout:previous.set,clearTimeout:previous.clear});}
}

test('deferred compact checks paint choices without replacing works and stop on completion',()=>fixture(async f=>{
  f.start();assert.equal(f.calls.length,0);assert.equal(f.timers[0].delay,0);
  await f.tick();assert.equal(f.calls[0].path,'/api/proxy/tracking/contributors/actor/catalogue%3A1/availability');
  assert.equal(f.calls[0].options.cache,'no-store');assert.equal(f.timers[0].delay,3000);
  f.reply({key:'catalogue:1',domains:{movie:'available',series:'empty',game:'empty'},pending:false});
  await f.tick();assert.equal(f.paints.at(-1).game,'empty');assert.equal(f.timers.length,0);
}));

test('stale identities, malformed evidence and traversal paths never disable controls',()=>fixture(async f=>{
  f.start('../people');assert.equal(f.timers.length,0);
  f.start();f.reply({key:'catalogue:2',domains:{movie:'empty',series:'empty',game:'empty'}});
  await f.tick();assert.equal(f.paints.length,0);
  f.start();f.reply({key:'catalogue:1',domains:{movie:'empty',series:'empty'}});
  await f.tick();assert.equal(f.paints.length,0);
}));

test('navigation aborts pending checks and repeated partial evidence has a finite poll budget',()=>fixture(async f=>{
  f.start();f.controller.abort();assert.equal(f.timers.length,0);
}));

test('unknown mapping evidence stops polling after twenty checks without claiming a negative',()=>fixture(async f=>{
  f.start();for(let i=0;i<25;i++)await f.tick();
  assert.equal(f.calls.length,20);assert.equal(f.timers.length,0);assert.ok(f.paints.every(value=>value.game==='unknown'));
}));
