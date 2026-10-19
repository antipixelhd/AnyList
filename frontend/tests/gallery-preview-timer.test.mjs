import assert from 'node:assert/strict';
import test from 'node:test';
import {createGalleryPreviewTimer,galleryPreviewImagesSettled} from '../src/lib/gallery-preview-timer.ts';

test('requested thumbnails wait for load/error, without waiting on deferred offscreen images',()=>{
  const image=(src,complete)=>({getAttribute:()=>src,complete});
  assert.equal(galleryPreviewImagesSettled([image(null,false),image('/cached.jpg',true)]),true);
  assert.equal(galleryPreviewImagesSettled([image('/pending.jpg',false)]),false);
  assert.equal(galleryPreviewImagesSettled([image('/failed.jpg',true)]),true);
});

function gallery(t) {
  t.mock.timers.enable({apis:['setTimeout']});
  const state={ready:false,hidden:false};
  const controller=new AbortController();
  const controls=createGalleryPreviewTimer(()=>state.ready,()=>{state.hidden=false;},()=>{state.hidden=true;},controller.signal);
  return {state,controls,controller,tick:ms=>t.mock.timers.tick(ms)};
}

test('slow image loading gets the full idle interval only after readiness',t=>{
  const g=gallery(t);g.controls.reveal();g.tick(10000);
  assert.equal(g.state.hidden,false);
  g.state.ready=true;g.controls.refresh();g.tick(1999);assert.equal(g.state.hidden,false);
  g.tick(1);assert.equal(g.state.hidden,true);
});

test('another thumbnail starting to load cancels an already-running countdown',t=>{
  const g=gallery(t);g.state.ready=true;g.controls.reveal();g.tick(1200);
  g.state.ready=false;g.controls.refresh();g.tick(5000);assert.equal(g.state.hidden,false);
  g.state.ready=true;g.controls.refresh();g.tick(1999);assert.equal(g.state.hidden,false);
  g.tick(1);assert.equal(g.state.hidden,true);
});

test('loading controls cannot be manually hidden, while ready controls can',t=>{
  const g=gallery(t);g.controls.reveal();g.controls.hide();assert.equal(g.state.hidden,false);
  g.state.ready=true;g.controls.hide();assert.equal(g.state.hidden,true);
  g.controls.refresh();g.tick(5000);assert.equal(g.state.hidden,true);
});

test('closing or navigation cancels a countdown and reopening starts fresh',t=>{
  const g=gallery(t);g.state.ready=true;g.controls.reveal();g.tick(1000);g.controls.reset();g.tick(5000);
  assert.equal(g.state.hidden,false);
  g.controls.reveal();g.tick(1999);assert.equal(g.state.hidden,false);
  g.controller.abort();g.tick(5000);assert.equal(g.state.hidden,false);
});

test('readiness is checked again when the timer expires',t=>{
  const g=gallery(t);g.state.ready=true;g.controls.reveal();g.state.ready=false;g.tick(2000);
  assert.equal(g.state.hidden,false);
  g.state.ready=true;g.controls.refresh();g.tick(2000);assert.equal(g.state.hidden,true);
});

test('ready scroll and animation refreshes do not extend the idle deadline',t=>{
  const g=gallery(t);g.state.ready=true;g.controls.reveal();
  for(let i=0;i<3;i++){g.tick(500);g.controls.refresh();}
  g.tick(499);g.controls.refresh();assert.equal(g.state.hidden,false);
  g.tick(1);assert.equal(g.state.hidden,true);
});

test('fresh wheel, pointer or selection activity starts a full new idle interval',t=>{
  const g=gallery(t);g.state.ready=true;g.controls.reveal();g.tick(700);
  g.controls.reveal();g.tick(1999);assert.equal(g.state.hidden,false);
  g.tick(1);assert.equal(g.state.hidden,true);
});

test('gallery scroll events preserve the deadline while real wheel events restart it',async t=>{
  const {readFileSync}=await import('node:fs');
  const vm=await import('node:vm');
  const source=readFileSync(new URL('../src/lib/media-detail-images.ts',import.meta.url),'utf8');
  const start=source.indexOf("  slots.addEventListener('scroll'");
  const bindings=source.slice(start,source.indexOf("  mobileViewer.addEventListener('change'",start));
  const g=gallery(t),handlers={};g.state.ready=true;
  vm.runInNewContext(bindings,{slots:{addEventListener:(name,callback)=>{handlers[name]=callback;}},refreshPreviewLoading:g.controls.refresh,revealPreviews:g.controls.reveal,signal:g.controller.signal});
  g.controls.reveal();g.tick(600);handlers.scroll();g.tick(1399);assert.equal(g.state.hidden,false);
  g.tick(1);assert.equal(g.state.hidden,true);
  g.controls.reveal();g.tick(1200);handlers.wheel();g.tick(1999);assert.equal(g.state.hidden,false);
  g.tick(1);assert.equal(g.state.hidden,true);
});
