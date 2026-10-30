import assert from 'node:assert/strict';
import test from 'node:test';
import {createMinimumDisplay} from '../src/lib/minimum-display.ts';

test('displayed skeletons retain their first timestamp and wait only the remaining minimum',t=>{
  t.mock.timers.enable({apis:['setTimeout']});let now=0,applied=0;
  const timing=createMinimumDisplay(new AbortController().signal,350,()=>now);
  timing.show();now=100;timing.show();timing.finish(()=>applied++);
  t.mock.timers.tick(249);assert.equal(applied,0);t.mock.timers.tick(1);assert.equal(applied,1);
});
test('warm content and already elapsed skeletons have no artificial wait',()=>{
  let now=0,applied=0;const timing=createMinimumDisplay(new AbortController().signal,350,()=>now);
  timing.finish(()=>applied++);timing.show();now=500;timing.finish(()=>applied++);assert.equal(applied,2);
});
test('new state supersedes delayed rendering and navigation cancels pending callbacks',t=>{
  t.mock.timers.enable({apis:['setTimeout']});const lifetime=new AbortController();let applied='';
  const timing=createMinimumDisplay(lifetime.signal,350,()=>0);timing.show();timing.finish(()=>applied='old');timing.finish(()=>applied='new');
  t.mock.timers.tick(350);assert.equal(applied,'new');timing.show();timing.finish(()=>applied='late');lifetime.abort();t.mock.timers.tick(350);assert.equal(applied,'new');
});
test('aborting a preview settles its wait without waiting for the minimum',async()=>{
  const lifetime=new AbortController(),timing=createMinimumDisplay(lifetime.signal);timing.show();const pending=timing.settle();lifetime.abort();await pending;
});
