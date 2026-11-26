import test from 'node:test';
import assert from 'node:assert/strict';
import {contributorAvailability,contributorMediaOptionDisabled,contributorMediaSwitchVisibility,contributorRoleState,defaultContributorGameRole,defaultsToContributorGames,disabledContributorOption,mergeContributorDomains,mergePersonTabRoles,personSwitchVisibility} from '../src/lib/contributor-availability.ts';

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

test('role choices follow the selected media and require a verified negative',()=>{
  const staff={movie:'empty',series:'unknown',game:'available'};
  assert.equal(contributorRoleState(staff,'all'),'unknown');
  assert.equal(contributorRoleState(staff,'game'),'available');
  assert.equal(contributorRoleState(staff,'movie'),'empty');
  assert.equal(contributorRoleState({...staff,series:'empty'},'all'),'empty');
  assert.equal(contributorRoleState({...staff,series:'available'},'all'),'available');
});

test('an unplayable Games choice hides on repeat visits, while roles with credits elsewhere stay visible',()=>{
  const actor={movie:'available',series:'empty',game:'unavailable'};
  const staff={movie:'empty',series:'empty',game:'unavailable'};
  assert.equal(personSwitchVisibility({actor,staff},'actor','all',true).domain,false);
  assert.equal(disabledContributorOption('domain','game',actor),true);
  const gameActor={...actor,game:'available'};
  assert.equal(personSwitchVisibility({actor:gameActor,staff},'actor','game',true).role,false);
  const screenStaff={...staff,series:'available'};
  assert.equal(personSwitchVisibility({actor:gameActor,staff:screenStaff},'actor','game',true).role,true);
  assert.equal(contributorRoleState(screenStaff,'game'),'unavailable');
  assert.equal(personSwitchVisibility({actor,staff:screenStaff},'actor','movie',true).role,true);
  assert.equal(contributorRoleState(screenStaff,'movie'),'empty');
});

test('saved negatives collapse redundant switches while unknown checks preserve the layout',()=>{
  const actor={movie:'available',series:'empty',game:'empty'};
  const empty={movie:'empty',series:'empty',game:'empty'};
  const roles={actor,staff:empty};
  assert.deepEqual(personSwitchVisibility(undefined,'actor','all',true),{role:true,domain:true,screen:true});
  assert.deepEqual(personSwitchVisibility(roles,'actor','all',true),{role:false,domain:false,screen:false});
  assert.equal(personSwitchVisibility(roles,'staff','all',false).role,true,'a direct empty view can switch away');
  assert.equal(personSwitchVisibility(roles,'actor','game',false).domain,true);
  assert.equal(personSwitchVisibility(roles,'actor','series',false).screen,true);
  const unknown={movie:'unknown',series:'unknown',game:'unknown'};
  assert.deepEqual(personSwitchVisibility(roles,'actor','all',true,{actor:unknown,staff:unknown}),
    {role:true,domain:true,screen:true},'newly confirmed negatives stay visible for the cold visit');
  assert.deepEqual(mergePersonTabRoles(roles,{actor:unknown,staff:unknown}),roles);
  const fresh=mergePersonTabRoles(roles,{actor:{...unknown,series:'available',game:'available'},
    staff:{...unknown,series:'available'}});
  assert.deepEqual(personSwitchVisibility(fresh,'actor','all',true),{role:true,domain:true,screen:true});
});

