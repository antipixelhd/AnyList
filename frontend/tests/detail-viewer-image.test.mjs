import assert from 'node:assert/strict';
import test from 'node:test';
import {createDetailViewerImageLoader} from '../src/lib/detail-viewer-image.ts';

function slide(cached=false) {
  let resolve,reject,decodes=0,sources=0;
  const image={complete:cached,naturalWidth:cached?1280:0,style:{},
    set src(value){this.source=value;sources++;},
    decode(){decodes++;return new Promise((yes,no)=>{resolve=yes;reject=no;});},
  };
  const skeleton={hidden:false,style:{},dataset:{},setAttribute(name,value){this[name]=value;}};
  return {image,skeleton,finish:()=>{image.complete=true;image.naturalWidth=1280;resolve();},
    fail:()=>reject(new Error('Image unavailable')),decodes:()=>decodes,sources:()=>sources};
}

test('an exposed slide keeps its own load when another slide is selected',async()=>{
  const load=createDetailViewerImageLoader(new AbortController().signal),a=slide(),b=slide();
  const first=load(a.image,a.skeleton,'first','First');
  const second=load(b.image,b.skeleton,'second','Second');
  assert.equal(a.image.source,'first');assert.equal(a.skeleton.hidden,false);
  a.finish();assert.equal(await first,true);assert.equal(a.skeleton.hidden,true);
  assert.equal(b.skeleton.hidden,false);
  b.finish();assert.equal(await second,true);assert.equal(b.skeleton.hidden,true);
  assert.equal(load(a.image,a.skeleton,'first','First again'),first);
  assert.equal(a.decodes(),1);assert.equal(a.sources(),1);
});

test('a cached image appears synchronously without resetting its skeleton on return',async()=>{
  const load=createDetailViewerImageLoader(new AbortController().signal),a=slide(true);
  const ready=load(a.image,a.skeleton,'first','First');
  assert.equal(a.skeleton.hidden,true);assert.equal(a.image.style.visibility,'');
  assert.equal(await ready,true);assert.equal(a.decodes(),0);
  load(a.image,a.skeleton,'first','First again');assert.equal(a.sources(),1);
});

test('background gallery images decode once and stay visible when selected later',async()=>{
  const load=createDetailViewerImageLoader(new AbortController().signal),a=slide();
  const warm=load(a.image,a.skeleton,'next','Next','low');
  assert.equal(a.image.fetchPriority,'low');
  a.finish();await warm;
  const selected=load(a.image,a.skeleton,'next','Next');
  assert.equal(selected,warm);
  assert.equal(a.skeleton.hidden,true);
  assert.equal(a.image.style.visibility,'');
  assert.equal(a.decodes(),1);assert.equal(a.sources(),1);
});

test('page teardown prevents a late decode from revealing an image',async()=>{
  const controller=new AbortController(),load=createDetailViewerImageLoader(controller.signal),a=slide();
  const ready=load(a.image,a.skeleton,'first','First');controller.abort();a.finish();
  assert.equal(await ready,false);assert.equal(a.skeleton.hidden,false);
  const b=slide();assert.equal(await load(b.image,b.skeleton,'second','Second'),false);assert.equal(b.sources(),0);
});

test('failed loads propagate to the gallery without displaying a broken image',async()=>{
  const load=createDetailViewerImageLoader(new AbortController().signal),a=slide();
  const ready=load(a.image,a.skeleton,'first','First');a.fail();
  await assert.rejects(ready,/Image unavailable/);assert.equal(a.image.style.visibility,'hidden');
});

test('a stale failed decode cannot remove or hide a replacement image',async()=>{
  const decoders=[];
  const image={complete:false,naturalWidth:1280,style:{},decode:()=>new Promise((resolve,reject)=>decoders.push({resolve,reject}))};
  const skeleton={hidden:false,style:{},dataset:{},setAttribute(name,value){this[name]=value;}},load=createDetailViewerImageLoader(new AbortController().signal);
  const old=load(image,skeleton,'old','Old'),replacement=load(image,skeleton,'replacement','Replacement');
  decoders[1].resolve();assert.equal(await replacement,true);
  decoders[0].reject(new Error('Stale image failed'));assert.equal(await old,false);
  assert.equal(skeleton.hidden,true);assert.equal(image.style.visibility,'');
});

test('selection promotes an in-flight background request without restarting it',async()=>{
  const load=createDetailViewerImageLoader(new AbortController().signal),a=slide();
  const pending=load(a.image,a.skeleton,'next','Next','low');
  assert.equal(load(a.image,a.skeleton,'next','Next','high'),pending);
  assert.equal(a.image.fetchPriority,'high');assert.equal(a.sources(),1);
  a.finish();await pending;
});

test('an existing decoded preview stays visible until the full slide is ready',async()=>{
  const load=createDetailViewerImageLoader(new AbortController().signal),a=slide();
  const preview={complete:true,naturalWidth:780,currentSrc:'https://images.example/preview.jpg'};
  const pending=load(a.image,a.skeleton,'full','Artwork','high',preview);
  assert.equal(a.skeleton.dataset.viewerPreview,'');
  assert.equal(a.skeleton.style.backgroundImage,'url("https://images.example/preview.jpg")');
  assert.equal(a.skeleton.role,'img');assert.equal(a.skeleton['aria-label'],'Artwork');
  assert.equal(a.image.source,'full');assert.equal(a.sources(),1);
  a.finish();await pending;
  assert.equal(a.skeleton.hidden,true);assert.equal(a.skeleton.dataset.viewerPreview,undefined);
  assert.equal(a.skeleton.style.backgroundImage,'');assert.equal(a.image.style.visibility,'');
});

test('a thumbnail already loading can supply the preview, without starting a second request',async()=>{
  const controller=new AbortController(),load=createDetailViewerImageLoader(controller.signal),a=slide();
  let onload,listeners=0;
  const preview={complete:false,naturalWidth:0,src:'preview',addEventListener(name,callback){assert.equal(name,'load');onload=callback;listeners++;}};
  const pending=load(a.image,a.skeleton,'full','Artwork','low');
  load(a.image,a.skeleton,'full','Artwork','high',preview);
  load(a.image,a.skeleton,'full','Artwork','high',preview);
  assert.equal(listeners,1);assert.equal(a.skeleton.dataset.viewerPreview,undefined);
  preview.complete=true;preview.naturalWidth=180;onload();
  assert.equal(a.skeleton.dataset.viewerPreview,'');assert.equal(a.sources(),1);
  a.finish();await pending;
});

test('late previews cannot replace a newer slide or reveal content after teardown',async()=>{
  const controller=new AbortController(),load=createDetailViewerImageLoader(controller.signal),a=slide();
  let onload;
  const preview={complete:false,naturalWidth:0,src:'old-preview',addEventListener(_name,callback){onload=callback;}};
  const pending=load(a.image,a.skeleton,'old','Old','high',preview);
  controller.abort();preview.complete=true;preview.naturalWidth=180;onload();
  assert.equal(a.skeleton.dataset.viewerPreview,undefined);
  a.finish();assert.equal(await pending,false);
});
