import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';
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


test('a failed statistics load keeps its destination frame and leaves the retained source usable',()=>{
  const sourceText=readFileSync(new URL('../src/lib/profile-shell.ts',import.meta.url),'utf8');
  const start=sourceText.indexOf('    const failed=()=>{');
  const snippet=sourceText.slice(start,sourceText.indexOf('    if(cached?.ready)',start));
  const source={dispatchEvent(){}},preview={prepend(){}},scrolls=[],states=[];
  const context={stopProgress(){},signal:new AbortController().signal,wanted:'stats',key:'stats',active:'movies',source,shown:preview,
    positions:new Map([['movies',1250]]),scrollY:150,window:{scrollTo:(x,y)=>scrolls.push(y)},
    setPanelWaiting:(root,on)=>states.push([root,on]),show:root=>context.shown=root,nav:{removeAttribute(){}},update(){},
    document:{createElement:()=>({dataset:{},setAttribute(){},addEventListener(){},append(){}})},host:{append(){}},Event,select(){},url:'/user/alex/stats',
  };
  runInNewContext(stripTypeScriptTypes(snippet)+';this.failed=failed;',context);context.failed();
  assert.equal(context.shown,preview);assert.equal(context.wanted,'stats');assert.deepEqual(scrolls,[]);assert.deepEqual(states,[[source,false]]);
});

for(const destination of ['games','stats','stats/games'])test(`a cold ${destination} selection shows its frame and keeps shared page progress`,async()=>{
  const sourceText=readFileSync(new URL('../src/lib/profile-shell.ts',import.meta.url),'utf8');
  const start=sourceText.indexOf('  function select(');
  const snippet=sourceText.slice(start,sourceText.indexOf('  const target=',start));
  const source={dataset:{},matches:()=>false,dispatchEvent(){}},preview={kind:'games',dataset:{},prepend(){}},waiting=[];let rejectRead;
  const events=[];
  const context={URL,changeStatistics:{cancel(){}},stopProgress(){},progressRunning:false,signal:new AbortController().signal,wanted:'movies',active:'movies',shown:source,scrollY:780,
    panels:new Map([['movies',{root:source,ready:true}]]),loadingTemplates:new Map(),profileLoadingFrame:()=>preview,
    createLoadingReveal:()=>({begin(){},finish(){}}),
    read:()=>new Promise((resolve,reject)=>rejectRead=reject),setPanelWaiting:(root,on)=>waiting.push([root,on]),update(){},
    show:root=>context.shown=root,nav:{setAttribute(){},removeAttribute(){},getBoundingClientRect:()=>({top:-600})},
    host:{querySelector:()=>null,append(){}},window:{scrollTo(){}},location:{href:'https://example.test/user/alex/movies'},
    document:{dispatchEvent:event=>events.push(event.type),createElement:()=>({dataset:{},setAttribute(){},addEventListener(){},append(){}})},Event,
  };
  runInNewContext(stripTypeScriptTypes(snippet)+';this.select=select;',context);
  context.select(destination);assert.equal(context.shown,preview);assert(events.includes('anylist:route-progress-start'));assert.equal(context.wanted,destination);
  assert.deepEqual(waiting,[[source,true]]);
  rejectRead(new Error('unavailable'));await new Promise(setImmediate);
  assert.equal(context.shown,preview);assert.equal(context.wanted,destination);assert.deepEqual(waiting.at(-1),[source,false]);
});

test('the statistics controller owns its media filter even when the shell entered on Games',()=>{
  const source=readFileSync(new URL('../src/lib/profile-shell.ts',import.meta.url),'utf8');
  const start=source.indexOf("  host.addEventListener('click'");
  const binding=source.slice(start,source.indexOf('  bindPredictionIntent',start));
  let click;
  const link={target:'',hasAttribute:()=>false,closest:selector=>selector==='[data-integrated-statistics] [data-screen-media]'?{}:null};
  runInNewContext(stripTypeScriptTypes(binding),{host:{addEventListener:(_,fn)=>click=fn},signal:new AbortController().signal,target(){throw new Error('Media filters must not select a shell panel');}});
  click({target:{closest:()=>link},button:0,preventDefault(){throw new Error('Shell consumed filter');}});
});

test('profile prediction retains destination styles without applying them until selection',()=>{
  const source=readFileSync(new URL('../src/lib/profile-shell.ts',import.meta.url),'utf8');
  const read=source.slice(source.indexOf('  async function read('),source.indexOf('  function show('));
  assert.doesNotMatch(read,/preparePanelStyles/);assert.match(read,/styles:doc/);
  const activate=source.slice(source.indexOf('    const activate='),source.indexOf('    const failed='));
  assert(activate.indexOf('await preparePanelStyles')<activate.indexOf('stage.append(root)'));
  assert.match(activate,/await document\.fonts\.ready/);
  assert.match(activate,/Promise\.allSettled\(\[initialize\(panel,signal,layoutReady\),layoutReady\]\)/);
});

test('returning during initial import still waits for its own font and layout barrier',async()=>{
  const source=readFileSync(new URL('../src/lib/profile-shell.ts',import.meta.url),'utf8');
  const start=source.indexOf('            const [mounted,layout]=');
  const end=source.indexOf('            await panelLayoutFrame',start);
  let initialized,layout;
  const mount=new Promise(resolve=>{initialized=resolve;});
  const layoutReady=new Promise(resolve=>{layout=resolve;});
  const wait=runInNewContext('(async()=>{'+stripTypeScriptTypes(source.slice(start,end))+'})()',{
    panel:{},signal:new AbortController().signal,initialize:()=>mount,layoutReady,
  });
  let settled=false;
  const done=wait.then(()=>{settled=true;});
  initialized();
  await Promise.resolve();await Promise.resolve();
  assert.equal(settled,false);
  layout();await done;
  assert.equal(settled,true);
});

test('failed component loading waits for staged styles before its cleanup can run',async()=>{
  const source=readFileSync(new URL('../src/lib/profile-shell.ts',import.meta.url),'utf8');
  const start=source.indexOf('async function initialize(');
  const end=source.indexOf('\nfunction setup()',start);
  const failure=new Error('Module unavailable');
  const moduleRead=Promise.reject(failure);
  let finishStyles;
  const styles=new Promise(resolve=>{finishStyles=resolve;});
  const snippet=source.slice(start,end).replaceAll(/import\('\.\/[^']+'\)/g,'moduleRead');
  const initialize=runInNewContext(stripTypeScriptTypes(snippet)+';initialize;',{moduleRead});
  const panel={root:{isConnected:true,querySelector:()=>null},kind:'games',ready:false};
  const pending=initialize(panel,new AbortController().signal,styles);
  let settled=false;
  const result=pending.catch(error=>{settled=true;assert.equal(error,failure);});
  await Promise.resolve();await Promise.resolve();
  assert.equal(settled,false);
  finishStyles();await result;
  assert.equal(settled,true);
  assert.equal(panel.initializing,undefined);
  assert.equal(panel.ready,false);
});
