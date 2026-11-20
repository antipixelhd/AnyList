import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';

const source=stripTypeScriptTypes(readFileSync(new URL('../src/lib/notification-store.ts',import.meta.url),'utf8'))
  .replace(/^import .*;$/gm,'').replace(/^export /gm,'');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function environment({seed,fetch:respond}={}) {
  const document=new EventTarget();document.body={dataset:{cacheUser:'1'}};
  document.querySelector=()=>seed;
  const lifecycles=[],requests=[];
  const api=runInNewContext(source+';({subscribeNotifications,refreshNotifications})',{
    document,AbortController,CustomEvent,setTimeout,clearTimeout,console,
    startVisibleRefresh:options=>{
      lifecycles.push(options);
      if(options.immediate)void options.refresh(new AbortController().signal);
      return {stop(){}};
    },
    fetch:(url,options)=>{
      requests.push({url,...options});
      return respond?respond(url,options):Promise.resolve({ok:true,json:async()=>({results:[{id:'fresh'}],pending:1,outbound:[{media_id:8}]})});
    },
  });
  return {...api,document,lifecycles,requests};
}
const seedFor=user=>({textContent:JSON.stringify({user,snapshot:{results:[{id:'seed'}],pending:1,outbound:[]}})});

test('badges and prompts share the SSR snapshot without an immediate duplicate fetch',async()=>{
  const env=environment({seed:seedFor('1')}),seen=[];
  const a=env.subscribeNotifications(value=>seen.push(value));
  const b=env.subscribeNotifications(value=>seen.push(value));
  await flush();assert.equal(env.requests.length,0);assert.equal(seen.length,2);
  assert.equal(env.lifecycles[0].wakeIntervalMs,15000);
  await env.lifecycles[0].refresh(new AbortController().signal);
  assert.equal(env.requests.length,1);
  assert.equal(env.requests[0].url,'/api/proxy/tracking/notifications');
  a();b();
});

test('opening the inbox upgrades to the full feed without clearing its operational queue',async()=>{
  const env=environment({seed:seedFor('1')}),badge=[],inbox=[];
  const a=env.subscribeNotifications(value=>badge.push(value));
  const b=env.subscribeNotifications(value=>inbox.push(value),{full:true});
  assert.equal(inbox.length,0);
  await flush();assert.equal(env.requests.length,1);
  assert.equal(env.requests[0].url,'/api/proxy/tracking/recent-events');
  assert.equal(inbox[0].outbound[0].media_id,8);
  b();await env.refreshNotifications({force:true});
  assert.equal(env.requests.at(-1).url,'/api/proxy/tracking/notifications');a();
});

test('an inbox subscription supersedes an in-flight lightweight response',async()=>{
  const pending=[];
  const env=environment({fetch:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}))});
  const badge=env.subscribeNotifications(()=>{}),seen=[];
  const inbox=env.subscribeNotifications(value=>seen.push(value),{full:true});
  assert.equal(pending.length,2);assert.equal(pending[0].options.signal.aborted,true);
  pending[1].resolve({ok:true,json:async()=>({results:[],outbound:[{media_id:9}]})});
  await flush();
  pending[0].resolve({ok:true,json:async()=>({results:[{id:'old'}],outbound:[]})});
  await flush();assert.equal(seen.length,1);assert.equal(seen[0].outbound[0].media_id,9);
  badge();inbox();
});

test('SSR data from a different viewer is ignored and session changes reject late responses',async()=>{
  const pending=[];
  const env=environment({seed:seedFor('2'),fetch:(url,options)=>new Promise(resolve=>pending.push({options,resolve}))});
  const seen=[],a=env.subscribeNotifications(value=>seen.push(value));
  assert.equal(seen.length,0);assert.equal(pending.length,1);
  env.document.body.dataset.cacheUser='2';
  const b=env.subscribeNotifications(value=>seen.push(value));
  const fresh=env.refreshNotifications();
  pending[1].resolve({ok:true,json:async()=>({results:[{id:'new-viewer'}]})});
  await fresh;
  pending[0].resolve({ok:true,json:async()=>({results:[{id:'private-old'}]})});
  await flush();assert(seen.every(value=>value.results[0].id==='new-viewer'));
  a();b();
});
