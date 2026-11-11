import test from 'node:test';
import assert from 'node:assert/strict';
import {mountRoutePreview,detailHints} from '../src/lib/detail-loading.ts';
import {mountDetailResolution} from '../src/lib/detail-resolution.ts';

const tick=()=>new Promise(setImmediate);
function fixture(t){
  const originals=new Map(['document','location'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  t.after(()=>{for(const [key,value] of originals)if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];});
  const retry=new EventTarget(),nodes=new Map(['title','kind-label','series','secondary-label','poster','backdrop','status'].map(key=>[key,{hidden:true}]));
  nodes.set('retry',retry);
  const frame={dataset:{},classList:{add(){}},remove(){this.removed=true;},querySelector:selector=>nodes.get(selector.match(/data-loading-(.+)\]/)?.[1])};
  let headerHidden=true;
  const header={classList:{contains:()=>headerHidden,remove:()=>headerHidden=false,add:()=>headerHidden=true}};
  const doc=new EventTarget();doc.body={dataset:{cacheUser:'loading-tests'}};doc.documentElement={style:{overflow:'auto'}};
  doc.querySelector=selector=>selector==='[data-appbar]'?header:{content:{firstElementChild:{cloneNode:()=>frame}}};
  const location={href:'https://local.test/discover-game?id=12',origin:'https://local.test',assign(){}};
  Object.defineProperty(globalThis,'document',{configurable:true,value:doc});Object.defineProperty(globalThis,'location',{configurable:true,value:location});
  const host={style:{visibility:'visible'},inert:false,before(node){this.preview=node;},querySelector:()=>frame};
  return {doc,host,frame,retry,nodes,headerHidden:()=>headerHidden};
}

test('pending route frame preserves outgoing geometry and restores navigation, overflow and interaction on cancellation',t=>{
  const f=fixture(t),controller=new AbortController();
  const clear=mountRoutePreview(f.host,{kind:'movie',title:'Destination'},new URL('https://local.test/title/2'),controller.signal);
  assert.equal(f.nodes.get('title').textContent,'Destination');assert.equal(f.host.inert,true);assert.equal(f.host.style.visibility,'hidden');
  assert.equal(f.headerHidden(),false);assert.equal(f.doc.documentElement.style.overflow,'hidden');
  controller.abort();assert.equal(f.frame.removed,true);assert.equal(f.host.inert,false);assert.equal(f.host.style.visibility,'visible');
  assert.equal(f.headerHidden(),true);assert.equal(f.doc.documentElement.style.overflow,'auto');clear();
});

test('an already cancelled route cannot hide the current page',t=>{
  const f=fixture(t),controller=new AbortController();controller.abort();
  mountRoutePreview(f.host,{kind:'game'},new URL('https://local.test/games/2'),controller.signal)();
  assert.equal(f.host.preview,undefined);assert.equal(f.host.inert,false);assert.equal(f.doc.documentElement.style.overflow,'auto');
});

test('resolver shares public identity with its canonical destination and hands progress to navigation',async t=>{
  const f=fixture(t),events=[],controller=new AbortController();
  f.doc.addEventListener('anylist:route-progress-start',event=>events.push(event.type));f.doc.addEventListener('anylist:route-progress-end',event=>events.push(event.type));
  detailHints.scope('loading-tests');detailHints.set(new URL(location.href),{kind:'game',title:'Known game'});
  let requestSignal,navigated;
  mountDetailResolution(f.host,async signal=>{requestSignal=signal;return '/games/88';},async url=>navigated=url,controller.signal);
  await tick();assert.equal(requestSignal,controller.signal);assert.equal(navigated,'/games/88');
  assert.equal(f.nodes.get('title').textContent,'Known game');assert.equal(detailHints.get(new URL('/games/88',location.origin)).title,'Known game');
  assert.deepEqual(events,['anylist:route-progress-start']);controller.abort();
});

test('resolver error permits retry, coalesces repeated clicks and ignores a late aborted result',async t=>{
  const f=fixture(t),controller=new AbortController();let count=0,finish,navigations=0;
  mountDetailResolution(f.host,async()=>{count++;if(count===1)throw new Error('Unavailable');return new Promise(resolve=>finish=resolve);},async()=>navigations++,controller.signal);
  await tick();assert.equal(f.nodes.get('status').textContent,'Unavailable');assert.equal(f.retry.hidden,false);
  f.retry.dispatchEvent(new Event('click'));f.retry.dispatchEvent(new Event('click'));assert.equal(count,2);assert.equal(f.retry.hidden,true);
  controller.abort();finish('/games/99');await tick();assert.equal(navigations,0);
});
