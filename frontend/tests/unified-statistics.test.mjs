import assert from 'node:assert/strict';
import test from 'node:test';
import {createStatisticsReader} from '../src/lib/unified-statistics-request.ts';
import {gameStatisticsView,statisticsSection,emptyStatistics} from '../src/lib/unified-statistics-data.ts';
import {metricValue} from '../src/lib/stats-overview-data.ts';
const game=(section='genres',generation=1,page=1)=>({status:'ready',domain:'game',section,page,generation,data:[],has_more:false});
function harness(){
  const requests=[],renders=[],errors=[],busy=[];let changed=0;
  let value={domain:'game',media:'all',section:'genres',sort:'count',page:1};
  const controller=new AbortController();
  const reader=createStatisticsReader('viewer name',controller.signal,()=>({...value}),{render:d=>renders.push(d),onError:(...e)=>errors.push(e),onBusy:b=>busy.push(b),changed:()=>changed++},(url,options)=>new Promise(resolve=>requests.push({url,options,resolve})));
  return {reader,requests,renders,errors,busy,controller,get changed(){return changed;},set:(next)=>value={...value,...next}};
}
test('prediction and demand share one request; cached domain return renders immediately without a request',async()=>{
  const h=harness();const prediction=h.reader.prefetch({domain:'game',media:'all',section:'genres',sort:'count',page:1});
  const demand=h.reader.load();assert.equal(h.requests.length,1);assert.match(h.requests[0].url,/viewer%20name/);
  h.requests[0].resolve(Response.json(game()));await Promise.all([prediction,demand]);
  assert.equal(h.renders.length,1);await h.reader.load();assert.equal(h.requests.length,1);assert.equal(h.renders.length,2);
});
test('late domain/section reads never overwrite the selected view, but remain reusable',async()=>{
  const h=harness(),old=h.reader.load();h.set({section:'actors',page:2,generation:4});const selected=h.reader.load();
  assert.match(h.requests[1].url,/generation=4/);h.requests[1].resolve(Response.json(game('actors',4,2)));await selected;
  h.requests[0].resolve(Response.json(game('genres',4)));await old;assert.deepEqual(h.renders.map(d=>d.game.section),['actors']);
  h.set({section:'genres',page:1});await h.reader.load();assert.equal(h.requests.length,2);assert.equal(h.renders.at(-1).game.section,'genres');
});
test('write invalidation discards older flights and Games generation changes invalidate older sections',async()=>{
  const h=harness(),old=h.reader.load();h.reader.clear();const fresh=h.reader.load();
  h.requests[0].resolve(Response.json(game('genres',1)));await old;assert.equal(h.renders.length,0);
  h.requests[1].resolve(Response.json(game('genres',2)));await fresh;
  h.set({section:'actors'});const actors=h.reader.load();h.requests[2].resolve(Response.json(game('actors',3)));await actors;
  h.set({section:'genres'});const genres=h.reader.load();assert.equal(h.requests.length,4);h.requests[3].resolve(Response.json(game('genres',3)));await genres;
});
test('generation conflict, privacy denial, and lifetime cancellation preserve their distinct behavior',async()=>{
  const h=harness();let pending=h.reader.load();h.requests[0].resolve(new Response(null,{status:409}));await pending;assert.equal(h.changed,1);
  pending=h.reader.load();h.requests[1].resolve(new Response(null,{status:403}));await pending;assert.equal(h.errors[0][1],true);
  pending=h.reader.load();h.controller.abort();h.requests[2].resolve(Response.json(game()));await pending;assert.equal(h.renders.length,0);
});
test('Games projection caps presentation at 30 rows while retaining, raw domain data, unknown time, and native genre links',()=>{
  const raw=game();raw.data=Array.from({length:40},(_,i)=>({key:String(i),label:String(i),games:1,recorded_seconds:null,known_time_games:0,mean_score:null,rated_games:0,href:'/browse/games?genres='+i,image:null,roles:[],top_titles:[]}));
  const view=gameStatisticsView(raw,'time');assert.equal(view.game,raw);assert.equal(view.overview.genres.length,30);assert.equal(view.overview.genres[0].href,'/browse/games?genres=0');assert.equal(view.selectionSort,'time');
  assert.equal(metricValue({minutes:0,time_known:false},'hours'),null);
  for(const section of ['overview','genres','actors','staff'])assert.equal(statisticsSection('game',section),section);
  assert.equal(statisticsSection('screen','developers'),'overview');assert.equal(emptyStatistics('series').media_type,'series');
});

test('ready refreshing snapshots continue reading until the new generation settles',async()=>{
  const h=harness();h.set({domain:'screen'});
  let pending=h.reader.load();h.requests[0].resolve(Response.json({status:'ready',refreshing:true,generation:1,overview:emptyStatistics()}));await pending;
  pending=h.reader.load(undefined,true);assert.equal(h.requests.length,2);
  h.requests[1].resolve(Response.json({status:'ready',refreshing:false,generation:2,overview:emptyStatistics()}));await pending;
  await h.reader.load();assert.equal(h.requests.length,2);assert.deepEqual(h.renders.map(d=>d.generation),[1,2,2]);assert.equal(h.busy.at(-1),false);
});
test('privacy denial invalidates outstanding predictions before they can repopulate the cache',async()=>{
  const h=harness();const prediction=h.reader.prefetch({domain:'game',media:'all',section:'genres',sort:'count',page:1});
  h.set({section:'actors'});const denied=h.reader.load();h.requests[1].resolve(new Response(null,{status:403}));await denied;
  h.requests[0].resolve(Response.json(game()));await prediction;
  h.set({section:'genres'});const next=h.reader.load();assert.equal(h.requests.length,3);assert.equal(h.renders.length,0);
  h.requests[2].resolve(new Response(null,{status:403}));await next;assert.equal(h.renders.length,0);
});
