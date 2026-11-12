import assert from 'node:assert/strict';
import test from 'node:test';
import {preloadDetailGallery} from '../src/lib/detail-gallery-preload.ts';

test('bulk downloads wait for selection, then preload every image nearest-first without a count cap',async()=>{
  let finish;
  const selected=new Promise(resolve=>{finish=resolve;}),started=[];
  const pending=preloadDetailGallery(7,36,index=>{started.push(index);return index===7?selected:undefined;},()=>true);
  assert.deepEqual(started,[7]);await Promise.resolve();assert.deepEqual(started,[7]);
  finish();await pending;
  assert.deepEqual(started.slice(0,5),[7,8,6,9,5]);
  assert.equal(started.length,36);assert.equal(new Set(started).size,36);
});

test('new selections bypass the head start while the first ready image releases the complete gallery',async()=>{
  const finish=[],started=[],pending=new Map();
  const prepare=index=>{
    if(!pending.has(index)){
      started.push(index);pending.set(index,new Promise(resolve=>{finish[index]=resolve;}));
    }
    return pending.get(index);
  };
  const first=preloadDetailGallery(0,4,prepare,()=>true);
  const next=preloadDetailGallery(2,4,prepare,()=>true);
  assert.deepEqual(started,[0,2]);finish[0]();await first;
  assert.deepEqual(started,[0,2,1,3]);
  finish[2]();await next;assert.deepEqual(started,[0,2,1,3]);
});

test('closed viewers, changed groups and aborted pages do not admit delayed gallery downloads',async()=>{
  for(const reason of ['closed','changed','aborted']){
    let finish,valid=true;const started=[];
    const pending=preloadDetailGallery(0,5,index=>{started.push(index);return new Promise(resolve=>{finish=resolve;});},()=>valid);
    valid=false;finish();await pending;assert.deepEqual(started,[0],reason);
  }
});

test('a failed selected image does not strand the remaining gallery',async()=>{
  const started=[];
  await preloadDetailGallery(0,3,index=>{started.push(index);return index===0?Promise.reject(Error('Unavailable')):undefined;},()=>true);
  assert.deepEqual(started,[0,1,2]);
});
