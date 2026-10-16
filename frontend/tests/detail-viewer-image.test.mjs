import assert from 'node:assert/strict';
import test from 'node:test';
import {createDetailViewerImageLoader} from '../src/lib/detail-viewer-image.ts';

function slide(cached=false) {
  let resolve,reject,decodes=0,sources=0;
  const image={complete:cached,naturalWidth:cached?1280:0,style:{},
    set src(value){this.source=value;sources++;},
    decode(){decodes++;return new Promise((yes,no)=>{resolve=yes;reject=no;});},
  };
  const skeleton={hidden:false};
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
  const skeleton={hidden:false},load=createDetailViewerImageLoader(new AbortController().signal);
  const old=load(image,skeleton,'old','Old'),replacement=load(image,skeleton,'replacement','Replacement');
  decoders[1].resolve();assert.equal(await replacement,true);
  decoders[0].reject(new Error('Stale image failed'));assert.equal(await old,false);
  assert.equal(skeleton.hidden,true);assert.equal(image.style.visibility,'');
});
