import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';
import test from 'node:test';

test('finishing an import unlocks upload and connection controls without reloading', async () => {
  const classes = {toggle() {}};
  const button = {disabled: false, classList: classes};
  const upload = {inert: false, classList: classes};
  const banner = {dataset: {}, hidden: true, querySelector: () => null};
  const document = Object.assign(new EventTarget(), {
    querySelector: selector => selector === '#add-connection-btn' ? button : banner,
    querySelectorAll: () => [upload],
  });
  let current = {import_id: 'draft', connection_id: null, title: 'Netflix import', resolve_url: '/connections?import=draft'};
  let requests = 0;
  let delayNext = false;
  let staleResponse;
  const source = stripTypeScriptTypes(readFileSync(new URL('../src/lib/import-admission.ts', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/, '').replaceAll('export ', '');
  const context = vm.createContext({document, window: new EventTarget(), AbortController, AbortSignal,
    fetch: async () => {
      requests++;
      if (delayNext) { delayNext = false; return new Promise(resolve => {staleResponse = resolve;}); }
      return {ok: true, json: async () => ({active: current})};
    },
  });
  vm.runInContext(source + '\nmountImportAdmission();', context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  assert.equal(button.disabled, true);
  assert.equal(upload.inert, true);
  assert.equal(banner.hidden, false);

  const previous = current;
  delayNext = true;
  document.dispatchEvent(new Event('streaming:session-changed'));
  current = null;
  document.dispatchEvent(new Event('streaming:session-changed'));
  await settle();
  assert.equal(button.disabled, false);
  assert.equal(upload.inert, false);
  assert.equal(banner.hidden, true);
  staleResponse({ok: true, json: async () => ({active: previous})});
  await settle();
  assert.equal(button.disabled, false, 'a delayed earlier response cannot lock the controls again');

  document.dispatchEvent(new Event('astro:before-swap'));
  const before = requests;
  document.dispatchEvent(new Event('streaming:session-changed'));
  await settle();
  assert.equal(requests, before, 'outgoing pages release their lifecycle listeners');
});
