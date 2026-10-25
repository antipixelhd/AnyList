import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';
import test from 'node:test';

test('inline admission replaces starts, disappears on resume, and unlocks on completion', async () => {
  const connection = {hidden: false};
  const app = {dataset: {}, hidden: false};
  const service = {hidden: false, dataset: {service: 'netflix'}, querySelector: () => app};
  const banner = {dataset: {}, hidden: true, querySelector: () => null};
  const document = Object.assign(new EventTarget(), {
    querySelector: () => banner,
    querySelectorAll: selector => ({
      '[data-import-admission]': [banner],
      '[data-import-start-connection]': [connection],
      '[data-import-start-service]': [service],
      '[data-streaming-import][data-import-session]': app.dataset.importSession ? [app] : [],
      '[data-import-admission-cancel]': [],
    })[selector] || [],
  });
  const location = {href: 'http://example.test/connections?section=imports'};
  let current = {import_id: 'draft', connection_id: null, title: 'Netflix import', resolve_url: '/connections?import=draft'};
  let requests = 0;
  let delayNext = false;
  let staleResponse;
  const source = stripTypeScriptTypes(readFileSync(new URL('../src/lib/import-admission.ts', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/, '').replaceAll('export ', '');
  const context = vm.createContext({document, window: new EventTarget(), location, URL, queueMicrotask, AbortController, AbortSignal,
    fetch: async () => {
      requests++;
      if (delayNext) { delayNext = false; return new Promise(resolve => {staleResponse = resolve;}); }
      return {ok: true, json: async () => ({active: current})};
    },
  });
  vm.runInContext(source + '\nmountImportAdmission();', context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  assert.equal(connection.hidden, true);
  assert.equal(service.hidden, true);
  assert.equal(banner.hidden, false);

  const beforeResume = requests;
  location.href = 'http://example.test/connections?section=imports&import=draft&source=netflix';
  document.dispatchEvent(new Event('streaming:activity-changed'));
  await settle();
  assert.equal(banner.hidden, true, 'resuming removes the redundant notice');
  assert.equal(service.hidden, false, 'the resumed workflow stays visible');
  assert.equal(connection.hidden, true, 'a second connection still cannot be added');
  assert.equal(requests, beforeResume, 'workflow paints do not refetch admission');

  location.href = 'http://example.test/connections?section=imports';
  app.dataset.importSession = 'draft';
  document.dispatchEvent(new Event('streaming:activity-changed'));
  await settle();
  assert.equal(banner.hidden, true, 'an already open workflow suppresses the notice without URL parameters');

  const previous = current;
  delayNext = true;
  document.dispatchEvent(new Event('streaming:session-changed'));
  current = null;
  document.dispatchEvent(new Event('streaming:session-changed'));
  await settle();
  assert.equal(connection.hidden, false);
  assert.equal(service.hidden, false);
  assert.equal(banner.hidden, true);
  staleResponse({ok: true, json: async () => ({active: previous})});
  await settle();
  assert.equal(connection.hidden, false, 'a delayed earlier response cannot hide the controls again');

  document.dispatchEvent(new Event('astro:before-swap'));
  const before = requests;
  document.dispatchEvent(new Event('streaming:session-changed'));
  await settle();
  assert.equal(requests, before, 'outgoing pages release their lifecycle listeners');
});
