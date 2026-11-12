import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
const read=path=>readFileSync(new URL(path,import.meta.url),'utf8');
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};

test('SSR profile consumers share one private read per request, including failures and anonymous defaults',async()=>{
  const requests=[];
  const source=stripTypeScriptTypes(read('../src/lib/viewer-profile-preferences.ts')).replace(/^import .*;$/gm,'').replace('export function','function');
  const load=runInNewContext(source+';viewerProfilePreferences',{api:{profile:{get:token=>{
    requests.push(token);return token==='failed'?Promise.reject(new Error('unavailable')):Promise.resolve({avatar_url:token});
  }}}});
  const a={token:'a'},b={token:'b'},failed={token:'failed'};
  assert.equal(load(a),load(a));assert.equal((await load(b)).avatar_url,'b');
  assert.equal((await load(a)).avatar_url,'a');
  assert.equal(await load(failed),null);assert.equal(await load(failed),null);
  assert.equal(await load({}),null);assert.deepEqual(requests,['a','b','failed']);
});

test('middleware starts preferences and profile independently of settings before rendering pages',async()=>{
  const source=read('../src/middleware.ts');
  const start=source.indexOf('  if (context.locals.user && token && !isStaticAsset');
  const snippet=source.slice(start,source.indexOf('  const response = await next();',start));
  const calls=[],settings=deferred(),media=deferred(),profile=deferred();
  const context={locals:{user:{id:1}},cookies:{set(){}}};
  const pending=runInNewContext('(async()=>{'+stripTypeScriptTypes(snippet)+'})()',{
    context,token:'private',isStaticAsset:false,pathname:'/browse',
    viewerMediaPreferences:()=>{calls.push('media');return media.promise;},
    viewerProfilePreferences:()=>{calls.push('profile');return profile.promise;},
    api:{auth:{getSettings:()=>{calls.push('settings');return settings.promise;}}},
    displayPreferenceCookies:()=>[],
  });
  assert.deepEqual(calls,['media','profile','settings']);
  settings.resolve({has_rpdb_key:false});await pending;
  assert.equal(context.locals.hasRpdbKey,false);
  media.resolve({});profile.resolve({});
});

test('app bar starts notifications while its shared preferences read is still pending',async()=>{
  const source=read('../src/components/AppBar.astro');
  const snippet=stripTypeScriptTypes(source.slice(3,source.indexOf('const notificationTotal'))).replace(/^import .*;$/gm,'');
  const media=deferred(),calls=[];
  const pending=runInNewContext('(async()=>{'+snippet+'})()',{
    Astro:{locals:{user:{username:'a'},token:'private'},props:{},url:{pathname:'/title/1'}},
    viewerMediaPreferences:()=>media.promise,
    viewerProfilePreferences:()=>{calls.push('profile');return Promise.resolve(null);},
    tracker:path=>{calls.push(path);return Promise.resolve({results:[]});},
  });
  assert.deepEqual(calls,['recent-events','profile']);
  media.resolve({combine_lists:true});await pending;
});
