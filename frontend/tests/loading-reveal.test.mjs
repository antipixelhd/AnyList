import assert from 'node:assert/strict';
import test from 'node:test';
import { createLoadingReveal, cancelLoadingReveals } from '../src/lib/loading-reveal.ts';

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
  assert.deepEqual(calls[0].frames, { opacity: [.6, 1] });
  assert.equal(calls[0].timing.duration, 120);
  cancelLoadingReveals(element);
  assert.equal(calls[0].cancelled, true);
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
