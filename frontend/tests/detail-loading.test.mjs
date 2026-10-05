import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../src/components/dev/DetailLoading.astro', import.meta.url), 'utf8');
const script = source.match(/<script\b[^>]*>([\s\S]*?)<\/script>/)[1];

function preview({ complete = false, naturalWidth = 0, decode = () => Promise.resolve() } = {}) {
  let now = 0;
  let timerId = 0;
  const timers = new Map();
  const listeners = new Map();
  const attributes = new Map();
  const region = {
    isConnected: true,
    setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: name => attributes.delete(name),
  };
  const img = {
    complete, naturalWidth, decode,
    closest: () => region,
    addEventListener: (name, handler) => listeners.set(name, handler),
    removeEventListener: (name, handler) => {
      if (listeners.get(name) === handler) listeners.delete(name);
    },
  };
  const root = {
    isConnected: true,
    querySelectorAll: selector => selector.includes('.lab-hero-image') ? [img] : [],
  };
  runInNewContext(script, {
    document: { currentScript: { closest: () => root }, addEventListener() {} },
    AbortController,
    performance: { now: () => now },
    requestAnimationFrame: callback => callback(),
    setTimeout: (callback, ms) => {
      const id = ++timerId;
      timers.set(id, { callback, at: now + ms });
      return id;
    },
    clearTimeout: id => timers.delete(id),
  });
  const flush = async () => {
    for (let i = 0; i < 30; i++) await Promise.resolve();
  };
  return {
    attributes, listeners, flush,
    emit: name => listeners.get(name)?.(),
    advance: async ms => {
      now += ms;
      for (const [id, timer] of timers) {
        if (timer.at <= now) {
          timers.delete(id);
          timer.callback();
        }
      }
      await flush();
    },
  };
}

for (const [name, options] of [
  ['image request never completes', {}],
  ['image decode never completes', { complete: true, decode: () => new Promise(() => {}) }],
]) {
  test(`loading skeleton clears when ${name}`, async () => {
    const page = preview(options);
    await page.flush();
    assert.equal(page.attributes.get('aria-busy'), 'true');
    assert.ok(page.attributes.has('data-lab-loading'));
    await page.advance(1499);
    assert.ok(page.attributes.has('data-lab-loading'));
    await page.advance(1);
    await page.advance(0);
    assert.equal(page.attributes.get('aria-busy'), 'false');
    assert.ok(!page.attributes.has('data-lab-loading'));
    assert.ok(!page.attributes.has('data-lab-pending'));
    assert.equal(page.listeners.size, 0);
  });
}

for (const event of ['load', 'error']) {
  test(`image ${event} clears loading after the minimum skeleton duration`, async () => {
    const page = preview({ decode: event === 'error' ? () => Promise.reject(new Error('broken image')) : undefined });
    await page.flush();
    page.emit(event);
    await page.flush();
    await page.advance(349);
    assert.ok(page.attributes.has('data-lab-loading'));
    await page.advance(1);
    assert.equal(page.attributes.get('aria-busy'), 'false');
    assert.ok(!page.attributes.has('data-lab-loading'));
    assert.equal(page.listeners.size, 0);
  });
}

test('cached images skip loading skeletons', async () => {
  const page = preview({ complete: true, naturalWidth: 100 });
  await page.flush();
  assert.equal(page.attributes.size, 0);
  assert.equal(page.listeners.size, 0);
});
