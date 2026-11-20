import test from 'node:test';
import assert from 'node:assert/strict';
import {instantPageTransitions} from '../src/lib/instant-page-transitions.ts';

test('only active route navigation skips capture while preserving the native callback and transition',async()=>{
  const doc=new EventTarget();let skipped=0,updated=0;
  let rejectReady;
  const transition={ready:new Promise((_,reject)=>{rejectReady=reject;}),skipTransition(){skipped++;rejectReady(new DOMException('Skipped','AbortError'));}};
  doc.startViewTransition=update=>{update();return transition;};
  instantPageTransitions(doc);
  assert.strictEqual(doc.startViewTransition(()=>updated++),transition);assert.equal(skipped,0);
  const controller=new AbortController(),event=new Event('astro:before-preparation');event.signal=controller.signal;
  doc.dispatchEvent(event);doc.startViewTransition(()=>updated++);assert.equal(skipped,1);
  doc.dispatchEvent(new Event('astro:after-swap'));doc.startViewTransition(()=>updated++);assert.equal(skipped,1);
  doc.dispatchEvent(event);controller.abort();doc.startViewTransition(()=>updated++);assert.equal(skipped,1);
  assert.equal(updated,4);
  await new Promise(resolve=>setImmediate(resolve));
});

test('browsers using router fallback need no transition shim',()=>{
  const doc=new EventTarget();instantPageTransitions(doc);assert.equal(doc.startViewTransition,undefined);
});
