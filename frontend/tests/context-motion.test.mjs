import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import {boundedAnimation} from '../src/lib/bounded-animation.ts';

function source(name){return stripTypeScriptTypes(readFileSync(new URL(`../src/lib/${name}.ts`,import.meta.url),'utf8').replace(/^import .*$/gm,'').replace(/export /g,''));}
function motionFixture(){
 const calls=[];
 const play=(element,frames,duration)=>{let resolve;const c={element,frames,duration,finished:new Promise(done=>resolve=done),cancel(){this.cancelled=true;resolve();},finish(){resolve();}};calls.push(c);return c;};
 const style=()=>({opacity:'',overflow:'',marginTop:'',marginBottom:'',maxHeight:'',removeProperty(name){this[name]='';}});
 const view=Object.assign(new EventTarget(),{innerHeight:900,innerWidth:1400,getComputedStyle:()=>({maxHeight:'700px'}),matchMedia:()=>reduced});
 const reduced=Object.assign(new EventTarget(),{matches:false});
 return {calls,play,style,view,reduced};
}

test('native animation completion is bounded and interruption cancels immediately',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 let cancelled=0;const element={ownerDocument:{timeline:{currentTime:100}},animate(){return {finished:new Promise(()=>{}),cancel(){cancelled++;}};}};
 const a=boundedAnimation(element,{opacity:[0,1]},320);let done=false;a.finished.then(()=>done=true);
 t.mock.timers.tick(399);await Promise.resolve();assert.equal(done,false);
 t.mock.timers.tick(1);await a.finished;assert.equal(cancelled,1);
 const b=boundedAnimation(element,{opacity:[0,1]},320);b.cancel();await b.finished;assert.equal(cancelled,2);
});

test('adaptive fields pin once, replace interrupted height motion and restore native layout on close',async()=>{
 let visualTop=120;
 const f=motionFixture(),dialog=Object.assign(new EventTarget(),{open:true,style:f.style(),ownerDocument:{defaultView:f.view},getBoundingClientRect:()=>({top:visualTop})});
 const content={hidden:true,getBoundingClientRect:()=>({height:content.hidden?0:content.height}),height:80};
 const wrapper={hidden:true,style:f.style(),getBoundingClientRect:()=>({height:wrapper.hidden?0:parseFloat(wrapper.style.height)||content.height})};
 let resized;
 let stopped=false;
 const context={boundedAnimation:f.play,cancelUiMotion(){stopped=true;visualTop=90;},ResizeObserver:class{constructor(cb){resized=cb;}observe(){}disconnect(){}},...{AbortController}};
 runInNewContext(source('anchored-field-reveal')+';globalThis.create=createAnchoredFieldReveal;',context);
 const signal=new AbortController();const reveal=context.create(dialog,wrapper,content,signal.signal);
 reveal.change(()=>{content.hidden=false;},true);
 assert.equal(stopped,true);
 assert.equal(dialog.style.marginTop,'120px');assert.equal(wrapper.hidden,false);assert.equal(f.calls[0].frames.height[1],'80px');
 content.height=150;resized();assert.equal(f.calls[0].cancelled,true);assert.equal(f.calls.at(-1).frames.height[1],'150px');
 reveal.change(()=>{content.hidden=true;},true);const closing=f.calls.at(-1);closing.finish();await Promise.resolve();
 assert.equal(wrapper.hidden,true);assert.equal(wrapper.style.height,'');
 dialog.open=false;dialog.dispatchEvent(new Event('close'));assert.equal(dialog.style.marginTop,'');assert.equal(dialog.style.maxHeight,'');dialog.open=true;
 const count=f.calls.length;f.reduced.matches=true;
 reveal.change(()=>{content.hidden=false;},true);
 assert.equal(f.calls.length,count);assert.equal(wrapper.hidden,false);assert.equal(wrapper.style.height,'');
 signal.abort();
});