test('studios use saved media evidence without collapsing the layout mid-visit',()=>{
  const saved={movie:'available',series:'unknown',game:'unknown'};
  const verified={movie:'available',series:'empty',game:'empty'};
  assert.deepEqual(contributorMediaSwitchVisibility(undefined,'all',true),{domain:true,screen:true});
  assert.deepEqual(contributorMediaSwitchVisibility(verified,'all',true,saved),{domain:true,screen:true});
  assert.equal(contributorMediaOptionDisabled('media_type','series','all',true,verified),true);
  assert.deepEqual(contributorMediaSwitchVisibility(verified,'all',true),{domain:false,screen:false});
  assert.equal(contributorMediaSwitchVisibility(verified,'series',false).screen,true);
  assert.equal(contributorMediaSwitchVisibility(verified,'game',false).domain,true);
  const rechecking={movie:'unknown',series:'unknown',game:'available'};
  const updated=mergeContributorDomains(verified,rechecking);
  assert.deepEqual(contributorMediaSwitchVisibility(updated,'all',true,verified),{domain:true,screen:false});
  assert.equal(contributorMediaOptionDisabled('media_type','series','series',true,verified),false);
});

test('contributors with games and no reachable screen credits drop the redundant switches',()=>{
  const games={movie:'unavailable',series:'unavailable',game:'available'};
  const empty={...games,game:'unavailable'};
  assert.equal(defaultsToContributorGames(games),true);
  assert.deepEqual(contributorMediaSwitchVisibility(games,'game',true),{domain:false,screen:false});
  assert.deepEqual(contributorMediaSwitchVisibility(games,'all',false),{domain:true,screen:false});
  assert.deepEqual(personSwitchVisibility({actor:games,staff:empty},'actor','all',false),
    {role:false,domain:true,screen:false});
  assert.deepEqual(personSwitchVisibility({actor:games,staff:empty},'actor','game',true),
    {role:false,domain:false,screen:false});
  assert.equal(defaultContributorGameRole({actor:games,staff:empty},'actor',false),'actor');
  assert.equal(defaultContributorGameRole({actor:empty,staff:games},'actor',true),undefined);
  assert.equal(defaultContributorGameRole({actor:empty,staff:games},'actor',false),'staff');
  const linked=mergeContributorDomains(games,{movie:'unknown',series:'unknown',game:'available'});
  assert.equal(defaultsToContributorGames(linked),false);
  assert.equal(contributorMediaSwitchVisibility(linked,'game',true,games).domain,true);
});

async function fixture(run){
  const previous={fetch:globalThis.fetch,set:globalThis.setTimeout,clear:globalThis.clearTimeout};
  const timers=[],calls=[],paints=[],controller=new AbortController();
  let reply={key:'catalogue:1',domains:{movie:'available',series:'empty',game:'unknown'},pending:true};
  globalThis.setTimeout=(callback,delay)=>{const timer={callback,delay};timers.push(timer);return timer;};
  globalThis.clearTimeout=timer=>{const i=timers.indexOf(timer);if(i>=0)timers.splice(i,1);};
  globalThis.fetch=async(path,options)=>{calls.push({path,options});return Response.json(reply);};
  const start=(endpoint='contributors/actor/catalogue%3A1')=>contributorAvailability(endpoint,'catalogue:1',controller.signal,value=>paints.push(value.roles??value.domains));
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

test('one People check keeps polling for unresolved Staff after Acting has settled',()=>fixture(async f=>{
  f.start('contributors/person/catalogue%3A1');
  const actor={movie:'available',series:'empty',game:'empty'};
  const staff={movie:'empty',series:'unknown',game:'available'};
  f.reply({key:'catalogue:1',roles:{actor,staff},pending:true});
  await f.tick();
  assert.deepEqual(f.paints,[{actor,staff}]);
  assert.equal(f.timers.length,1);
  f.reply({key:'catalogue:1',roles:{actor,staff:{...staff,series:'empty'}},pending:false});
  await f.tick();
  assert.equal(f.paints.at(-1).staff.series,'empty');
  assert.equal(f.timers.length,0);
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

test('known domain choices stop availability reads while source enrichment continues separately',()=>fixture(async f=>{
  f.start();f.reply({key:'catalogue:1',domains:{movie:'available',series:'empty',game:'available'},pending:true});
  await f.tick();assert.equal(f.calls.length,1);assert.equal(f.timers.length,0);
}));
