import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import {browsePanelActive} from '../src/lib/browse-tabs.ts';

test('a failed cold Browse switch restores the source selection and a direct retry remains possible',async()=>{
  const source=readFileSync(new URL('../src/lib/browse-shell.ts',import.meta.url),'utf8');
  const start=source.indexOf('  function select(tab:BrowseTab)');
  const snippet=source.slice(start,source.indexOf("  nav.addEventListener('click'",start));
  const attributes=new Set(),nav=[],controls=[{inert:false}];let reads=0;
  const shown={isConnected:true,hidden:false,inert:false,dataset:{},hasAttribute:name=>attributes.has(name),setAttribute:name=>attributes.add(name),removeAttribute:name=>attributes.delete(name),querySelectorAll:()=>controls,append(){}};
  const context={signal:new AbortController().signal,wanted:'movie',active:'movie',shown,positions:new Map(),scrollY:20,
    document:{dispatchEvent(){},createElement:()=>({append(){},addEventListener(){},remove(){}})},Event,
    tabs:{peek:tab=>tab==='movie'?shown:undefined,get:()=>{reads++;return Promise.reject(new Error('network failed'));}},
    updateNav:tab=>nav.push(tab),loading:undefined,
  };
  runInNewContext(stripTypeScriptTypes(snippet)+';this.select=select;',context);
  context.select('game');assert.equal(browsePanelActive(shown),false);assert.equal(controls[0].inert,true);
  await new Promise(setImmediate);
  assert.equal(context.wanted,'movie');assert.equal(nav.at(-1),'movie');assert.equal(browsePanelActive(shown),true);assert.equal(controls[0].inert,false);
  context.select('game');await new Promise(setImmediate);assert.equal(reads,2);
});

test('a Games pagination frame queued before a tab switch cannot append outgoing results',()=>{
  const source=readFileSync(new URL('../src/lib/game-browse.ts',import.meta.url),'utf8');
  const start=source.indexOf("  window.addEventListener('scroll'");
  const snippet=source.slice(start,source.indexOf("  document.addEventListener('anylist:cache-invalidated'",start));
  let scroll,queued,active=true,loads=0;
  runInNewContext(stripTypeScriptTypes(snippet),{window:{addEventListener:(_,fn)=>scroll=fn},root:{},browsePanelActive:()=>active,
    signal:new AbortController().signal,scrollY:100,loading:false,presentedCategories:false,near:true,frame:0,payload:{has_more:true},
    demand:{advance(){},consume:()=>true},requestAnimationFrame:fn=>{queued=fn;return 1;},load:()=>loads++});
  scroll();active=false;queued();assert.equal(loads,0);
});
