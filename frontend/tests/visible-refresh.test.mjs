import assert from 'node:assert/strict';
import test from 'node:test';
import { startVisibleRefresh } from '../src/lib/visible-refresh.ts';

function environment(t) {
  const document = new EventTarget();
  document.hidden = false;
  const window = new EventTarget();
  const timers = new Map();
  let id = 0;
  const originals = new Map();
  for (const [name, value] of Object.entries({
    document, window,
    setInterval: callback => { timers.set(++id, callback); return id; },
    clearInterval: timer => timers.delete(timer),
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  }
  t.after(() => {
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });
  return { document, window, timers };
}

test('navigation aborts an active refresh and removes polling and wake listeners', async t => {
  const { document, window, timers } = environment(t);
  let signal;
  let finish;
  let calls = 0;
  const handle = startVisibleRefresh({ refresh: received => {
    calls++;
    signal = received;
    return new Promise(resolve => { finish = resolve; });
  } });
  const pending = handle.refresh();
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(signal.aborted, false);
  document.dispatchEvent(new Event('astro:before-swap'));
  assert.equal(signal.aborted, true);
  assert.equal(timers.size, 0);
  window.dispatchEvent(new Event('focus'));
  document.dispatchEvent(new Event('visibilitychange'));
  finish();
  await pending;
  await handle.refresh();
  assert.equal(calls, 1);
});

test('focus, visibility and explicit refresh share one in-flight request', async t => {
  const { document, window } = environment(t);
  let calls = 0;
  let finish;
  const handle = startVisibleRefresh({ refresh: () => {
    calls++;
    return new Promise(resolve => { finish = resolve; });
  } });
  document.hidden = true;
  await handle.refresh();
  assert.equal(calls, 0);
  document.hidden = false;
  const pending = handle.refresh();
  window.dispatchEvent(new Event('focus'));
  document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(handle.refresh(), pending);
  await Promise.resolve();
  assert.equal(calls, 1);
  finish();
  await pending;
  handle.stop();
});

test('a failed request allows retry and navigation abort errors stay quiet', async t => {
  environment(t);
  const failures = [];
  let calls = 0;
  const handle = startVisibleRefresh({
    refresh: () => { calls++; throw new Error('offline'); },
    onError: error => failures.push(error.message),
  });
  await handle.refresh();
  await handle.refresh();
  assert.equal(calls, 2);
  assert.deepEqual(failures, ['offline', 'offline']);
  handle.stop();
  const aborting = startVisibleRefresh({
    refresh: signal => new Promise((_, reject) => {
      signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
    }),
    onError: error => failures.push(error.message),
  });
  const pending = aborting.refresh();
  await Promise.resolve();
  aborting.stop();
  await pending;
  assert.deepEqual(failures, ['offline', 'offline']);
});
