import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source=readFileSync(new URL('../src/lib/profile-shell.ts',import.meta.url),'utf8');
const warming=source.slice(source.indexOf('  function queueWarm()'),source.indexOf("  document.addEventListener('astro:before-preparation'"));
const settle=()=>new Promise(setImmediate);

function fixture(owner=false,allowed=true){
  const frames=[],timers=[],requests=[],prepared=[];
  const controller=new AbortController();
  const links=['overview','stats','list','games','favorites','social'].map(key=>({key,url:`https://example.test/user/alex/${key}`}));
  const context={owner,initialReady:false,warming:false,warmFrame:0,warmTimer:0,leaving:false,signal:controller.signal,warmRequest:undefined,
    document:{hidden:false},nav:{busy:false,hasAttribute(){return this.busy;},querySelectorAll:()=>links},
    requestAnimationFrame:callback=>frames.push(callback),window:{setTimeout:callback=>timers.push(callback)},
    active:'overview',panels:new Map([['overview',{ready:true}]]),attempted:new Set(),savedPanels:undefined,
    returnViews:{get:()=>undefined},returnViewKey:url=>url.href,target:link=>link,predictionAllowed:()=>allowed,URL,AbortController,
    savePanel(){},forget(){},
    read:(key,_url,signal)=>{
      if(context.panels.has(key))return Promise.resolve(context.panels.get(key));
      return new Promise((resolve,reject)=>{
        const request={key,signal,resolve:()=>{const panel={ready:false};context.panels.set(key,panel);resolve(panel);}};
        signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
        requests.push(request);
      });
    },
    prepare:async panel=>{prepared.push(panel);panel.ready=true;},
  };
  runInNewContext(stripTypeScriptTypes(warming)+';this.queueWarm=queueWarm;',context);
  return {context,requests,prepared,frames,timers,controller,
    tick(){frames.shift()?.();timers.shift()?.();}};
}

for(const owner of [true,false])test(`${owner?'own':'visitor'} profile warms sequentially after initialization and a paint, with Stats last`,async()=>{
  const f=fixture(owner);f.context.queueWarm();assert.equal(f.frames.length,0);
  f.context.initialReady=true;f.context.queueWarm();
  assert.equal(f.requests.length,0);assert.equal(f.frames.length,1);
  for(const key of ['list','games','favorites','social','stats']){
    f.tick();
    const request=f.requests.at(-1);assert.equal(request.key,key);
    const count=f.requests.length;
    f.context.queueWarm();f.tick();
    assert.equal(f.requests.length,count,'pending background work cannot start a second request');
    request.resolve();await settle();
    assert.equal(f.context.panels.get(key).ready,true);
  }
  f.tick();assert.equal(f.requests.length,5);assert.equal(f.prepared.length,5);
});

for(const blocked of ['hidden','busy','leaving','aborted'])test(`warming pauses while ${blocked}`,()=>{
  const f=fixture();f.context.initialReady=true;
  if(blocked==='hidden')f.context.document.hidden=true;
  if(blocked==='busy')f.context.nav.busy=true;
  if(blocked==='leaving')f.context.leaving=true;
  if(blocked==='aborted')f.controller.abort();
  f.context.queueWarm();f.tick();assert.equal(f.requests.length,0);
});

test('Data Saver or a slow connection prevents speculative network requests',()=>{
  const f=fixture(false,false);f.context.initialReady=true;
  f.context.queueWarm();f.tick();assert.equal(f.requests.length,0);
});

test('a saved Stats tab still waits for lighter tabs',()=>{
  const f=fixture(true);f.context.initialReady=true;
  f.context.savedPanels={get:key=>key.endsWith('/stats')?{}:undefined};
  f.context.queueWarm();f.tick();assert.equal(f.requests[0].key,'list');
});

test('an existing panel can be prepared without network loading on a restricted connection',async()=>{
  const f=fixture(false,false);f.context.initialReady=true;
  f.context.panels.set('list',{ready:false});
  f.context.queueWarm();f.tick();await settle();
  assert.equal(f.requests.length,0);assert.equal(f.context.panels.get('list').ready,true);
});

test('hiding during a read postpones preparation until the page becomes visible',async()=>{
  const f=fixture();f.context.initialReady=true;f.context.queueWarm();f.tick();
  f.context.document.hidden=true;f.requests[0].resolve();await settle();
  assert.equal(f.prepared.length,0);assert.equal(f.context.attempted.has('list'),false);
  f.context.document.hidden=false;f.context.queueWarm();f.tick();await settle();
  assert.equal(f.prepared.length,1);assert.equal(f.requests.length,1,'reuse the completed read');
});

test('cancelled warming releases the queue and permits a later retry',async()=>{
  const f=fixture();f.context.initialReady=true;f.context.queueWarm();f.tick();
  f.context.warmRequest.controller.abort();await settle();
  assert.equal(f.context.warming,false);assert.equal(f.context.attempted.has('list'),false);
  f.tick();assert.equal(f.requests.length,2);
  f.requests[1].resolve();await settle();
  assert.equal(f.context.panels.get('list').ready,true);
});
