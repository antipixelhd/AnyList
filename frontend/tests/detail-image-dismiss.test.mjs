import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

function viewer(mobile=true) {
  const style=()=>({getPropertyValue(key){return this[key]||'';},getPropertyPriority(){return '';},setProperty(key,value){this[key]=value;},removeProperty(key){delete this[key];}});
  const element=()=>({style:style(),dataset:{},listeners:{},addEventListener(name,callback){this.listeners[name]=callback;},fire(name,event={}){this.listeners[name]?.({type:name,...event});}});
  const dialog=element(),track=element(),window=element(),controller=new AbortController();
  let timer,now=0;
  Object.assign(dialog,{open:true,clientHeight:844,hasAttribute:()=>false,close(){this.open=false;this.fire('close');}});
  track.setPointerCapture=()=>{};
  Object.assign(window,{innerHeight:844,scrollX:0,scrollY:520,scrollTo(options){this.restored=options;}});
  const document={body:element(),documentElement:element()};
  document.body.style.position='relative';
  const viewport=element();viewport.matches=mobile;
  const context={window,document,performance:{now:()=>now},matchMedia:query=>query.includes('650')?viewport:{matches:false,addEventListener(){}},
    setTimeout:callback=>(timer=callback,1),clearTimeout:()=>{timer=undefined;}};
  const source=readFileSync(new URL('../src/lib/detail-image-dismiss.ts',import.meta.url),'utf8');
  runInNewContext(stripTypeScriptTypes(source.replace(/export /g,''))+';globalThis.bind=bindDetailImageDragDismiss;',context);
  const prepare=context.bind(dialog,track,controller.signal);
  const pointer=(name,x,y,id=1)=>track.fire(name,{pointerId:id,clientX:x,clientY:y,preventDefault(){this.prevented=true;}});
  const release=(cancel=false,id=1)=>window.fire(cancel?'pointercancel':'pointerup',{pointerId:id});
  return {dialog,track,window,document,controller,prepare,pointer,release,resize:mobile=>{viewport.matches=mobile;viewport.fire('change');},advance:ms=>{now+=ms;},finish:()=>timer?.()};
}

test('mobile locks the background and restores the exact scroll and previous styles on close',()=>{
  const v=viewer();v.prepare();
  assert.equal(v.document.body.style.position,'fixed');assert.equal(v.document.body.style.top,'-520px');
  v.dialog.close();assert.equal(v.document.body.style.position,'relative');assert.equal(v.document.body.style.top,undefined);
  assert.equal(v.window.restored.top,520);
});
test('short downward drags return, long drags close only after release and animation',()=>{
  const v=viewer();v.prepare();v.pointer('pointerdown',100,100);v.pointer('pointermove',102,160);
  assert.equal(v.dialog.style.translate,'0 60px');assert.equal(v.dialog.open,true);
  v.release();assert.equal(v.dialog.style.translate,'0 0');v.finish();assert.equal(v.dialog.open,true);
  v.pointer('pointerdown',100,100);v.pointer('pointermove',100,310);
  assert.equal(v.dialog.open,true);v.release();assert.equal(v.dialog.open,true);v.finish();assert.equal(v.dialog.open,false);
});
test('horizontal navigation and upward gestures never dismiss',()=>{
  const v=viewer();v.prepare();v.pointer('pointerdown',100,100);v.pointer('pointermove',300,110);v.release();
  assert.equal(v.dialog.style.translate,undefined);
  v.pointer('pointerdown',100,300);v.pointer('pointermove',100,50);v.release();v.finish();assert.equal(v.dialog.open,true);
});
test('pointer cancellation, pinch gestures and teardown cannot commit a dismissal',()=>{
  const v=viewer();v.prepare();v.pointer('pointerdown',100,100);v.pointer('pointermove',100,400);v.release(true);v.finish();
  assert.equal(v.dialog.open,true);
  v.pointer('pointerdown',100,100);v.pointer('pointermove',100,400);v.pointer('pointerdown',200,100,2);v.release();v.finish();
  assert.equal(v.dialog.open,true);v.controller.abort();assert.equal(v.document.body.style.position,'relative');
});
test('desktop locks the gallery background without enabling drag dismissal',()=>{
  const v=viewer(false);v.prepare();v.pointer('pointerdown',100,100);v.pointer('pointermove',100,400);v.release();
  assert.equal(v.document.body.style.position,'fixed');assert.equal(v.dialog.style.translate,undefined);assert.equal(v.dialog.open,true);
  v.dialog.close();assert.equal(v.document.body.style.position,'relative');assert.equal(v.window.restored.top,520);
});

test('resizing an open gallery keeps the background locked until it closes',()=>{
  const v=viewer();v.prepare();v.resize(false);
  assert.equal(v.document.body.style.position,'fixed');assert.equal(v.document.body.style.top,'-520px');
  v.resize(true);assert.equal(v.document.body.style.position,'fixed');
  v.controller.abort();assert.equal(v.document.body.style.position,'relative');assert.equal(v.window.restored.top,520);
});

test('a short deliberate flick closes while a slow short drag or a paused flick returns',()=>{
  const fast=viewer();fast.prepare();fast.pointer('pointerdown',100,100);fast.advance(60);fast.pointer('pointermove',105,140);fast.release();fast.finish();
  assert.equal(fast.dialog.open,false);
  const slow=viewer();slow.prepare();slow.pointer('pointerdown',100,100);slow.advance(300);slow.pointer('pointermove',105,140);slow.release();slow.finish();
  assert.equal(slow.dialog.open,true);
  const paused=viewer();paused.prepare();paused.pointer('pointerdown',100,100);paused.advance(60);paused.pointer('pointermove',105,140);paused.advance(200);paused.release();paused.finish();
  assert.equal(paused.dialog.open,true);
});
test('a relaxed diagonal drag closes at the smaller distance threshold',()=>{
  const v=viewer();v.prepare();v.pointer('pointerdown',100,100);v.advance(400);v.pointer('pointermove',200,210);v.release();v.finish();
  assert.equal(v.dialog.open,false);
});
test('tiny flicks and reversing upward before release return rather than closing',()=>{
  const tiny=viewer();tiny.prepare();tiny.pointer('pointerdown',100,100);tiny.advance(10);tiny.pointer('pointermove',100,120);tiny.release();tiny.finish();
  assert.equal(tiny.dialog.open,true);
  const reverse=viewer();reverse.prepare();reverse.pointer('pointerdown',100,100);reverse.advance(300);reverse.pointer('pointermove',100,300);reverse.advance(80);reverse.pointer('pointermove',100,240);reverse.release();reverse.finish();
  assert.equal(reverse.dialog.open,true);
});
