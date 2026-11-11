import assert from 'node:assert/strict';
import test from 'node:test';
import { createLoadingReveal, cancelLoadingReveals, scheduleArtworkPulse } from '../src/lib/loading-reveal.ts';

function fixture({ reduced = false, top = 20 } = {}) {
  const calls = [];
  const element = {
    isConnected: true,
    ownerDocument: { defaultView: { innerHeight: 800, matchMedia: () => ({ matches: reduced }) } },
    closest: () => null,
    getBoundingClientRect: () => ({ width: 40, height: 40, top, bottom: top + 40 }),
    animate(frames, timing) {
      let resolve;
      const call = { frames, timing, cancelled: false, finished: new Promise(done => { resolve = done; }),
        cancel() { this.cancelled = true; resolve(); } };
      calls.push(call);
      return call;
    },
  };
  return { element, calls };
}

test('only a visible skeleton wait reveals ready content, once and without an exit delay', () => {
  let now = 0;
  const transition = createLoadingReveal(() => now);
  const { element, calls } = fixture();
  transition.finish(element);
  transition.begin();
  now = 99;
  transition.finish(element);
  assert.equal(calls.length, 0);
  transition.begin();
  now = 199;
  transition.begin(); // Repeated pending updates do not restart the clock.
  transition.finish(element);
  transition.finish(element);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].frames, { opacity: [.92, 1] });
  assert.equal(calls[0].timing.duration, 120);
  cancelLoadingReveals(element);
  assert.equal(calls[0].cancelled, true);
});

test('fast artwork skips the pulse and a started pulse can finish over ready artwork', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const motion = Object.assign(new EventTarget(), { matches: false });
  const calls = [];
  let attached = 0, finish, enter, top = 20;
  const holder = {
    isConnected: true,
    ownerDocument: {
      defaultView: {
        innerHeight: 800, innerWidth: 500, matchMedia: () => motion,
        IntersectionObserver: class {
          constructor(callback) { enter = () => callback([{ isIntersecting: true }]); }
          observe() {}
          disconnect() {}
        },
      },
      createElement: () => ({
        setAttribute() {},
        remove() { attached--; },
        animate(_frames, timing) {
          calls.push(timing.duration);
          return { finished: new Promise(resolve => { finish = resolve; }), cancel() {} };
        },
      }),
    },
    getBoundingClientRect: () => ({ width: 50, height: 60, top, bottom: top + 60, left: 20, right: 70 }),
    append() { attached++; },
  };
  const controller = new AbortController();
  scheduleArtworkPulse(holder, controller.signal)(true);
  t.mock.timers.tick(120);
  assert.equal(attached, 0);
  const ready = scheduleArtworkPulse(holder, controller.signal);
  t.mock.timers.tick(120);
  ready(true);
  assert.equal(attached, 1);
  assert.deepEqual(calls, [180]);
  finish();
  await Promise.resolve();
  assert.equal(attached, 0);
  top = 900;
  scheduleArtworkPulse(holder, controller.signal);
  t.mock.timers.tick(120);
  assert.equal(attached, 0);
  top = 50;
  enter();
  t.mock.timers.tick(119);
  assert.equal(attached, 0);
  t.mock.timers.tick(1);
  assert.equal(attached, 1);
  controller.abort();
  assert.equal(attached, 0);
});

test('reduced motion, offscreen content and cancelled lifetimes do not animate', () => {
  let now = 0;
  const transition = createLoadingReveal(() => now);
  const offscreen = fixture({ top: 900 }), reduced = fixture({ reduced: true }), shown = fixture();
  const controller = new AbortController();
  transition.begin();
  now = 200;
  transition.finish([offscreen.element, reduced.element, shown.element], controller.signal);
  assert.equal(offscreen.calls.length + reduced.calls.length, 0);
  assert.equal(shown.calls.length, 1);
  controller.abort();
  assert.equal(shown.calls[0].cancelled, true);
  transition.begin();
  now = 400;
  transition.finish(shown.element, controller.signal);
  assert.equal(shown.calls.length, 1);
});
