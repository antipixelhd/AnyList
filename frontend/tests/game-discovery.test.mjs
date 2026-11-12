import assert from 'node:assert/strict';
import test from 'node:test';
import {gameResultHref,resolveGameId} from '../src/lib/game-discovery.ts';
import {quickSearchRequests,availableQuickSearchTabs,visibleQuickSearchSections,readQuickSearch} from '../src/lib/quick-search-data.ts';

test('games use canonical catalogue routes and resolve remote IDs without a tracking write',async()=>{
  const game={id:null,igdb_id:240009,type:'game',title:'Erdtree',game_class:'expansion'};
  assert.equal(gameResultHref(game),'/discover-game?id=240009');
  assert.equal(gameResultHref({...game,id:12}),'/games/12');
  let request;
  assert.equal(await resolveGameId(240009,new AbortController().signal,async(url,options)=>{
    request={url,method:options.method};return {ok:true,json:async()=>({id:12})};
  }),12);
  assert.deepEqual(request,{url:'/api/proxy/tracking/games/resolve/240009',method:'POST'});
  await assert.rejects(resolveGameId(1,new AbortController().signal,async()=>({ok:true,json:async()=>({id:null})})),/Could not load/);
});

test('Games title fan-out, pending tabs and visibility stay separate from movie/series ranking',()=>{
  assert.deepEqual(quickSearchRequests('Ring',undefined,false,true).map(row=>row.kind),['game','contributors','contributors']);
  assert.ok(!quickSearchRequests('Ring',undefined,true,false).some(row=>row.kind==='game'));
  const counts={all:2,movie:1,series:1,game:4,actor:1};
  assert.deepEqual(visibleQuickSearchSections(false,'all',true,counts),['all','game','actor']);
  assert.deepEqual(visibleQuickSearchSections(false,'all',true,counts,true,false),['all','actor']);
  assert.deepEqual(availableQuickSearchTabs({},true,false,true,false,true,true),['game']);
  assert.deepEqual(visibleQuickSearchSections(true,'game',true,counts),['game']);
});

test('a stalled game request does not gate an independently completed movie response',async()=>{
  let release;const events=[];
  const fetcher=async url=>{if(url==='/game')await new Promise(resolve=>release=resolve);return {ok:true,json:async()=>({results:[url]})};};
  const game=readQuickSearch({kind:'game',url:'/game'},new AbortController().signal,()=>events.push('game'),fetcher);
  await readQuickSearch({kind:'movie',url:'/movie'},new AbortController().signal,()=>events.push('movie'),fetcher);
  assert.deepEqual(events,['movie']);release();await game;
  assert.deepEqual(events,['movie','game']);
});
