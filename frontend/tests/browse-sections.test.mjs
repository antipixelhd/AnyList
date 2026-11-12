import assert from 'node:assert/strict';
import test from 'node:test';
import {readBrowseSections} from '../src/lib/browse-sections.ts';

test('shelves paint as chunks arrive, retain fixed indices and return page order',async()=>{
  let feed;const painted=[];
  const body=new ReadableStream({start(controller){feed=controller;}});
  const encode=new TextEncoder(), request=new AbortController();
  const complete=readBrowseSections('browse/sections?media_type=series',request.signal,
    (index,section)=>painted.push([index,section.title]),async()=>new Response(body));
  feed.enqueue(encode.encode('{"index":2,"section":{"title":"Popular"}}\n{"index":0,"section":{"title":"Trend'));
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.deepEqual(painted,[[2,'Popular']]);
  feed.enqueue(encode.encode('ing"}}\n{"index":3,"section":{"title":"Rated"}}\n'));
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.deepEqual(painted,[[2,'Popular'],[0,'Trending'],[3,'Rated']]);
  feed.enqueue(encode.encode('{"index":1,"section":{"title":"Newest"}}\n'));feed.close();
  assert.deepEqual((await complete).sections.map(section=>section.title),['Trending','Newest','Popular','Rated']);
});

test('cancelled streams cannot paint subsequent shelf chunks',async()=>{
  let feed;const request=new AbortController(), painted=[];
  const body=new ReadableStream({start(controller){feed=controller;}});
  const complete=readBrowseSections('browse/sections?media_type=movie',request.signal,index=>painted.push(index),async()=>new Response(body));
  request.abort();feed.enqueue(new TextEncoder().encode('{"index":0,"section":{}}\n'));
  await assert.rejects(complete,{name:'AbortError'});assert.deepEqual(painted,[]);
});

test('truncated or failed streams retain delivered shelves and reject incomplete cache entries',async()=>{
  for(const tail of ['', '{"error":"Provider unavailable"}\n']){
    const painted=[];
    const complete=readBrowseSections('browse/sections?media_type=series',new AbortController().signal,index=>painted.push(index),
      async()=>new Response('{"index":0,"section":{}}\n'+tail));
    await assert.rejects(complete);assert.deepEqual(painted,[0]);
  }
});
