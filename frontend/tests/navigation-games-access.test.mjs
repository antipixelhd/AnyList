import test from 'node:test';
import assert from 'node:assert/strict';
import {snapshotHasGames,authorizeGameSnapshot} from '../src/lib/navigation-games-access.ts';
import {reusableGamesPage} from '../src/lib/game-public-routes.ts';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';

test('game content marks mixed snapshots, while generic game-list navigation stays unrestricted',()=>{
  for(const html of ['<a href="/games/7">Game</a>',"<a href='/games/7?tab=social'>Game</a>",'<a href="https://example.test/games/7">Game</a>'])assert.equal(snapshotHasGames(html),true);
  for(const html of ['<a href="/user/alex/games">Games List</a>','<a href="/title/7">Series</a>','<img src="/games/7">'])assert.equal(snapshotHasGames(html),false);
});

test('cached game content requires matching viewer and current approval on every reuse',async()=>{
  const controller=new AbortController(),calls=[];let approved=true,id=7;
  const read=async(path,options)=>{calls.push({path,options});return Response.json({id,can_access_games:approved});};
  assert.equal(await authorizeGameSnapshot('7',controller.signal,read),true);
  approved=false;assert.equal(await authorizeGameSnapshot('7',controller.signal,read),false);
  approved=true;id=8;assert.equal(await authorizeGameSnapshot('7',controller.signal,read),false);
  assert.equal(calls.length,3);assert(calls.every(call=>call.options.cache==='no-store'&&call.options.credentials==='same-origin'));
  await authorizeGameSnapshot('anonymous',controller.signal,read);controller.abort();
  assert.equal(await authorizeGameSnapshot('7',controller.signal,read),false);assert.equal(calls.length,3);
});

test('middleware-approved accounts switch their own Games tabs without authorization round trips',async t=>{
  const original=Object.getOwnPropertyDescriptor(globalThis,'document');
  const account={cacheUser:'7',cacheUsername:'alex',gamesApproved:'true'};
  Object.defineProperty(globalThis,'document',{value:{body:{dataset:account}},configurable:true});
  t.after(()=>{if(original)Object.defineProperty(globalThis,'document',original);else delete globalThis.document;});
  let calls=0;const read=async()=>{calls++;return Response.json({id:7,can_access_games:true});};
  const signal=new AbortController().signal;
  for(const path of ['/browse/games','/user/alex/games','/user/alex/stats/games'])
    assert.equal(await authorizeGameSnapshot('7',signal,read,new URL(path,'https://example.test')),true);
  assert.equal(calls,0);
  assert.equal(await authorizeGameSnapshot('7',signal,read,new URL('/user/other/games','https://example.test')),true);
  assert.equal(calls,1,'other profile privacy is still checked');
  account.gamesApproved='false';assert.equal(await authorizeGameSnapshot('7',signal,read),false);assert.equal(calls,1);
});

test('network failures, redirects and navigation cancellation never authorize stale game content',async()=>{
  const controller=new AbortController();
  assert.equal(await authorizeGameSnapshot('7',controller.signal,async()=>{throw new Error('offline');}),false);
  assert.equal(await authorizeGameSnapshot('7',controller.signal,async()=>new Response('',{status:403})),false);
  assert.equal(await authorizeGameSnapshot('7',controller.signal,async()=>({ok:true,redirected:true})),false);
  assert.equal(await authorizeGameSnapshot('7',controller.signal,async()=>{controller.abort();return Response.json({id:7,can_access_games:true});}),false);
});

test('cached Games list and statistics also recheck the target profile visibility',async()=>{
  const signal=new AbortController().signal;
  for(const path of ['/user/other%20viewer/games','/user/other%20viewer/stats/games','/user/other%20viewer/favorites']){
    for(const visible of [true,false]){
      const calls=[];
      const allowed=await authorizeGameSnapshot('7',signal,async(url,options)=>{
        calls.push(url);assert.equal(options.cache,'no-store');
        return url==='/api/proxy/auth/me'?Response.json({id:7,can_access_games:true}):new Response('',{status:visible?200:403});
      },new URL(path,'https://example.test'));
      assert.equal(allowed,visible);
      assert.deepEqual(calls,['/api/proxy/auth/me','/api/proxy/tracking/people/other%20viewer/header']);
    }
  }
});

