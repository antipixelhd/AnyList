import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';
function viewer(mobile=true,reduced=false){
 let now=1000,id=0;const frames=new Map();
 const element=()=>({listeners:{},dataset:{},style:{setProperty(k,v){this[k]=v;},removeProperty(k){delete this[k];}},animations:[],addEventListener(n,f){this.listeners[n]=f;},dispatchEvent(e){this.listeners[e.type]?.(e);},fire(type,e={}){const ev={type,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;},...e};this.listeners[type]?.(ev);return ev;},animate(keyframes,options){const a={keyframes,options,cancel(){this.cancelled=true;}};this.animations.push(a);return a;}});
 const dialog=element(),track=element(),window=element(),image=element(),controller=new AbortController(),selected=[];
 dialog.open=true;image.naturalWidth=780;track.clientWidth=400;track.scrollLeft=0;const images=[image,element(),element()];track.children=images.map(img=>({querySelector:()=>img}));
 const rect=(h)=>({left:0,top:0,right:400,bottom:h,width:400,height:h});
 track.getBoundingClientRect=()=>rect('viewerZoomFrame' in dialog.dataset?844:600);dialog.getBoundingClientRect=()=>rect('viewerZoomFrame' in dialog.dataset?844:690);
 track.setPointerCapture=()=>{};track.hasPointerCapture=()=>false;
 const context={window,Event,performance:{now:()=>now},requestAnimationFrame:f=>(frames.set(++id,f),id),cancelAnimationFrame:i=>frames.delete(i),matchMedia:q=>({matches:q.includes('reduced-motion')?reduced:mobile,addEventListener(){}}),getComputedStyle:e=>({transform:e.style.transform,clipPath:'inset(0px)'}),DOMMatrixReadOnly:class {constructor(){this.a=1.5;this.e=20;this.f=30;}}};
 const swipe=readFileSync(new URL('../src/lib/detail-image-swipe.ts',import.meta.url),'utf8');const zoom=readFileSync(new URL('../src/lib/detail-image-zoom.ts',import.meta.url),'utf8');
 runInNewContext(stripTypeScriptTypes((swipe+'\n'+zoom).replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.bind=bindDetailImageZoom;globalThis.bounds=zoomBounds;',context);
 const reset=context.bind(dialog,track,controller.signal,()=>image,i=>selected.push(i),()=>{});
 const pointer=(type,x,y,id=1)=>(type==='pointerup'||type==='pointercancel'?window:track).fire(type,{pointerId:id,pointerType:'touch',clientX:x,clientY:y});
 const advance=ms=>{now+=ms;const tasks=[...frames.values()];frames.clear();tasks.forEach(f=>f(now));};
 const tap=()=>{pointer('pointerdown',200,300);pointer('pointerup',200,300);};
 const pinch=()=>{pointer('pointerdown',100,300);pointer('pointerdown',300,300,2);};
 const end=()=>{pointer('pointerup',400,300,2);pointer('pointerup',100,300);};
 return {images,dialog,track,window,image,controller,selected,pointer,reset,advance,tap,pinch,end,bounds:context.bounds};
}
test('fit swipes follow the finger immediately and finish with a bounded 160ms snap',()=>{
 const v=viewer();v.pointer('pointerdown',300,300);v.advance(20);v.pointer('pointermove',180,305);
 assert.equal(v.track.scrollLeft,120);assert.ok('viewerSwiping' in v.track.dataset);assert.deepEqual(v.selected,[]);
 v.pointer('pointerup',180,305);v.advance(80);assert.ok(v.track.scrollLeft>120&&v.track.scrollLeft<400);assert.deepEqual(v.selected,[]);
 v.advance(80);assert.equal(v.track.scrollLeft,400);assert.deepEqual(v.selected,[1]);assert.equal(v.track.dataset.viewerSwiping,undefined);
});
test('short drags and cancellation settle back without changing selection',()=>{
 const v=viewer();v.pointer('pointerdown',200,300);v.advance(300);v.pointer('pointermove',180,302);v.pointer('pointerup',180,302);v.advance(160);assert.equal(v.track.scrollLeft,0);assert.deepEqual(v.selected,[0]);
 v.pointer('pointerdown',300,300);v.pointer('pointermove',100,300);v.pointer('pointercancel',100,300);v.advance(160);assert.equal(v.track.scrollLeft,0);
});
test('pinching inward or resting two fingers at fit never expands, shrinks, or navigates',()=>{
 const v=viewer();v.pinch();assert.equal(v.dialog.dataset.viewerZoomFrame,undefined);
 for(const x of [280,220,240,290,300])v.pointer('pointermove',x,300,2);
 assert.equal(v.dialog.dataset.viewerZoomed,undefined);assert.match(v.image.style.transform,/scale\(1\)/);assert.equal(v.dialog.animations.length,0);
 v.end();assert.deepEqual(v.selected,[]);
});
test('zoom frame is latched across the boundary until all contacts lift, with one expansion',()=>{
 const v=viewer();v.pinch();v.pointer('pointermove',400,300,2);assert.ok('viewerZoomFrame' in v.dialog.dataset);assert.match(v.image.style.transform,/scale\(1.5\)/);
 for(const x of [290,330,280,340,300])v.pointer('pointermove',x,300,2);
 assert.equal(v.dialog.animations.length,0);assert.ok('viewerZoomed' in v.dialog.dataset);
 v.pointer('pointerup',300,300,2);assert.equal(v.image.animations.length,0);assert.ok('viewerZoomFrame' in v.dialog.dataset);
 v.pointer('pointerup',100,300);assert.equal(v.image.animations.length,1);v.image.animations.at(-1).onfinish();assert.equal(v.dialog.dataset.viewerZoomFrame,undefined);
});
test('pinch takes over an interrupted swipe and pan never navigates or toggles controls',()=>{
 const v=viewer();v.pointer('pointerdown',300,300);v.pointer('pointermove',240,300);assert.equal(v.track.scrollLeft,60);
 v.pointer('pointerdown',340,300,2);assert.equal(v.track.scrollLeft,0);v.pointer('pointermove',400,300,2);v.end();
 assert.deepEqual(v.selected,[]);assert.equal(v.track.dataset.viewerSwiping,undefined);
 v.pointer('pointerdown',200,300);v.pointer('pointermove',240,340);v.pointer('pointerup',240,340);assert.deepEqual(v.selected,[]);assert.equal(v.dialog.dataset.viewerZoomControls,undefined);
});
test('single taps toggle zoom controls and double taps animate back to fitted layout',()=>{
 const v=viewer();v.pinch();v.pointer('pointermove',400,300,2);v.end();
 v.advance(400);v.tap();assert.ok('viewerZoomControls' in v.dialog.dataset);v.advance(400);v.tap();assert.equal(v.dialog.dataset.viewerZoomControls,undefined);
 v.advance(150);v.tap();assert.equal(v.image.animations.at(-1).options.duration,200);v.image.animations.at(-1).onfinish();assert.equal(v.dialog.dataset.viewerZoomed,undefined);
});
test('double-tap zoom can be interrupted from its displayed frame',()=>{
 const v=viewer();v.tap();v.advance(150);v.tap();const a=v.image.animations.at(-1);assert.match(v.image.style.transform,/scale\(2.5\)/);
 v.pointer('pointerdown',200,300);assert.equal(a.cancelled,true);v.pointer('pointermove',200,300);assert.match(v.image.style.transform,/scale\(1.5\)/);
 v.controller.abort();assert.equal(v.image.style.transform,undefined);assert.equal(v.dialog.dataset.viewerZoomFrame,undefined);
});
test('reduced motion restores layout immediately and desktop remains untouched',()=>{
 const v=viewer(true,true);v.tap();v.advance(150);v.tap();assert.equal(v.image.animations.length,0);v.advance(400);v.tap();v.advance(150);v.tap();assert.equal(v.dialog.dataset.viewerZoomFrame,undefined);
 const d=viewer(false);d.pinch();d.pointer('pointermove',400,300,2);assert.equal(d.image.style.transform,undefined);assert.equal(d.dialog.dataset.viewerZoomFrame,undefined);
});
test('history restores clean gesture state and bounds never allow zoom below fit',()=>{
 const v=viewer();v.pinch();v.pointer('pointermove',400,300,2);v.window.fire('pagehide');assert.equal(v.dialog.dataset.viewerZoomFrame,undefined);assert.equal(v.image.style.transform,undefined);
 const b=v.bounds(.5,100,100,400,220,400,844);assert.equal(b.scale,1);assert.equal(b.x,0);assert.equal(b.y,0);
});

test('rapid swipes interrupt snapping, commit the nearest visible slide, and immediately follow the next drag',()=>{
 const v=viewer();v.pointer('pointerdown',300,300);v.advance(20);v.pointer('pointermove',160,300);v.pointer('pointerup',160,300);v.advance(40);
 v.pointer('pointerdown',300,300);assert.equal(v.track.scrollLeft,400);assert.deepEqual(v.selected,[1]);
 v.pointer('pointermove',160,300);assert.equal(v.track.scrollLeft,540);v.pointer('pointerup',160,300);v.advance(160);assert.equal(v.track.scrollLeft,800);assert.deepEqual(v.selected,[1,2]);
});

test('interrupting return-to-fit keeps the zoom surface stable and invalidates the old completion',()=>{
 const v=viewer();v.pinch();v.pointer('pointermove',400,300,2);v.end();v.advance(400);v.tap();v.advance(150);v.tap();
 const returning=v.image.animations.at(-1);v.pointer('pointerdown',200,300);assert.equal(returning.cancelled,true);
 returning.onfinish();assert.ok('viewerZoomFrame' in v.dialog.dataset);v.pointer('pointermove',220,320);assert.match(v.image.style.transform,/scale\(1.5\)/);
});

test('small-axis letterboxing preserves the pinch anchor instead of jumping to the new viewport center',()=>{
 const v=viewer();v.pinch();v.pointer('pointermove',312,300,2);
 assert.ok('viewerZoomFrame' in v.dialog.dataset);assert.match(v.image.style.transform,/translate\(6px,-104px\) scale\(1.06\)/);
 const anchored=v.bounds(1.06,0,-45,390,219,390,844,true);assert.equal(anchored.y,-45);
 const settled=v.bounds(1.06,0,-45,390,219,390,844);assert.equal(settled.y,0);
});


test('pinch release preserves the image position instead of recentering smaller axes',()=>{
 const v=viewer();v.pinch();v.pointer('pointermove',320,300,2);const held=v.image.style.transform;
 v.pointer('pointerup',320,300,2);v.pointer('pointerup',100,300);
 assert.equal(v.image.style.transform,held);assert.equal(v.image.animations.length,0);
});
test('same preview animates back to fit before committing the selection',()=>{
 const v=viewer();v.pinch();v.pointer('pointermove',400,300,2);v.end();
 assert.equal(v.reset.preview(0,0),true);assert.deepEqual(v.selected,[]);assert.ok('viewerZoomFrame' in v.dialog.dataset);
 const animation=v.image.animations.at(-1);assert.match(animation.keyframes[1].transform,/scale\(1\)/);
 animation.onfinish();assert.deepEqual(v.selected,[0]);assert.equal(v.dialog.dataset.viewerZoomFrame,undefined);
});
test('a different preview scrolls simultaneously with zoom-out and selects only after completion',()=>{
 const v=viewer();v.pinch();v.pointer('pointermove',400,300,2);v.end();
 assert.equal(v.reset.preview(1,0),true);assert.ok('viewerSwiping' in v.track.dataset);assert.deepEqual(v.selected,[]);v.advance(80);
 assert.ok(v.track.scrollLeft>0&&v.track.scrollLeft<400);assert.ok(v.images[1].style.transform);
 v.image.animations.at(-1).onfinish();assert.equal(v.track.scrollLeft,400);assert.deepEqual(v.selected,[1]);assert.equal(v.images[1].style.transform,undefined);assert.equal(v.dialog.dataset.viewerZoomFrame,undefined);
});
test('changing preview choice during zoom-out cancels the obsolete selection',()=>{
 const v=viewer();v.pinch();v.pointer('pointermove',400,300,2);v.end();v.reset.preview(1,0);v.advance(80);
 const old=v.image.animations.at(-1);v.reset.preview(0,0);old.onfinish();assert.deepEqual(v.selected,[]);
 v.image.animations.at(-1).onfinish();assert.deepEqual(v.selected,[0]);assert.equal(v.track.scrollLeft,0);
});


test('pinch entry follows small outward increments without a scale jump or independent frame animation',()=>{
 const v=viewer();v.pinch();
 for(const [x,scale] of [[300.4,1.002],[302,1.01],[306,1.03],[310,1.05],[312,1.06]]){
  v.pointer('pointermove',x,300,2);const rendered=Number(v.image.style.transform.match(/scale\(([^)]+)\)/)[1]);assert.ok(Math.abs(rendered-scale)<.000001);
  assert.ok('viewerZoomFrame' in v.dialog.dataset);assert.equal(v.dialog.animations.length,0);assert.equal(v.image.animations.length,0);
 }
});
