import assert from 'node:assert/strict';
import test from 'node:test';
import { chartMinimumWidth, mountChartViewport } from '../src/lib/stats-chart-density.ts';

test('crowded charts overflow phone widths while sparse charts fit', () => {
  assert.ok(chartMinimumWidth(20) > 390);
  assert.ok(chartMinimumWidth(5) < 320);
  assert.ok((chartMinimumWidth(100) - 32) / 100 >= 44);
});

test('charts start at the right, preserve manual panning on metric updates, and reset for new data',t=>{
  let resized,disconnected=false;
  const original=globalThis.ResizeObserver;
  globalThis.ResizeObserver=class {constructor(callback){resized=callback;}observe(){}disconnect(){disconnected=true;}};
  t.after(()=>{globalThis.ResizeObserver=original;});
  const scroller=Object.assign(new EventTarget(),{clientWidth:300,scrollWidth:1000,scrollLeft:0});
  const controller=new AbortController();
  const viewport=mountChartViewport(scroller,controller.signal);
  viewport.update('movie:2000,2026');
  assert.equal(scroller.scrollLeft,700);
  scroller.scrollLeft=150;scroller.dispatchEvent(new Event('scroll'));
  viewport.update('movie:2000,2026');resized();
  assert.equal(scroller.scrollLeft,150);
  viewport.update('series:2000,2026');
  assert.equal(scroller.scrollLeft,700);
  scroller.dispatchEvent(new Event('scroll'));
  scroller.clientWidth=200;resized();
  assert.equal(scroller.scrollLeft,800);
  controller.abort();assert.equal(disconnected,true);
});

test('initially hidden and sparse chart viewports settle when shown',t=>{
  let resized;
  const original=globalThis.ResizeObserver;
  globalThis.ResizeObserver=class {constructor(callback){resized=callback;}observe(){}disconnect(){}};
  t.after(()=>{globalThis.ResizeObserver=original;});
  const scroller=Object.assign(new EventTarget(),{clientWidth:0,scrollWidth:1000,scrollLeft:0});
  const controller=new AbortController(),viewport=mountChartViewport(scroller,controller.signal);
  viewport.update('years');assert.equal(scroller.scrollLeft,0);
  scroller.clientWidth=300;resized();assert.equal(scroller.scrollLeft,700);
  scroller.scrollWidth=200;viewport.update('short');assert.equal(scroller.scrollLeft,0);
  controller.abort();
});
