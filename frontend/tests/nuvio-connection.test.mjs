import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transpile } from 'typescript';
import test from 'node:test';

const source = readFileSync(new URL('../src/pages/connections.astro', import.meta.url), 'utf8');
const fields = source.slice(source.indexOf('    // Provider-specific fields for a new connection'), source.indexOf("    connectStremioBtn?.addEventListener('click'"));
const actions = source.slice(source.indexOf('    // Test new connection button'), source.indexOf('  function setupScrobbleConnections()'));

function element(value = '') {
  const listeners = new Map();
  const el = {
    value, disabled: false, checked: true, className: '', textContent: '', options: [],
    addEventListener: (type, callback) => listeners.set(type, callback),
    fire: type => listeners.get(type)?.(),
    querySelector: () => null,
    closest: () => null,
  };
  el.classList = {
    contains: name => el.className.split(' ').includes(name),
    toggle(name, on) {
      const classes = new Set(el.className.split(' ').filter(Boolean));
      if (on) classes.add(name); else classes.delete(name);
      el.className = [...classes].join(' ');
    },
  };
  Object.defineProperty(el, 'innerHTML', {
    set(html) {
      el.options = [...html.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)].map(match => ({ value: match[1], text: match[2] }));
      el.value = el.options[0]?.value || '';
    },
  });
  return el;
}

function form(login = async () => ({ profiles: [{ profile_index: 0, name: 'First' }, { profile_index: 1, name: 'Second' }], refresh_token: 'refresh' })) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  get('new-conn-type').value = 'nuvio';
  get('new-conn-name').value = 'My Nuvio';
  get('new-nuvio-email').value = 'person@example.com';
  get('new-conn-token').value = 'password';
  get('create-connection-btn').className = 'bg-blue-600 text-white';
  get('test-new-connection-btn').className = 'bg-zinc-800';
  get('test-new-connection-btn').textContent = 'Test';
  get('create-connection-btn').textContent = 'Add';
  const requests = [], messages = [];
  const context = {
    document: { getElementById: get, querySelector: selector => get(selector.slice(1)) },
    token: 'session', stremioPollTimer: null, resetPlexLogin: null,
    esc: value => value, errorMessage: error => error.message,
    showMessage: message => messages.push(message), setTimeout() {},
    fetch: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) });
      const data = url.endsWith('/connections') ? {} : await login();
      return { ok: true, json: async () => data };
    },
  };
  // The actions slice ends with the enclosing setup function's closing brace.
  runInNewContext(transpile(`async function setup() {\n${fields}\n${actions}\nsetup();`), context);
  return { get, requests, messages, test: () => get('test-new-connection-btn').fire('click'), add: () => get('create-connection-btn').fire('click') };
}

test('Nuvio requires testing and an explicit profile, then saves that profile without another login', async () => {
  const f = form();
  assert.equal(f.get('new-nuvio-profile-row').classList.contains('hidden'), true);
  assert.equal(f.get('create-connection-btn').classList.contains('hidden'), true);
  assert.equal(f.get('test-new-connection-btn').classList.contains('hidden'), false);
  assert.equal(f.get('test-new-connection-btn').classList.contains('bg-blue-600'), true);
  await f.add();
  assert.equal(f.requests.length, 0);
  await f.test();
  assert.equal(f.get('new-nuvio-profile-row').classList.contains('hidden'), false);
  assert.equal(f.get('test-new-connection-btn').classList.contains('hidden'), true);
  assert.equal(f.get('create-connection-btn').classList.contains('hidden'), false);
  assert.equal(f.get('new-nuvio-profile').value, '');
  assert.equal(f.get('new-nuvio-profile').options[0].text, 'Select profile');
  assert.equal(f.get('create-connection-btn').disabled, true);
  await f.add();
  assert.equal(f.requests.length, 1);
  f.get('new-nuvio-profile').value = '1';
  f.get('new-nuvio-profile').fire('change');
  assert.equal(f.get('create-connection-btn').disabled, false);
  await f.add();
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[1].body.server_user_id, '1');
  assert.equal(f.requests[1].body.token, 'refresh');
});

test('changing credentials clears loaded profiles and requires another test', async () => {
  for (const id of ['new-nuvio-email', 'new-conn-token', 'new-conn-url']) {
    const f = form();
    await f.test();
    f.get('new-nuvio-profile').value = '0';
    f.get(id).value = 'changed';
    f.get(id).fire('input');
    assert.equal(f.get('new-nuvio-profile').value, '');
    assert.equal(f.get('new-nuvio-profile-row').classList.contains('hidden'), true);
    assert.equal(f.get('test-new-connection-btn').classList.contains('hidden'), false);
    await f.add();
    assert.equal(f.requests.length, 1);
  }
});

test('an empty profile response keeps the test step available', async () => {
  const f = form(async () => ({ profiles: [], refresh_token: 'refresh' }));
  await f.test();
  assert.equal(f.get('new-nuvio-profile-row').classList.contains('hidden'), true);
  assert.equal(f.get('test-new-connection-btn').disabled, false);
  assert.match(f.messages.at(-1), /No Nuvio profile/);
  await f.add();
  assert.equal(f.requests.length, 1);
});

test('a test response for old credentials cannot unlock Add', async () => {
  let finish;
  const f = form(() => new Promise(resolve => { finish = resolve; }));
  const testing = f.test();
  f.get('new-nuvio-email').fire('input');
  finish({ profiles: [{ profile_index: 0 }], refresh_token: 'old' });
  await testing;
  assert.equal(f.get('new-nuvio-profile-row').classList.contains('hidden'), true);
  assert.match(f.messages.at(-1), /Credentials changed/);
  await f.add();
  assert.equal(f.requests.length, 1);
});

test('switching providers clears Nuvio authentication and restores other provider buttons', async () => {
  const f = form();
  await f.test();
  f.get('new-conn-type').value = 'jellyfin';
  f.get('new-conn-type').fire('change');
  assert.equal(f.get('create-connection-btn').disabled, false);
  assert.equal(f.get('test-new-connection-btn').classList.contains('bg-zinc-800'), true);
  f.get('new-conn-type').value = 'nuvio';
  f.get('new-conn-type').fire('change');
  assert.equal(f.get('new-nuvio-profile-row').classList.contains('hidden'), true);
  await f.add();
  assert.equal(f.requests.length, 1);
});
