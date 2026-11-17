import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import {needsPageContext,resolveViewerContext} from '../src/lib/viewer-context.ts';
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

test('optional notifications coalesce per request and failure does not reject essential page data',async()=>{
  const source=stripTypeScriptTypes(read('../src/lib/viewer-notifications.ts')).replace(/^import .*;$/gm,'').replace('export function','function');
  const requests=[],pending=deferred();
  const load=runInNewContext(source+';viewerNotifications',{tracker:(_,token)=>{requests.push(token);return token==='failed'?Promise.reject(new Error('optional unavailable')):pending.promise;}});
  const locals={token:'a',user:{id:1}};
  assert.equal(load(locals),load(locals));
  assert.equal(await load({user:{id:2},token:'failed'}),null);
  assert.equal(await load({}),null);
  pending.resolve({results:[]});await load(locals);
  assert.deepEqual(requests,['a','failed']);
});

test('page requests select full/essential context or session-only validation',async()=>{
  for(const [pathname,profilePanel,isStaticAsset,expected] of [
    ['/title/1',false,false,'full'],['/user/a',true,false,'essential'],
    ['/login',false,false,'me'],['/register',false,false,'me'],['/logout',false,false,'me'],
    ['/api/proxy/tracking/notifications',false,false,'me'],['/asset.js',false,true,'me'],
  ]){
    const calls=[],context={user:{id:1},settings:{has_rpdb_key:true}};
    const auth={pageContext:async(_,notifications)=>{calls.push(notifications?'full':'essential');return context;},
      me:async()=>{calls.push('me');return context.user;}};
    const result=await resolveViewerContext(auth,'private',{pathname,profilePanel,isStaticAsset});
    assert.deepEqual(calls,[expected]);assert.equal(result.user,context.user);
    assert.equal(result.pageContext,expected==='me'?undefined:context);
    assert.equal(needsPageContext(pathname,isStaticAsset),expected!=='me');
  }
});

test('context failures preserve session validation and propagate actual auth rejection',async()=>{
  const calls=[],auth={pageContext:async()=>{calls.push('context');throw new Error('unavailable');},
    me:async()=>{calls.push('me');return {id:1};}};
  assert.equal((await resolveViewerContext(auth,'a',{pathname:'/browse',isStaticAsset:false})).user.id,1);
  assert.deepEqual(calls,['context','me']);
  auth.me=async()=>{throw new Error('API 401 invalid');};
  await assert.rejects(resolveViewerContext(auth,'a',{pathname:'/browse',isStaticAsset:false}),/API 401/);
});

test('concurrent SSR viewers keep separate context and do not repeat successful authentication',async()=>{
  const waiting=deferred(),first={user:{id:1}},second={user:{id:2}};
  const auth={pageContext:async token=>token==='a'?waiting.promise:second,
    me:async()=>{throw new Error('duplicate authentication');}};
  const a=resolveViewerContext(auth,'a',{pathname:'/browse',isStaticAsset:false});
  assert.equal((await resolveViewerContext(auth,'b',{pathname:'/browse',isStaticAsset:false})).pageContext,second);
  waiting.resolve(first);assert.equal((await a).pageContext,first);
});

test('page context supplies private preferences and profile without repeat API reads',async()=>{
  const profileSource=stripTypeScriptTypes(read('../src/lib/viewer-profile-preferences.ts')).replace(/^import .*;$/gm,'').replace('export function','function');
  const profile=runInNewContext(profileSource+';viewerProfilePreferences',{api:{profile:{get:()=>{throw new Error('duplicate profile read');}}}});
  const value={avatar_url:'/private/1'},locals={token:'a',pageContext:{profile:value}};
  assert.equal(await profile(locals),value);
});
