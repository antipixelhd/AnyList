import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transpile } from 'typescript';
import test from 'node:test';

const source = readFileSync(new URL('../src/pages/connections.astro', import.meta.url), 'utf8');
const scrobble = source.slice(source.indexOf('  const scrobblePages ='), source.indexOf('  function setupSettingsForm()'));
const lifecycle = source.slice(source.indexOf('  function setupConnectionsPage()'), source.indexOf('</script>', source.indexOf('  function setupConnectionsPage()')));

function control(value = '') {
  return {
    value, textContent: 'Create', listeners: new Map(), hidden: false,
    classList: { toggle() {} }, getAttribute() { return ""; }, setAttribute() {}, removeAttribute() {},
    addEventListener(type, handler) {
      this.listeners.set(type, [...(this.listeners.get(type) || []), handler]);
    },
  };
}

test('scrobble copy, type, and create controls survive category navigation and bind once', async () => {
  let page = {}, ui;
  const swaps = new Map(), copied = [], requests = [];
  const nextPage = () => {
    ui = {
      '#scrob_api_key': control('test-key'),
      '#new-scrobble-type': control('jellyfin'),
      '#new-scrobble-name': control('Local server'),
      '#new-scrobble-user-id': control('viewer-id'),
      '#new-scrobble-username': control('viewer-name'),
      '#new-scrobble-user-id-row': control(),
      '#new-scrobble-username-row': control(),
      '#create-scrobble-conn-btn': control(),
      copy: control(), webhook: control(),
    };
    for (const selector of ['#new-scrobble-user-id-row', '#new-scrobble-username-row']) {
      ui[selector].classList.toggle = (_, hidden) => { ui[selector].hidden = hidden; };
    }
    ui.webhook.getAttribute = () => 'jellyfin';
    ui.card = {
      getAttribute: () => '42',
      querySelector: selector => selector === '.scrobble-webhook-url' ? ui.webhook : ui.copy,
    };
    return ui;
  };
  nextPage();
  const context = {
    document: {
      querySelector: selector => selector === '[data-connections-page]' ? page : ui[selector] ?? null,
      querySelectorAll: () => page ? [ui.card] : [],
      getElementById: id => ui['#' + id] ?? null,
      addEventListener: (type, handler) => swaps.set(type, handler),
    },
    window: { location: { origin: 'http://anylist.test', reload() {} } },
    token: 'test-only',
    copyToClipboard: async value => copied.push(value),
    flashCopied() {}, showMessage() {}, errorMessage: error => error.message,
    setTimeout() {},
    fetch: async (_, options) => { requests.push(JSON.parse(options.body)); return { ok: true }; },
  };
  for (const name of ['setupJobMonitoring', 'setupActions', 'setupConnections', 'setupSettingsForm', 'setupApiKey', 'setupConnectedApps', 'setupCollapsibleRows', 'openDeepLinkedConnection', 'checkConnections', 'loadSyncWarnings', 'loadLastSynced', 'setupSeasonRemap'])
    context[name] = () => {};
  runInNewContext(transpile(scrobble + lifecycle), context);
  const original = ui;
  page = null;
  swaps.get('astro:after-swap')();
  assert.equal(original.copy.listeners.get('click').length, 1);
  page = {};
  nextPage();
  swaps.get('astro:after-swap')();
  swaps.get('astro:after-swap')();
  for (const [item, event] of [[ui.copy, 'click'], [ui['#new-scrobble-type'], 'change'], [ui['#create-scrobble-conn-btn'], 'click']])
    assert.equal(item.listeners.get(event).length, 1);
  await ui.copy.listeners.get('click')[0]();
  assert.deepEqual(copied, ['http://anylist.test/api/proxy/webhooks/jellyfin/scrobble/42?api_key=test-key']);
  ui['#new-scrobble-type'].value = 'plex';
  ui['#new-scrobble-type'].listeners.get('change')[0]();
  assert.equal(ui['#new-scrobble-user-id-row'].hidden, true);
  assert.equal(ui['#new-scrobble-username-row'].hidden, false);
  await ui['#create-scrobble-conn-btn'].listeners.get('click')[0]();
  assert.deepEqual(requests, [{ name: 'Local server', type: 'plex', server_username: 'viewer-name' }]);
});

