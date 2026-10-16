import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

test('public cache policy shares requests, expires and survives a temporary outage',async()=>{
  let now=0,calls=0,enabled=true,failed=false;
  const source=readFileSync(new URL('../src/lib/image-cache-policy.server.ts',import.meta.url),'utf8');
  const context={process:{env:{}},AbortSignal,Date:{now:()=>now},fetch:async()=>{
    calls++;if(failed)throw new Error('Unavailable');
    return {ok:true,json:async()=>({image_cache_enabled:enabled})};
  }};
  runInNewContext(stripTypeScriptTypes(source.replace(/export /g,''))+';globalThis.policy=imageCacheEnabled;',context);
  const first=context.policy(),second=context.policy();assert.equal(first,second);
  assert.equal(await first,true);assert.equal(await context.policy(),true);assert.equal(calls,1);
  now=31_000;failed=true;assert.equal(await context.policy(),true);assert.equal(calls,2);
  assert.equal(await context.policy(),true);assert.equal(calls,2);
  now+=6000;failed=false;enabled=false;assert.equal(await context.policy(),false);assert.equal(calls,3);
});
