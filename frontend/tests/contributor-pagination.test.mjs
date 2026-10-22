import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';

test('a failed replacement filter restores pagination and ignores the older page', async () => {
  const source=readFileSync(new URL('../src/lib/contributor-page.ts',import.meta.url),'utf8');
  const pagination=source.slice(source.indexOf("  root.querySelector<HTMLElement>('[data-contributor-next]')"),source.indexOf('  bindPredictivePagination(region'));
  const handlers={},pending=[],appended=[],errors=[];
  const more={disabled:false,textContent:'Load more',addEventListener:(name,handler)=>{handlers[name]=handler;}};
  const region={setAttribute(){},removeAttribute(){}};
  const data={has_more:true,page:1,source:'provider',list_count:null};
  const context=vm.createContext({
    Error,URL,URLSearchParams,AbortController,DOMException,FormData:class {forEach(){}},
    signal:new AbortController().signal,writeVersion:0,gameEdits:new Map(),
    root:{dataset:{endpoint:'people/1'},querySelector:()=>({addEventListener(){}})},
    contributorSourceRefresh:()=>({update(){},stop(){}}),
    form:{action:'https://example.test/person/1',querySelectorAll:()=>[],addEventListener:(name,handler)=>{handlers[name]=handler;}},
    document:{addEventListener(){}},region,more,data,
    error:{hidden:true},params:new URLSearchParams(),
    createPrediction:()=>({cancel(){},consume:async()=>null}),
    fetch:()=>new Promise((resolve,reject)=>pending.push({resolve,reject})),
    contributorParams:query=>query,
    append:works=>appended.push(...works),showError:message=>errors.push(message),
    restoreFilterControls(){},
  });
  vm.runInContext(stripTypeScriptTypes(pagination),context);
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  handlers.click();await flush();
  assert.equal(more.disabled,true);
  handlers.submit({preventDefault(){}});
  pending[1].reject(new Error('Filter failed'));await flush();
  assert.deepEqual(errors,['Filter failed']);
  assert.equal(more.disabled,false);
  assert.equal(more.textContent,'Load more');
  pending[0].resolve({ok:true,json:async()=>({works:['stale'],page:2,has_more:false})});
  await flush();
  assert.deepEqual(appended,[]);
  assert.equal(data.page,1);
  handlers.click();await flush();
  assert.equal(pending.length,3);
  pending[2].resolve({ok:true,json:async()=>({works:['current'],page:2,has_more:false})});
  await flush();
  assert.deepEqual(appended,['current']);
  assert.equal(more.disabled,false);
});

test('a game filter read waits for pending writes and retries stale membership/counts',async()=>{
  const source=readFileSync(new URL('../src/lib/contributor-page.ts',import.meta.url),'utf8');
  const snippet=source.slice(source.indexOf('  const waitForGameWrites='),source.indexOf('  const prediction='));
  const pendingWrites=new Set(),network=[],invalidated=[],document=new EventTarget();
  const context=vm.createContext({document,DOMException,Error,URLSearchParams,Set,Map,Promise,
    root:{querySelector:()=>null},signal:new AbortController().signal,data:{},params:new URLSearchParams(),
    contributorSourceRefresh:()=>({update(){},stop(){}}),
    writeVersion:0,gameEdits:new Map([[8,{}]]),items:new Map(),
    editorStore:{hasPendingSubject:subject=>pendingWrites.has(subject.catalogue_id),invalidateSubject:subject=>invalidated.push(subject.catalogue_id)},
    fetch:()=>new Promise(resolve=>network.push(resolve)),
  });
  vm.runInContext(stripTypeScriptTypes(snippet)+'\nthis.read=requestPage;',context);
  const flush=()=>new Promise(setImmediate),controller=new AbortController();
  const read=context.read('/contributors/actor/1?media_type=game&list_scope=in',controller.signal);
  await flush();assert.equal(network.length,1);
  pendingWrites.add(8);context.writeVersion++;
  network[0]({ok:true,json:async()=>({works:[],list_count:0})});
  await flush();assert.equal(network.length,1,'do not refetch while the write is still pending');
  pendingWrites.delete(8);context.writeVersion++;document.dispatchEvent(new Event('anylist:editor-state'));
  await flush();assert.equal(network.length,2);
  network[1]({ok:true,json:async()=>({works:[{id:8,type:'game',list_status:'playing'}],list_count:1})});
  const result=await read;
  assert.equal(result.list_count,1);assert.equal(result.works[0].id,8);
  assert.deepEqual(invalidated,[8]);
  pendingWrites.add(8);
  const abort=new AbortController(),waiting=context.read('/contributors/actor/1?media_type=game',abort.signal);
  await flush();assert.equal(network.length,2);
  abort.abort();await assert.rejects(waiting,{name:'AbortError'});
});
