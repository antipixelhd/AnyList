import assert from 'node:assert/strict';
import test from 'node:test';
import { createContentTransition, mountDetailsMotion } from '../src/lib/content-motion.ts';

function fixture() {
  const reduced = new EventTarget(); reduced.matches = false;
  const original = globalThis.matchMedia;
  globalThis.matchMedia = () => reduced;
  const controller = new AbortController(), element = {inert:false,getClientRects:()=>[{}]};
  const finishes = [], changes = [];
  const transition = createContentTransition(element,controller.signal,{
    dismiss:()=>new Promise(resolve=>finishes.push(resolve)), reveal:()=>{}, cancel:()=>{},
  });
  globalThis.matchMedia = original;
  return {reduced,controller,element,finishes,changes,transition};
}

test('rapid tab changes apply only the latest requested content', async () => {
  const f = fixture();
  const first = f.transition(()=>f.changes.push('actors'));
  const second = f.transition(()=>f.changes.push('staff'));
  f.finishes[1](true);
  await Promise.all([first,second]);
  assert.deepEqual(f.changes,['staff']); assert.equal(f.element.inert,false);
  f.controller.abort();
});

test('enabling reduced motion settles content even when the interrupted animation never finishes', async () => {
  const f = fixture();
  const pending = f.transition(()=>f.changes.push('compact'));
  f.reduced.matches = true; f.reduced.dispatchEvent(new Event('change'));
  await pending;
  assert.deepEqual(f.changes,['compact']); assert.equal(f.element.inert,false);
  f.controller.abort();
});

test('navigation cancels deferred changes and restores interaction', async () => {
  const f = fixture();
  const pending = f.transition(()=>f.changes.push('old page'));
  f.controller.abort(); await pending;
  assert.deepEqual(f.changes,[]); assert.equal(f.element.inert,false);
});

test('season header and native summary share disclosure state and stop responding after navigation', t => {
  const previousMedia=globalThis.matchMedia, previousElement=globalThis.Element;
  const reduced=new EventTarget(); reduced.matches=true;
  class Control extends EventTarget {
    attributes=new Map();
    closest(){return null;}
    setAttribute(name,value){this.attributes.set(name,value);}
  }
  globalThis.Element=Control;
  globalThis.matchMedia=()=>reduced;
  t.after(()=>{globalThis.matchMedia=previousMedia;globalThis.Element=previousElement;});
  const summary=new Control(), header=new Control(), details=new Control(), controller=new AbortController();
  const arrow={style:{}};
  summary.querySelector=()=>arrow;
  Object.assign(details,{open:false,style:{removeProperty(){}},querySelector:()=>summary,getBoundingClientRect:()=>({height:details.open?400:50})});
  mountDetailsMotion(details,controller.signal,[header]);
  header.dispatchEvent(new Event('click'));
  assert.equal(details.open,true);
  assert.equal(arrow.style.transform,'rotate(-180deg)');
  assert.equal(header.attributes.get('aria-expanded'),'true');
  summary.dispatchEvent(new Event('click'));
  assert.equal(details.open,false);
  assert.equal(arrow.style.transform,'rotate(0deg)');
  assert.equal(header.attributes.get('aria-expanded'),'false');
  details.open=true;
  details.dispatchEvent(new Event('toggle'));
  assert.equal(header.attributes.get('aria-expanded'),'true');
  controller.abort();
  header.dispatchEvent(new Event('click'));
  assert.equal(details.open,true);
});
