import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';
import {createMinimumDisplay} from '../src/lib/minimum-display.ts';
import {profilePanelKey,panelActive,panelUrl,setPanelUrl,setPanelWaiting} from '../src/lib/profile-panel.ts';

test('profile panels are limited to lists and statistics of an exact profile',()=>{
  for(const key of ['movies','series','list','games','stats','stats/games'])assert.deepEqual(profilePanelKey(new URL(`https://example.test/user/alex%20one/${key}/?section=genres`)),{username:'alex one',key});
  for(const path of ['/user/alex','/user/alex/favorites','/user/alex/social','/settings','/games/1','/user/alex/stats/games/other'])assert.equal(profilePanelKey(new URL(path,'https://example.test')),undefined);
});
test('inactive completions update their own URL without changing the active page or history',t=>{
  const before=Object.getOwnPropertyDescriptor(globalThis,'history'),calls=[];
  Object.defineProperty(globalThis,'history',{configurable:true,value:{state:{index:9},replaceState:(...args)=>calls.push(args),pushState:(...args)=>calls.push(args)}});
  t.after(()=>{if(before)Object.defineProperty(globalThis,'history',before);else delete globalThis.history;});
  const panel={dataset:{profileUrl:'https://example.test/user/alex/games'}};
  const root={isConnected:false,closest:selector=>selector==='[data-profile-url]'?panel:null};
  setPanelUrl(root,'https://example.test/user/alex/games?page=2');
  assert.equal(panelUrl(root),'https://example.test/user/alex/games?page=2');assert.equal(panelActive(root),false);assert.equal(calls.length,0);
  root.isConnected=true;setPanelUrl(root,'https://example.test/user/alex/games?page=3',true);
  assert.equal(calls.length,1);assert.deepEqual(calls[0][0],{index:9});
});


test('waiting panels disable data controls without disabling domain navigation and restore prior inert states',()=>{
  const controls=[{inert:false},{inert:true}],attributes=new Set();
  const root={toggleAttribute:(name,on)=>on?attributes.add(name):attributes.delete(name),querySelectorAll:selector=>{
    assert.equal(selector.includes('.stats-domain-navigation'),false);return controls;
  }};
  setPanelWaiting(root,true);setPanelWaiting(root,true);
  assert.equal(attributes.has('data-profile-waiting'),true);assert.deepEqual(controls.map(node=>node.inert),[true,true]);
  setPanelWaiting(root,false);assert.equal(attributes.has('data-profile-waiting'),false);assert.deepEqual(controls.map(node=>node.inert),[false,true]);
});


test('reclicking a selected or pending profile tab cannot escape into full-page navigation',()=>{
  const source=readFileSync(new URL('../src/lib/profile-shell.ts',import.meta.url),'utf8');
  const start=source.indexOf("  host.addEventListener('click'");
  const binding=source.slice(start,source.indexOf('  bindPredictionIntent',start));
  let click,prevented=0,stopped=0,selected=0;
  const link={target:'',hasAttribute:()=>false,closest:selector=>selector.includes('data-integrated-statistics')?null:({})};
  runInNewContext(stripTypeScriptTypes(binding),{host:{addEventListener:(_,fn)=>click=fn},signal:new AbortController().signal,
    target:()=>({key:'stats',url:'/user/alex/stats'}),wanted:'stats',select:()=>selected++});
  const event={target:{closest:()=>link},button:0,preventDefault:()=>prevented++,stopImmediatePropagation:()=>stopped++};
  click(event);assert.equal(prevented,1);assert.equal(stopped,1);
  link.closest=()=>null;click(event);assert.equal(prevented,1,'same-domain pagination keeps its own controller');
});


test('a failed optimistic statistics load restores the source list scroll position',()=>{
  const sourceText=readFileSync(new URL('../src/lib/profile-shell.ts',import.meta.url),'utf8');
  const start=sourceText.indexOf('    const failed=()=>{');
  const snippet=sourceText.slice(start,sourceText.indexOf('    if(cached?.ready)',start));
  const source={dispatchEvent(){}},preview={},scrolls=[],states=[];
  const context={stopProgress(){},signal:new AbortController().signal,wanted:'stats',key:'stats',active:'movies',source,shown:preview,
    positions:new Map([['movies',1250]]),scrollY:150,window:{scrollTo:(x,y)=>scrolls.push(y)},
    setPanelWaiting:(root,on)=>states.push([root,on]),show:root=>context.shown=root,nav:{removeAttribute(){}},update(){},
    document:{createElement:()=>({dataset:{},setAttribute(){},addEventListener(){},append(){}})},host:{append(){}},Event,select(){},url:'/user/alex/stats',
  };
  runInNewContext(stripTypeScriptTypes(snippet)+';this.failed=failed;',context);context.failed();
  assert.equal(context.shown,source);assert.equal(context.wanted,'movies');assert.deepEqual(scrolls,[1250]);assert.deepEqual(states,[[source,false]]);
});

test('a cold list selection uses the shared page progress without a local loading frame',async()=>{
  const sourceText=readFileSync(new URL('../src/lib/profile-shell.ts',import.meta.url),'utf8');
  const start=sourceText.indexOf('  function select(key:string,url:string)');
  const snippet=sourceText.slice(start,sourceText.indexOf('  const target=',start));
  const source={dataset:{},matches:()=>false,dispatchEvent(){}},preview={kind:'games'},waiting=[],positions=new Map();let rejectRead;
  const events=[];
  const context={URL,changeStatistics:{cancel(){}},stopProgress(){},createMinimumDisplay,progressRunning:false,signal:new AbortController().signal,wanted:'movies',active:'movies',shown:source,positions,scrollY:780,
    panels:new Map([['movies',{root:source,ready:true}]]),loadingTemplates:new Map(),
    read:()=>new Promise((resolve,reject)=>rejectRead=reject),setPanelWaiting:(root,on)=>waiting.push([root,on]),update(){},
    show:root=>context.shown=root,nav:{setAttribute(){},removeAttribute(){},getBoundingClientRect:()=>({top:-600})},
    host:{querySelector:()=>null,append(){}},window:{scrollTo(){}},location:{href:'https://example.test/user/alex/movies'},
    document:{dispatchEvent:event=>events.push(event.type),createElement:()=>({dataset:{},setAttribute(){},addEventListener(){},append(){}})},Event,
  };
  runInNewContext(stripTypeScriptTypes(snippet)+';this.select=select;',context);
  context.select('games');assert.equal(context.shown,source);assert(events.includes('anylist:route-progress-start'));assert.equal(context.wanted,'games');assert.equal(positions.get('movies'),780);
  assert.deepEqual(waiting,[[source,true]]);
  rejectRead(new Error('unavailable'));await new Promise(setImmediate);
  assert.equal(context.shown,source);assert.equal(context.wanted,'movies');assert.deepEqual(waiting.at(-1),[source,false]);
});
