import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import {isSameOrigin,requiresSameOrigin} from '../src/lib/request-security.ts';
import {profilePanelRequest,profilePanelHeader,profilePagePath} from '../src/lib/profile-panel-request.ts';
import {anonymousGameDiscoveryRoute,approvedGamesPage} from '../src/lib/game-public-routes.ts';
import {GET,POST} from '../src/pages/api/proxy/[...path].ts';

function middleware(guestNavigation=false){
  const source=stripTypeScriptTypes(readFileSync(new URL('../src/middleware.ts',import.meta.url),'utf8'))
    .replace(/^import .*;\r?$/gm,'').replace('export const onRequest','const onRequest');
  return runInNewContext(source+';onRequest',{
    defineMiddleware:fn=>fn,sessionCookieName:()=> 'selected_token',
    crawlerShareResponse:async()=>null,publicLegalPage:()=>false,shareLoginPath:()=>'/login',
    isSameOrigin,requiresSameOrigin,Response,URL,process:{env:{}},
    profilePanelRequest,profilePanelHeader,profilePagePath,anonymousGameDiscoveryRoute,approvedGamesPage,
    api:{auth:{me:()=>{throw new Error('Redundant auth preflight');}},
      profile:{publicAccessStatus:async()=>({enable_logged_out_navigation:guestNavigation})}},
  });
}
function context(path,method='GET',origin='https://example.test',token='session'){
  const url=new URL('/api/proxy/'+path,'https://example.test');
  return {url,request:new Request(url,{method,headers:origin?{Origin:origin}:{}}),
    params:{path},locals:{},cookies:{get:name=>name==='selected_token'&&token?{value:token}:undefined},
    redirect:()=>{throw new Error('API requests must return backend status');}};
}

test('guest contributor requests reach their declared read routes only when guest navigation is enabled',async()=>{
  for(const [method,path,canRead] of [
    ['GET','contributors/person/tmdb%3A42',true],
    ['GET','contributors/person/tmdb%3A42/availability',true],
    ['GET','contributors/person/catalogue:42/availability',true],
    ['GET','contributors/person/catalogue:42/images',true],
    ['GET','contributors/person/catalogue:42/series/7/episode-credits',true],
    ['GET','contributors/studio/catalogue:42/availability',false],
    ['POST','contributors/person/catalogue:42/availability',false],
  ]){
    for(const enabled of [false,true]){
      const ctx=context(`tracking/${path}`,method,'https://example.test',null);
      ctx.redirect=(path,status)=>new Response(null,{status,headers:{Location:path}});
      const response=await middleware(enabled)(ctx,()=>Response.json({ok:true}));
      assert.equal(response.status,enabled&&canRead?200:302,`${method} ${path}, guest navigation ${enabled}`);
    }
  }
});

test('all Games API reads reach backend once without a separate account preflight',async t=>{
  const paths=['tracking/games/browse','tracking/games/users/alex/list',
    'tracking/games/users/alex/statistics','tracking/games/users/alex/favorites',
    'tracking/games/works/1','catalogue/entities/1/details','auth/me'];
  const calls=[];
  t.mock.method(globalThis,'fetch',async(url,init)=>{
    calls.push(new URL(url).pathname);
    assert.equal(init.headers.get('Authorization'),'Bearer session');
    return Response.json({ok:true});
  });
  const run=middleware();
  for(const path of paths){
    const ctx=context(path);
    const response=await run(ctx,()=>GET(ctx));
    assert.equal(response.status,200);
    assert.equal(response.headers.get('X-Content-Type-Options'),'nosniff');
  }
  assert.deepEqual(calls,paths.map(path=>'/'+path));
});

test('backend rejection, revocation and privacy failures pass through unchanged',async t=>{
  const run=middleware();
  for(const status of [401,403,404]){
    t.mock.method(globalThis,'fetch',async()=>Response.json({detail:'unavailable'},{status}));
    const ctx=context('tracking/games/works/1');
    const response=await run(ctx,()=>GET(ctx));
    assert.equal(response.status,status);
    assert.deepEqual(await response.json(),{detail:'unavailable'});
  }
});

test('cookie Games writes retain CSRF checks before proxy handoff',async t=>{
  let calls=0;
  t.mock.method(globalThis,'fetch',async()=>{calls++;return new Response(null,{status:204});});
  const run=middleware();
  for(const origin of ['https://evil.test',null]){
    const ctx=context('tracking/games/entries/1','POST',origin);
    assert.equal((await run(ctx,()=>POST(ctx))).status,403);
  }
  assert.equal(calls,0);
  const ctx=context('tracking/games/entries/1','POST');
  assert.equal((await run(ctx,()=>POST(ctx))).status,204);
  assert.equal(calls,1);
});

test('all authenticated navigation layouts carry the same account approval evidence',()=>{
  for(const name of ['Tracker','TrackerSettings','Base']){
    const source=readFileSync(new URL(`../src/layouts/${name}.astro`,import.meta.url),'utf8');
    assert.match(source,/data-cache-user=/);
    assert.match(source,/data-cache-username=/);
    assert.match(source,/data-games-approved=\{String\((?:Astro.locals.user|user)\?\.can_access_games===true\)\}/);
  }
});
