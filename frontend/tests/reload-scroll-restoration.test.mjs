import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';

const component = readFileSync(new URL('../src/components/ReloadScrollRestoration.astro', import.meta.url), 'utf8');
const script = component.match(/<script is:inline>([\s\S]*?)<\/script>/)[1];

test('a hard reload restores natively before deferred routing without changing the saved position', () => {
  const state = { index: 2, scrollX: 0, scrollY: 1800 };
  const history = { state, scrollRestoration: 'manual' };
  runInNewContext(script, { history, performance: { getEntriesByType: () => [{ type: 'reload' }] } });
  assert.equal(history.scrollRestoration, 'auto');
  assert.equal(history.state, state);
  assert.equal(history.state.scrollY, 1800);
});

test('normal visits and back/forward navigation keep the router restoration mode', () => {
  for (const type of ['navigate', 'back_forward', undefined]) {
    const history = { scrollRestoration: 'manual' };
    runInNewContext(script, { history, performance: { getEntriesByType: () => type ? [{ type }] : [] } });
    assert.equal(history.scrollRestoration, 'manual');
  }
});

test('browsers without scroll restoration support are left alone', () => {
  const history = {};
  runInNewContext(script, { history, performance: { getEntriesByType: () => [{ type: 'reload' }] } });
  assert.equal('scrollRestoration' in history, false);
});

test('the restoration script is in the shared head ahead of the client router', () => {
  const layout = readFileSync(new URL('../src/layouts/Tracker.astro', import.meta.url), 'utf8');
  const position = layout.indexOf('<ReloadScrollRestoration />');
  assert.ok(position > layout.indexOf('<head>'));
  assert.ok(position < layout.indexOf('<ClientRouter'));
  assert.ok(position < layout.indexOf('</head>'));
});
