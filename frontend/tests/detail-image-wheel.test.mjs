import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

function viewer() {
  const events={},steps=[],timers=new Map();let id=0,now=0;
  const controller=new AbortController();
  const dialog={open:true,addEventListener:(name,fn)=>{events[name]=fn;}};
  const track={clientHeight:800,addEventListener:(name,fn)=>{events[name]=fn;}};
  const context={performance:{now:()=>now},setTimeout:fn=>{timers.set(++id,fn);return id;},clearTimeout:id=>timers.delete(id)};
  const source=readFileSync(new URL('../src/lib/detail-image-wheel.ts',import.meta.url),'utf8');
  runInNewContext(stripTypeScriptTypes(source.replace(/export /g,''))+';globalThis.bind=bindDetailImageWheel;',context);
  context.bind(dialog,track,controller.signal,d=>steps.push(d));
  return {steps,controller,close:()=>{dialog.open=false;events.close();},wheel:(deltaY,options={})=>{
    const event={deltaY,deltaX:0,deltaMode:0,preventDefault(){this.prevented=true;},...options};events.wheel(event);return event;
  },advance:()=>{now+=180;const callbacks=[...timers.values()];timers.clear();callbacks.forEach(fn=>fn());}};
}

test('up advances right, repeated deliberate notches retain their steps',()=>{
  const v=viewer();v.wheel(-120);v.wheel(-120);v.wheel(-120);
  assert.deepEqual(v.steps,[1,1,1]);v.advance();v.advance();assert.deepEqual(v.steps,[1,1,1]);
  v.wheel(120);v.advance();assert.equal(v.steps.at(-1),-1);
});
test('each large delta is one step, rapid bursts retarget immediately, and tiny trackpad deltas accumulate',()=>{
  const v=viewer();v.wheel(-10000);assert.deepEqual(v.steps,[1]);
  for(let i=0;i<100;i++)v.wheel(-120);
  for(let i=0;i<10;i++)v.advance();assert.equal(v.steps.length,101);
  const small=viewer();small.wheel(-10);small.wheel(-10);small.wheel(-10);assert.equal(small.steps.length,0);
  small.wheel(-10);assert.deepEqual(small.steps,[1]);
});
test('browser zoom and horizontal gestures stay native; close and navigation leave no delayed steps',()=>{
  const v=viewer();assert.equal(v.wheel(-120,{ctrlKey:true}).prevented,undefined);
  assert.equal(v.wheel(-120,{deltaX:240}).prevented,undefined);
  v.wheel(-120);v.wheel(-120);v.close();v.advance();assert.deepEqual(v.steps,[1,1]);
  const other=viewer();other.wheel(-120);other.wheel(-120);other.controller.abort();other.advance();assert.deepEqual(other.steps,[1,1]);
});

test('wheel navigation survives modal/control targets and preserves preview scrolling',()=>{
  const v=viewer();
  v.wheel(-120,{target:{closest:()=>null}});
  v.wheel(-120,{target:{closest:()=>null}});
  assert.deepEqual(v.steps,[1,1]);
  assert.equal(v.wheel(-120,{target:{closest:()=>({})}}).prevented,undefined);
  assert.deepEqual(v.steps,[1,1]);
});
