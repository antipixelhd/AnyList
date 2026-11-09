import assert from 'node:assert/strict';
import test from 'node:test';
import {browseTab,browseTabHref,createBrowseTabContext,createBrowseTabs,setBrowsePanelUrl} from '../src/lib/browse-tabs.ts';

test('cold and retained tabs share live search and layout with compatible screen filters',()=>{
  const context=createBrowseTabContext();
  context.capture('movie',new URLSearchParams('q=Alien&view=timeline&genres=18,878,53&start=1990-01-01&end=2026-12-31&provider=8&region=DE&min_score=7'),true);
  const game=context.forTab('game');
  assert.equal(browseTabHref('game',game.values),'/browse/games?q=Alien&view=timeline');
  const series=context.forTab('series');
  assert.equal(series.moreOpen,true);
  assert.equal(series.values.get('genres'),'18,10765');
  assert.equal(series.values.get('provider'),'8');
  assert.equal(series.values.get('region'),'DE');
  assert.equal(series.values.get('start'),'1990-01-01');
  assert.equal(series.values.get('end'),'2026-12-31');
  assert.equal(series.values.get('min_score'),'7');
  context.capture('series',series.values,true);
  assert.equal(context.forTab('movie').values.get('genres'),'18,878,53');
  context.capture('game',new URLSearchParams('q=Control&view=grid&genres=igdb.genre:5'),false);
  const movie=context.forTab('movie');
  const target=new URL(browseTabHref('movie',movie.values,'/browse?sort=score&view=timeline&cinema=1'),'https://example.test');
  assert.equal(target.searchParams.get('q'),'Control');
  assert.equal(target.searchParams.has('view'),false);
  assert.equal(target.searchParams.get('provider'),'8');
  assert.equal(target.searchParams.get('sort'),'score');
  assert.equal(target.searchParams.get('cinema'),'1');
  assert.equal(movie.moreOpen,true);
});

test('a retained tab is returned synchronously and warming shares the demand load',async()=>{
  const controller=new AbortController();let finish,calls=0;
  const tabs=createBrowseTabs(()=>{calls++;return new Promise(resolve=>{finish=resolve;});},controller.signal);
  const movie={filters:'Drama',cards:[1,2],image:{decoded:true}};
  tabs.set('movie',movie);assert.equal(tabs.peek('movie'),movie);
  const warm=tabs.get('game'),demand=tabs.get('game');assert.equal(warm,demand);assert.equal(calls,1);
  finish({cards:[3]});await warm;
  assert.equal(await tabs.get('movie'),movie);assert.equal(calls,1);
  assert.equal(tabs.peek('movie').image,movie.image);
});

test('a failed tab load can retry and a departing shell cannot retain late data',async()=>{
  const controller=new AbortController();let calls=0,finish;
  const tabs=createBrowseTabs(async()=>{if(!calls++)throw new Error('offline');return new Promise(resolve=>{finish=resolve;});},controller.signal);
  await assert.rejects(tabs.get('series'));
  const next=tabs.get('series');controller.abort();finish({cards:[1]});
  await assert.rejects(next,{name:'AbortError'});assert.equal(tabs.peek('series'),undefined);
});

test('Browse routes resolve distinct panels without treating other pages as tab switches',()=>{
  assert.equal(browseTab(new URL('https://example.test/browse?type=series')),'series');
  assert.equal(browseTab(new URL('https://example.test/browse')),'movie');
  assert.equal(browseTab(new URL('https://example.test/browse/games')),'game');
  assert.equal(browseTab(new URL('https://example.test/browse/games/')),'game');
  assert.equal(browseTab(new URL('https://example.test/browse/?type=series')),'series');
  assert.equal(browseTab(new URL('https://example.test/title/1')),undefined);
});

test('a hidden panel can finish loading without overwriting the visible URL',t=>{
  const original=Object.getOwnPropertyDescriptor(globalThis,'history');
  const calls=[],state={astro:'retained'};
  Object.defineProperty(globalThis,'history',{configurable:true,value:{state,replaceState:(...args)=>calls.push(args)}});
  t.after(()=>{if(original)Object.defineProperty(globalThis,'history',original);else delete globalThis.history;});
  const root={dataset:{},isConnected:true,hidden:true,inert:true,dispatchEvent(){},hasAttribute:()=>false};
  setBrowsePanelUrl(root,'/browse?type=series&q=Drama');
  assert.equal(root.dataset.browseUrl,'/browse?type=series&q=Drama');assert.equal(calls.length,0);
  root.hidden=root.inert=false;
  setBrowsePanelUrl(root,'/browse?type=series&q=New');
  assert.equal(calls.length,1);assert.equal(calls[0][0],state);
});
