import assert from 'node:assert/strict';
import test from 'node:test';
import {gameStatsQuery,gameHeadlines,gameChartRows,signedNumber} from '../src/lib/game-statistics-data.ts';
import {createGameStatsLoader} from '../src/lib/game-statistics-request.ts';

test('Games statistics keep unknown time and achievements distinct from explicit zero',()=>{
  const totals={listed_games:2,played_games:2,completed_games:1,recorded_seconds:null,known_time_games:0,mean_score:null,rated_games:0,achievement_percentage:null,achievement_progress_games:0,achievement_count:null,achievement_count_games:0};
  const data={totals,coverage:{unknown_time_games:2}};
  assert.deepEqual(gameHeadlines(data).slice(2).map(row=>row.value),['—','—','—','—']);
  Object.assign(totals,{recorded_seconds:0,achievement_percentage:0,achievement_count:0});
  assert.deepEqual(gameHeadlines(data).slice(2).map(row=>row.value),['≥ 0.0','—','0.0%','0']);
  const [row]=gameChartRows([{key:'1',label:'1h+',games:1,recorded_seconds:null,known_time_games:0,mean_score:null,rated_games:0,lower_seconds:3600,upper_seconds:18000}]);
  assert.equal(row.time_known,false);assert.match(row.detail,/1 to 5 hours, upper bound excluded/);
  assert.equal(signedNumber(null),'—');assert.equal(signedNumber(7200,3600),'+2.0');
});

test('URL selection is bounded, canonical and independent from Movies/Series scope',()=>{
  assert.deepEqual(gameStatsQuery(new URLSearchParams('section=actors&sort=time&page=2&media=all')),{section:'actors',sort:'time',page:2});
  assert.deepEqual(gameStatsQuery(new URLSearchParams('section=all&sort=untrusted&page=5001')),{section:'overview',sort:'count',page:1});
});

test('Games requests cancel stale pages, restart changed generations and stop on privacy denial',async t=>{
  const requests=[],rendered=[],errors=[];let changed=0;
  t.mock.method(globalThis,'fetch',(url,options)=>new Promise(resolve=>requests.push({url,options,resolve})));
  const loader=createGameStatsLoader('viewer name',{render:data=>rendered.push(data),error:(...args)=>errors.push(args),busy(){},changed(){changed++;}});
  const first=loader.load({section:'actors',sort:'count',page:2,generation:4}),second=loader.load({section:'genres',sort:'time',page:1});
  assert.equal(requests[0].options.signal.aborted,true);assert.match(requests[0].url,/viewer%20name\/statistics.*generation=4/);
  assert.equal(requests[1].options.cache,'no-store');requests[1].resolve(Response.json({generation:5}));await second;
  requests[0].resolve(Response.json({generation:4}));await first;assert.deepEqual(rendered,[{generation:5}]);
  const stale=loader.load({section:'actors',sort:'count',page:2,generation:4});requests[2].resolve(new Response(null,{status:409}));await stale;assert.equal(changed,1);
  const denied=loader.load({section:'actors',sort:'count',page:1});requests[3].resolve(new Response(null,{status:403}));await denied;assert.equal(errors[0][1],true);
  const cancelled=loader.load({section:'overview',sort:'count',page:1});loader.stop();requests[4].resolve(Response.json({generation:6}));await cancelled;assert.equal(rendered.length,1);
});
