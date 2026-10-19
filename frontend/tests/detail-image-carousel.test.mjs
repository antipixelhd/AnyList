import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';
import {reconcileGalleryChildren} from '../src/lib/detail-gallery-dom.ts';

function gallery(reduced=false) {
  class Element {
    constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.style={};this.attributes=new Map();this.listeners=new Map();this.clientWidth=390;this.scrollLeft=0;}
    append(...children){children.forEach(child=>{child.parent=this;this.children.push(child);});}
    insertBefore(child,before){child.remove();const index=before?this.children.indexOf(before):this.children.length;this.children.splice(index,0,child);child.parent=this;}
    remove(){if(this.parent){this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;}}
    setAttribute(key,value){this.attributes.set(key,value);}
    getAttribute(key){return key==='src'?this.src:this.attributes.get(key);}
    querySelector(selector){return this.children.find(child=>selector==='img'?child.tag==='img':child.className?.includes('image-viewer-slide-loading'));}
    addEventListener(name,callback){this.listeners.set(name,callback);}
    fire(name){this.listeners.get(name)?.();}
    closest(){return null;}
    scrollTo(options){this.scrolled=options;this.scrollLeft=options.left;}
  }
  const controller=new AbortController(),selected=[],prepared=[],frames=[];
  let observer;
  const context={document:{createElement:tag=>new Element(tag)},matchMedia:()=>({matches:reduced}),
    requestAnimationFrame:callback=>(frames.push(callback),frames.length),cancelAnimationFrame:()=>{},
    ResizeObserver:class {constructor(callback){observer=this;this.callback=callback;} observe(){} disconnect(){this.disconnected=true;}},reconcileGalleryChildren};
  const source=readFileSync(new URL('../src/lib/detail-image-carousel.ts',import.meta.url),'utf8');
  runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.create=createDetailImageCarousel;',context);
  const track=new Element(),render=context.create(track,controller.signal,index=>selected.push(index),index=>prepared.push(index));
  const paint=(rows,index=0,scroll=true,animate=false,total=rows.length)=>render(rows,url=>url,total,index,'Artwork',scroll,animate);
  return {track,paint,selected,prepared,controller,resize:()=>observer.callback(),observer:()=>observer,flush:()=>frames.splice(0).forEach(callback=>callback())};
}

test('swipes prepare the exposed slide before selection without touching unseen neighbors',()=>{
  const g=gallery();g.paint(['one','two','three']);
  assert.deepEqual(g.prepared,[]);
  g.track.scrollLeft=40;g.track.fire('scroll');g.flush();
  assert.deepEqual(g.selected,[]);assert.deepEqual(g.prepared,[0,1]);
  assert.equal(g.track.children[2].querySelector('img').src,undefined);
});

test('preview jumps do not prepare intermediate slides during smooth navigation',()=>{
  const g=gallery();g.paint(['one','two','three'],2,true,true);
  g.track.scrollLeft=40;g.track.fire('scroll');g.flush();
  assert.deepEqual(g.prepared,[]);
});

test('metadata refreshes retain loaded slides and pending slots resolve without replacing cached images',()=>{
  const g=gallery();
  const loaded=g.paint(['one','two'],0,true,false,10);
  loaded.image.src='one';loaded.image.complete=true;
  const first=g.track.children[0],second=g.track.children[1];
  g.paint(['one','two','three'],0,true,false,10);
  assert.equal(g.track.children[0],first);assert.equal(g.track.children[1],second);
  assert.equal(g.track.children[0].querySelector('img'),loaded.image);
  assert.equal(loaded.image.src,'one');
  assert.equal(g.track.children[2].querySelector('img').src,undefined);
  g.paint(['one','two','three'],1);
  assert.equal(g.track.children[2].querySelector('img').src,undefined);
});

test('native swipe selection follows scroll position without forcing the row back',()=>{
  const g=gallery();g.paint(['one','two','three']);
  g.track.scrollLeft=390;g.track.fire('scroll');g.flush();
  assert.deepEqual(g.selected,[1]);
  g.paint(['one','two','three'],1,false);
  assert.equal(g.track.scrollLeft,390);
});

test('animated navigation ignores intermediate positions, but direct gestures can interrupt it',()=>{
  const g=gallery();g.paint(['one','two','three'],2,true,true);
  assert.equal(g.track.scrolled.behavior,'smooth');
  g.track.scrollLeft=390;g.track.fire('scroll');g.flush();assert.deepEqual(g.selected,[]);
  g.track.fire('pointerdown');g.track.fire('scroll');g.flush();assert.deepEqual(g.selected,[1]);
});

test('reduced motion, orientation changes and page teardown preserve selection safely',()=>{
  const g=gallery(true);g.paint(['one','two'],1,true,true);
  assert.equal(g.track.scrolled.behavior,'instant');
  g.track.clientWidth=844;g.resize();assert.equal(g.track.scrolled.left,844);
  g.controller.abort();assert.equal(g.observer().disconnected,true);
});



test('dragging warms exposed slides without committing a selection and zoom resizing never selects a neighbor',()=>{
 const g=gallery();g.paint(['one','two','three']);g.track.dataset.viewerSwiping='';
 g.track.scrollLeft=250;g.track.fire('scroll');g.flush();assert.deepEqual(g.selected,[]);assert.deepEqual(g.prepared,[0,1]);
 g.track.closest=()=>({});delete g.track.dataset.viewerSwiping;g.track.scrollLeft=780;g.track.fire('scroll');g.flush();assert.deepEqual(g.selected,[]);assert.deepEqual(g.prepared,[0,1]);
});
