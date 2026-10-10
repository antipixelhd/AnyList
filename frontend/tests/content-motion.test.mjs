import assert from 'node:assert/strict';
import test from 'node:test';
import { createContentTransition } from '../src/lib/content-motion.ts';

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
