import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transpile } from 'typescript';
import test from 'node:test';

const source = readFileSync(new URL('../src/pages/connections.astro', import.meta.url), 'utf8');
const setup = source.slice(source.indexOf('  function setupCollapsibleRows()'), source.indexOf('  function openDeepLinkedConnection()'));

test('Connections handlers ignore Administration after navigation and bind once on return', () => {
  let connectionsPage = true;
  let open = false;
  const listeners = new Map();
  const header = {
    dataset: {},
    getAttribute: () => 'section-body',
    setAttribute() {},
    querySelector: () => null,
    addEventListener(type, listener) {
      listeners.set(type, [...(listeners.get(type) || []), listener]);
    },
  };
  const body = { classList: { contains: () => !open }, inert: false };
  const context = {
    document: {
      body: { classList: { contains: () => connectionsPage } },
      querySelectorAll: () => [header],
      getElementById: () => body,
    },
    showCollapsible: () => { open = true; },
    hideCollapsible: () => { open = false; },
  };
  runInNewContext(transpile(setup) + '\nthis.setup = setupCollapsibleRows;', context);
  connectionsPage = false;
  context.setup();
  assert.equal(listeners.size, 0, 'Administration must only receive its own handlers');
  connectionsPage = true;
  context.setup();
  context.setup();
  assert.equal(listeners.get('click').length, 1);
  for (const listener of listeners.get('click')) listener();
  assert.equal(open, true);
  for (const listener of listeners.get('click')) listener();
  assert.equal(open, false);
});
