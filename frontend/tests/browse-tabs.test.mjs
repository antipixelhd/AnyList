import assert from 'node:assert/strict';
import test from 'node:test';
import {browseTab,createBrowseTabs,setBrowsePanelUrl} from '../src/lib/browse-tabs.ts';

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
