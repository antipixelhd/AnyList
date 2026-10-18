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
    Error,URLSearchParams,AbortController,FormData:class {forEach(){}},
    signal:new AbortController().signal,
    root:{dataset:{endpoint:'people/1'},querySelector:()=>({})},
    form:{addEventListener:(name,handler)=>{handlers[name]=handler;}},
    document:{addEventListener(){}},region,more,data,
    error:{hidden:true},params:new URLSearchParams(),
    createPrediction:()=>({cancel(){},consume:async()=>null}),
    fetch:()=>new Promise((resolve,reject)=>pending.push({resolve,reject})),
    contributorParams:query=>query,
    append:works=>appended.push(...works),showError:message=>errors.push(message),
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
