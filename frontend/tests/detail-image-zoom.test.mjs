import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';
function viewer(mobile=true,reduced=false){
 const element=()=>({listeners:{},dataset:{},style:{removeProperty(key){delete this[key];}},addEventListener(name,fn){this.listeners[name]=fn;},dispatchEvent(e){this.listeners[e.type]?.(e);},fire(type,e={}){const event={type,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;},...e};this.listeners[type]?.(event);return event;}});
 const dialog=element(),track=element(),window=element(),image=element(),controller=new AbortController();let now=1000,steps=[];
 const animations=[];image.animate=(frames,options)=>{const animation={frames,options,cancel(){this.cancelled=true;}};animations.push(animation);return animation;};dialog.open=true;image.naturalWidth=780;track.clientWidth=400;track.getBoundingClientRect=()=>({left:0,top:0,width:400,height:600});track.setPointerCapture=()=>{};track.hasPointerCapture=()=>false;
 const context={window,Event,performance:{now:()=>now},matchMedia:query=>({matches:query.includes("reduced-motion")?reduced:mobile,addEventListener(){}}),getComputedStyle:()=>({transform:"matrix(1.5,0,0,1.5,20,30)"}),DOMMatrixReadOnly:class {a=1.5;e=20;f=30;}};
 const source=readFileSync(new URL('../src/lib/detail-image-zoom.ts',import.meta.url),'utf8');
 runInNewContext(stripTypeScriptTypes(source.replace(/export /g,''))+';globalThis.bind=bindDetailImageZoom;globalThis.bounds=zoomBounds;',context);
 const reset=context.bind(dialog,track,controller.signal,()=>image,d=>steps.push(d),()=>{});
 const pointer=(type,x,y,id=1)=>(type==='pointerup'||type==='pointercancel'?window:track).fire(type,{pointerId:id,pointerType:'touch',clientX:x,clientY:y});
 return {animations,dialog,track,window,image,steps,pointer,reset,controller,bounds:context.bounds,advance:ms=>now+=ms};
}
test('pinch anchors scale and single remaining finger pans without navigation',()=>{
 const v=viewer();v.pointer('pointerdown',100,300);v.pointer('pointerdown',300,300,2);
 assert.ok('viewerZoomed' in v.dialog.dataset);
 assert.equal(v.pointer('pointermove',400,300,2).stopped,true);
 assert.match(v.image.style.transform,/scale\(1.5\)/);
 v.pointer('pointerup',400,300,2);v.pointer('pointermove',120,320);
 assert.match(v.image.style.transform,/translate\(70px,20px\)/);
 v.pointer('pointerup',120,320);assert.deepEqual(v.steps,[]);
 assert.equal(v.track.fire('click').stopped,true);
});
test('fit horizontal swipe navigates once and cancellation does not navigate',()=>{
 const v=viewer();v.pointer('pointerdown',300,300);v.pointer('pointermove',100,305);v.pointer('pointerup',100,305);
 assert.deepEqual(v.steps,[1]);v.pointer('pointerdown',300,300);v.pointer('pointercancel',100,300);assert.deepEqual(v.steps,[1]);
});
test('double tap zooms and resets; history and abort clear transforms',()=>{
 const v=viewer();const tap=()=>{v.pointer('pointerdown',200,300);v.pointer('pointerup',200,300);};
 tap();v.advance(150);tap();assert.match(v.image.style.transform,/scale\(2.5\)/);
 v.animations.at(-1).onfinish();v.advance(400);tap();v.advance(150);tap();assert.match(v.image.style.transform,/scale\(1\)/);
 tap();v.advance(150);tap();v.window.fire('pagehide');assert.equal(v.image.style.transform,undefined);assert.equal(v.dialog.dataset.viewerZoomed,undefined);
 v.controller.abort();assert.equal(v.dialog.dataset.viewerZoomed,undefined);
});
test('bounds reject overscaling and panning outside image; desktop stays native',()=>{
 const v=viewer(false);const b=v.bounds(10,999,-999,400,600);assert.equal(b.scale,4);assert.equal(b.x,600);assert.equal(b.y,-900);
 v.pointer('pointerdown',100,300);v.pointer('pointerdown',300,300,2);v.pointer('pointermove',400,300,2);assert.equal(v.image.style.transform,undefined);
});

test('single taps toggle zoom controls; panning hides them without toggling or navigating',()=>{
 const v=viewer();v.pointer('pointerdown',100,300);v.pointer('pointerdown',300,300,2);v.pointer('pointermove',400,300,2);v.pointer('pointerup',400,300,2);v.pointer('pointerup',100,300);
 const tap=()=>{v.advance(400);v.pointer('pointerdown',200,300);v.pointer('pointerup',200,300);};
 tap();assert.ok('viewerZoomControls' in v.dialog.dataset);assert.ok('viewerZoomed' in v.dialog.dataset);
 tap();assert.equal(v.dialog.dataset.viewerZoomControls,undefined);
 tap();assert.ok('viewerZoomControls' in v.dialog.dataset);
 v.pointer('pointerdown',200,300);v.pointer('pointermove',240,340);v.pointer('pointerup',240,340);
 assert.equal(v.dialog.dataset.viewerZoomControls,undefined);assert.deepEqual(v.steps,[]);
 tap();assert.ok('viewerZoomControls' in v.dialog.dataset);v.reset();assert.equal(v.dialog.dataset.viewerZoomControls,undefined);
});


test('double-tap animation eases both ways and a new touch continues from its displayed frame',()=>{
 const v=viewer();const tap=()=>{v.pointer('pointerdown',200,300);v.pointer('pointerup',200,300);};
 tap();v.advance(150);tap();const zoom=v.animations.at(-1);
 assert.equal(zoom.options.duration,240);assert.match(zoom.frames[0].transform,/scale\(1\)/);assert.match(zoom.frames[1].transform,/scale\(2.5\)/);
 v.pointer('pointerdown',200,300);assert.equal(zoom.cancelled,true);assert.match(v.image.style.transform,/translate\(20px,30px\) scale\(1.5\)/);
 v.pointer('pointercancel',200,300);v.advance(400);tap();v.advance(150);tap();
 assert.match(v.animations.at(-1).frames[1].transform,/scale\(1\)/);v.reset();assert.equal(v.animations.at(-1).cancelled,true);
});
test('reduced motion skips double-tap animation',()=>{
 const v=viewer(true,true);for(let i=0;i<2;i++){v.pointer('pointerdown',200,300);v.pointer('pointerup',200,300);v.advance(150);}
 assert.equal(v.animations.length,0);assert.match(v.image.style.transform,/scale\(2.5\)/);
});