function galleryFixture(){
 const f=motionFixture();const doc={defaultView:f.view,createElement(){return {className:'',style:f.style(),setAttribute(){},remove(){this.removed=true;},getBoundingClientRect(){return {left:20,top:30,width:100,height:150};}};}};
 const dialog=Object.assign(new EventTarget(),{open:true,style:f.style(),dataset:{},ownerDocument:doc,querySelectorAll:()=>[],querySelector:()=>null,append(node){this.bridge=node;},hasAttribute(name){return name==='data-motion-closing'?false:false;}});
 const track=Object.assign(new EventTarget(),{style:f.style()});
 const image={complete:true,naturalWidth:200,naturalHeight:300,currentSrc:'https://cdn.example/existing.jpg',src:'',isConnected:true,getBoundingClientRect:()=>({left:20,top:30,width:100,height:150})};
 const selected={...image,getBoundingClientRect:()=>({left:300,top:50,width:400,height:700})};
 let motion,identity='same';const context={boundedAnimation:f.play,registerDialogMotion(_d,m){motion=m;},queueMicrotask};
 runInNewContext(source('image-origin-motion')+';globalThis.api={createImageOriginMotion,containedImageBox,imageOriginTransform};',context);
 const controller=new AbortController(),prepare=context.api.createImageOriginMotion(dialog,track,controller.signal,()=>selected,()=>identity);
 const button={isConnected:true,focused:false,focus(){this.focused=true;}};
 return {...f,dialog,track,image,selected,prepare,button,context,controller,get motion(){return motion;},changeIdentity(){identity='other';}};
}

test('image expansion fits the actual contained image and reuses the displayed URL',async()=>{
 const f=galleryFixture();f.prepare(f.button,f.image,'same');f.motion.enter();await Promise.resolve();
 assert.equal(f.track.style.opacity,'0');assert.equal(f.dialog.bridge.style.backgroundImage,'url("https://cdn.example/existing.jpg")');
 assert.equal(f.dialog.bridge.style.height,'600px');
 assert.match(f.calls[0].frames.transform[0],/scale\(0.25, 0.25\)/);
 f.calls[0].finish();await Promise.resolve();await Promise.resolve();
 assert.equal(f.dialog.bridge.removed,true);assert.equal(f.track.style.opacity,'');
});

test('closing after paging or with an offscreen origin fades instead of flying to the wrong thumbnail',async()=>{
 for(const changed of [true,false]){
  const f=galleryFixture();f.prepare(f.button,f.image,'same');
  if(changed)f.changeIdentity();else f.image.getBoundingClientRect=()=>({left:20,top:-1000,width:100,height:150});
  const closing=f.motion.exit();assert.equal(f.calls[0].element,f.dialog);assert.deepEqual([...f.calls[0].frames.opacity],[1,0]);f.calls[0].finish();await closing;
 }
});

test('reduced motion, resize and close clear the bridge and return focus without residual opacity',async()=>{
 const f=galleryFixture();f.prepare(f.button,f.image,'same');f.reduced.matches=true;f.motion.enter();await Promise.resolve();assert.equal(f.calls.length,0);
 f.reduced.matches=false;f.motion.enter();await Promise.resolve();f.view.dispatchEvent(new Event('resize'));
 assert.equal(f.dialog.bridge.removed,true);assert.equal(f.track.style.opacity,'');
 f.dialog.open=false;f.dialog.dispatchEvent(new Event('close'));assert.equal(f.button.focused,true);
 f.controller.abort();
});

test('closing during expansion cleans the bridge synchronously without waiting for a native close event',async()=>{
 const f=galleryFixture();f.prepare(f.button,f.image,'same');f.motion.enter();await Promise.resolve();
 const close=f.motion.exit();f.calls.at(-1).finish();await close;
 f.dialog.open=false;f.motion.closed();
 assert.equal(f.track.style.opacity,'');assert.equal(f.dialog.bridge.removed,true);assert.equal(f.button.focused,true);
});
