import assert from 'node:assert/strict';
import test from 'node:test';
import {resolveTitleId} from '../src/lib/title-discovery.ts';

test('title resolution carries page cancellation and rejects invalid destinations',async()=>{
  const controller=new AbortController();
  let request;
  const id=await resolveTitleId('series',42,controller.signal,async(url,options)=>{
    request={url,...options};return {ok:true,json:async()=>({id:7})};
  });
  assert.equal(id,7);
  assert.deepEqual(request,{url:'/api/proxy/tracking/catalog/series/42',method:'POST',signal:controller.signal});
  for(const data of [{id:0},{id:-1},{id:'7'},{id:1.5},null]){
    await assert.rejects(resolveTitleId('movie',42,controller.signal,async()=>({ok:true,json:async()=>data})),/Could not load/);
  }
  await assert.rejects(resolveTitleId('movie',42,controller.signal,async()=>({ok:false,json:async()=>({id:7})})),/Could not load/);
});
