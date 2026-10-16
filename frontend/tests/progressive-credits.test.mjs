import test from 'node:test';
import assert from 'node:assert/strict';
import {setTimeout as tick} from 'node:timers/promises';
import {createProgressiveCredits} from '../src/lib/progressive-credits.ts';

function fixture() {
  const previous = {document:globalThis.document, observer:globalThis.IntersectionObserver};
  let observer, visible=[];
  globalThis.document={createElement:()=>({dataset:{},style:{},setAttribute(){}})};
  globalThis.IntersectionObserver=class {
    constructor(callback){this.callback=callback;observer=this;}
    observe(target){this.target=target;}
    unobserve(){this.target=undefined;}
    disconnect(){this.disconnected=true;}
    reach(){this.callback([{isIntersecting:true}]);}
  };
  const controller=new AbortController();
  const container={append(node){this.sentinel=node;}};
  const list=createProgressiveCredits(container,controller.signal,rows=>visible=rows);
  return {list,container,controller,get observer(){return observer;},get visible(){return visible;},
    restore(){controller.abort();globalThis.document=previous.document;globalThis.IntersectionObserver=previous.observer;}};
}

test('every cached credit remains reachable in order without building offscreen cards on expansion',async()=>{
  const f=fixture();
  try{
    const rows=Array.from({length:97},(_,id)=>({id}));
    f.list.replace(rows);assert.equal(f.visible.length,0);
    f.list.toggle(true);assert.deepEqual(f.visible,rows.slice(0,32));
    for(const count of [64,96,97]){f.observer.reach();f.observer.reach();await tick(5);assert.equal(f.visible.length,count);}
    assert.deepEqual(f.visible,rows);assert.equal(f.container.sentinel.hidden,true);
  }finally{f.restore();}
});

test('collapse and navigation cancel pending batches and retain already visited cards',async()=>{
  const f=fixture();
  try{
    f.list.replace(Array.from({length:100},(_,i)=>i));f.list.toggle(true);
    f.observer.reach();f.list.toggle(false);await tick(5);assert.equal(f.visible.length,32);
    f.list.toggle(true);assert.equal(f.visible.length,32);
    f.observer.reach();f.controller.abort();await tick(5);assert.equal(f.visible.length,32);
    assert(f.observer.disconnected);
  }finally{f.restore();}
});

test('metadata updates reconcile the visited range without exposing the entire refreshed list',()=>{
  const f=fixture();
  try{
    f.list.replace(Array.from({length:100},(_,i)=>i));f.list.toggle(true);
    const next=Array.from({length:200},(_,i)=>i+10);f.list.replace(next);
    assert.deepEqual(f.visible,next.slice(0,32));
    f.list.replace([8,9]);assert.deepEqual(f.visible,[8,9]);assert(f.container.sentinel.hidden);
  }finally{f.restore();}
});
