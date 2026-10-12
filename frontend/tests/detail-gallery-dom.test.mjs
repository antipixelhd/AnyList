import assert from 'node:assert/strict';
import test from 'node:test';
import {reconcileGalleryChildren,createGalleryThumbnailWatcher,createGalleryImageLoader} from '../src/lib/detail-gallery-dom.ts';

function parent(nodes=[]){
  const root={children:nodes,moves:0,insertBefore(node,before){
    this.moves++;
    const old=this.children.indexOf(node);if(old>=0)this.children.splice(old,1);
    const index=before?this.children.indexOf(before):this.children.length;
    this.children.splice(index,0,node);node.owner=this;
  }};
  nodes.forEach(node=>node.owner=root);return root;
}
const node=()=>({remove(){const i=this.owner.children.indexOf(this);this.owner.children.splice(i,1);}});

test('unchanged cached galleries retain every attached node without moving or recreating images',()=>{
  const nodes=Array.from({length:10},node),root=parent([...nodes]);
  reconcileGalleryChildren(root,nodes);reconcileGalleryChildren(root,nodes);
  assert.equal(root.moves,0);assert.deepEqual(root.children,nodes);
});
test('ten distinct loading slots survive repeated updates and only resolved slots change',()=>{
  const slots=Array.from({length:10},node),root=parent([...slots]);
  const image=node(),next=[image,...slots.slice(1)];
  reconcileGalleryChildren(root,next);reconcileGalleryChildren(root,next);
  assert.equal(root.children.length,10);assert.equal(new Set(root.children).size,10);
  assert.equal(root.moves,1);assert.equal(root.children[8],slots[8]);
});

function thumbnail({complete=false,width=0}={}){
  const image=new EventTarget();
  Object.assign(image,{src:'https://example.test/art.jpg',hidden:true,complete,naturalWidth:width,
    getAttribute:()=>image.src,style:{opacity:'0',removeProperty(name){delete this[name];}}});
  const classes=new Set(['detail-image-skeleton']);
  const button={querySelector:()=>image,classList:{add:name=>classes.add(name),remove:name=>classes.delete(name)}};
  return {image,button,classes};
}
test('cold lazy artwork keeps a layout box and skeleton until the image loads',()=>{
  const t=thumbnail(),controller=new AbortController();let ready=0;
  const watch=createGalleryThumbnailWatcher(controller.signal,()=>ready++);
  watch(t.button);watch(t.button);
  assert.equal(t.image.hidden,false);assert(t.classes.has('detail-image-skeleton'));
  assert.equal(t.image.style.opacity,'0');
  t.image.naturalWidth=780;t.image.complete=true;t.image.dispatchEvent(new Event('load'));
  assert.equal(ready,1);assert.equal(t.classes.size,0);assert.equal(t.image.style.opacity,undefined);
});
test('cached artwork is shown immediately without replaying its fade or skeleton',()=>{
  const t=thumbnail({complete:true,width:780});let fades=0;
  createGalleryThumbnailWatcher(new AbortController().signal,()=>fades++)(t.button);
  assert.equal(t.image.hidden,false);assert.equal(t.classes.size,0);assert.equal(fades,0);
});
test('navigation cancels readiness updates from an old gallery',()=>{
  const t=thumbnail(),controller=new AbortController();let ready=0;
  createGalleryThumbnailWatcher(controller.signal,()=>ready++)(t.button);
  controller.abort();t.image.naturalWidth=780;t.image.dispatchEvent(new Event('load'));
  assert.equal(ready,0);
});

test('offscreen gallery thumbnails stay source-free until viewed and stop loading after teardown',()=>{
  const previous=globalThis.IntersectionObserver;
  let callback,disconnected=false;
  globalThis.IntersectionObserver=class {
    constructor(cb){callback=cb;}
    observe(){} unobserve(){} disconnect(){disconnected=true;}
  };
  try {
    const controller=new AbortController(),image={dataset:{gallerySrc:'https://example.test/thumbnail.jpg'}},button={querySelector:()=>image};
    let loads=0;
    const load=createGalleryImageLoader(controller.signal,()=>loads++);
    load(button);callback([{target:button,isIntersecting:false}]);assert.equal(image.src,undefined);
    callback([{target:button,isIntersecting:true}]);assert.equal(image.src,'https://example.test/thumbnail.jpg');assert.equal(loads,1);
    const later={dataset:{gallerySrc:'https://example.test/later.jpg'}},other={querySelector:()=>later};
    load(other);controller.abort();callback([{target:other,isIntersecting:true}]);assert.equal(later.src,undefined);assert.equal(disconnected,true);
  }finally{if(previous===undefined)delete globalThis.IntersectionObserver;else globalThis.IntersectionObserver=previous;}
});