const runtime=readFileSync(new URL('../src/components/NavigationRuntime.astro',import.meta.url),'utf8');
test('mixed pages are remembered with an access requirement instead of excluded by game links',()=>{
  const start=runtime.indexOf('  function remember('),end=runtime.indexOf('  function storePrediction(',start);
  const state={user:'7',pages:new Map(),bytes:0};
  const html='<a href="/games/7">Game</a>';
  const context={URL,Blob,Date,state,snapshotHasGames,reusableGamesPage:()=>false,resetForIdentity(){},cacheable:()=>true,keyFor:()=>'/home',
    sanitizedHtml:()=>html,doc:{body:{dataset:{cacheUser:'7'},hasAttribute:()=>false},querySelector:()=>null}};
  runInNewContext(stripTypeScriptTypes(runtime.slice(start,end))+';remember(new URL("https://example.test/home"),doc);',context);
  assert.equal(state.pages.get('/home').requiresGamesAccess,true);
});

test('empty Games tab snapshots still require authorization before reuse',()=>{
  const start=runtime.indexOf('  function remember('),end=runtime.indexOf('  function storePrediction(',start);
  for(const path of ['/browse/games','/user/alex/games','/user/alex/stats/games']){
    const state={user:'7',pages:new Map(),bytes:0};
    const context={URL,Blob,Date,state,snapshotHasGames,reusableGamesPage,resetForIdentity(){},cacheable:()=>true,keyFor:()=>path,
      sanitizedHtml:()=>'<main>No games</main>',doc:{body:{dataset:{cacheUser:'7'},hasAttribute:()=>true},querySelector:()=>null}};
    runInNewContext(stripTypeScriptTypes(runtime.slice(start,end))+`;remember(new URL('https://example.test${path}'),doc);`,context);
    assert.equal(state.pages.get(path).requiresGamesAccess,true);
  }
});

test('cached mixed navigation waits for approval and falls back on revocation, invalidation or viewer changes',async()=>{
  const start=runtime.indexOf("    document.addEventListener('astro:before-preparation'");
  const snippet=runtime.slice(start,runtime.indexOf("    document.addEventListener('astro:after-preparation'",start));
  for(const outcome of ['approved','revoked','invalidated','other-viewer','aborted','ordinary']){
    let authorize,handler,parsed=0,fallback=0;
    const controller=new AbortController(),page={html:'cached',bytes:6,requiresGamesAccess:outcome!=='ordinary'};
    const state={user:'7',pages:new Map([['/home',page]]),generation:1,bytes:6,usedCached:new Set()};
    const context={URL,Date,state,document:{querySelector:()=>null,addEventListener:(_name,callback)=>handler=callback},navigator:{onLine:true},
      closeNavigationDialogs(){},resetForIdentity(){},keyFor:()=>'/home',cacheable:()=>true,approvedGamesPage:()=>false,reusableGamesPage:()=>false,
      currentUser:()=>outcome==='other-viewer'?'8':'7',startProgress(){},
      authorizeGameSnapshot:()=>new Promise(resolve=>authorize=resolve),
      DOMParser:class{parseFromString(){parsed++;return 'parsed';}},
    };
    runInNewContext(stripTypeScriptTypes(snippet),context);
    const event={to:new URL('https://example.test/home'),signal:controller.signal,loader:async()=>fallback++};
    handler(event);const pending=event.loader();
    if(outcome!=='ordinary')assert.equal(parsed,0,'authorization must precede parsing/painting');
    if(outcome==='invalidated')state.generation++;
    if(outcome==='aborted')controller.abort();
    authorize?.(outcome!=='revoked');await pending;
    const allowed=['approved','ordinary'].includes(outcome);
    assert.equal(parsed,Number(allowed),outcome);
    assert.equal(fallback,Number(!allowed&&outcome!=='aborted'),outcome);
    assert.equal(state.usedCached.size,Number(allowed),outcome);
  }
});

test('speculative mixed HTML goes through the same authorization before fetch interception can install it',async()=>{
  const start=runtime.indexOf('    const navigationFetch:typeof fetch=');
  const snippet=runtime.slice(start,runtime.indexOf('    window.fetch = mutationFetch',start));
  for(const approved of [true,false]){
    const controller=new AbortController(),page={html:'cached',bytes:6,predicted:true,storedAt:Date.now(),requiresGamesAccess:true};
    const target={key:'/home',signal:controller.signal,generation:1},calls=[];
    const state={user:'7',pages:new Map([['/home',page]]),generation:1,bytes:6,predictionNavigation:target,
      originalFetch:async(input)=>{calls.push(input);return String(input).includes('/auth/me')?Response.json({id:7,can_access_games:approved}):new Response('fresh');}};
    const context={URL,Date,Request,Response,state,authorizeGameSnapshot,keyFor:()=>'/home',location:{href:'https://example.test/home'}};
    const fetcher=runInNewContext(stripTypeScriptTypes(snippet)+';navigationFetch;',context);
    const response=await fetcher('/home',{signal:controller.signal});
    assert.equal(await response.text(),approved?'cached':'fresh');
    assert.equal(calls[0],'/api/proxy/auth/me');
    assert.equal(calls.length,approved?1:2);
  }
});
