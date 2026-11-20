import test from 'node:test';
import assert from 'node:assert/strict';
import {createBrowseFacetLoader} from '../src/lib/browse-facet-loading.ts';

test('delayed facets share a read and hydrate current choices without blocking other controls',async()=>{
  let release,calls=0,query='old';const controller=new AbortController(),applied=[],busy=[];
  const loader=createBrowseFacetLoader(()=>{calls++;return new Promise(resolve=>release=resolve);},
    data=>applied.push({data,query}),value=>busy.push(value),()=>{},controller.signal);
  const first=loader.load();assert.equal(loader.load(),first);await Promise.resolve();
  query='new';assert.equal(calls,1);assert.deepEqual(applied,[]);
  release({genres:['Adventure']});await first;
  assert.deepEqual(applied,[{data:{genres:['Adventure']},query:'new'}]);assert.deepEqual(busy,[true,false]);
});

test('failed facet acquisition can retry and a departed page ignores late metadata',async()=>{
  const controller=new AbortController(),errors=[],applied=[];let calls=0,release;
  const loader=createBrowseFacetLoader(()=>{
    if(!calls++)throw Error('unavailable');return new Promise(resolve=>release=resolve);
  },data=>applied.push(data),()=>{},failed=>errors.push(failed),controller.signal);
  await loader.load();assert.deepEqual(errors,[false,true]);
  const retry=loader.load();await Promise.resolve();controller.abort();release({genres:[]});await retry;
  assert.equal(calls,2);assert.deepEqual(applied,[]);assert.deepEqual(errors,[false,true,false]);
  await loader.load();assert.equal(calls,2);
});
