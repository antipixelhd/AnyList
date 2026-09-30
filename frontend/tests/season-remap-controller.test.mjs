import assert from 'node:assert/strict';
import test from 'node:test';
import { mountSeasonRemap } from '../src/lib/season-remap-controller.ts';

const drain = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };

class Node extends EventTarget {
  constructor() {
    super();
    this.dataset = {};
    this.value = '';
    this.disabled = false;
    this.children = [];
    const classes = new Set();
    this.classList = { add: (...values) => values.forEach(value => classes.add(value)),
      remove: (...values) => values.forEach(value => classes.delete(value)), contains: value => classes.has(value) };
  }
  set innerHTML(value) {
    this.children = Array.from(value.matchAll(/<button[^>]*>/g), match => {
      const button = new Node();
      for (const [, name, value] of match[0].matchAll(/data-([\w-]+)="([^"]*)"/g)) button.dataset[name] = value;
      return button;
    });
  }
  querySelectorAll(selector) { return selector.startsWith('img') ? [] : this.children; }
  appendChild(child) { this.children.push(child); }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  focus() {}
  closest(selector) { return selector.split(', ').some(value => this.classList.contains(value.slice(1))) ? this : null; }
  click() { this.dispatchEvent(new Event('click')); }
}

function page(t, fetch) {
  const nodes = new Map();
  const modal = new Node();
  modal.querySelector = selector => {
    if (!nodes.has(selector)) nodes.set(selector, new Node());
    return nodes.get(selector);
  };
  const previous = globalThis.document;
  globalThis.document = Object.assign(new EventTarget(), {
    getElementById: id => id === 'season-remap-modal' ? modal : null, createElement: () => new Node(),
  });
  const previousElement = globalThis.Element;
  globalThis.Element = Node;
  t.after(() => { if (previousElement === undefined) delete globalThis.Element; else globalThis.Element = previousElement; });
  for (const [name, value] of [['confirm', () => true], ['alert', () => {}]]) {
    const previous = globalThis[name];
    globalThis[name] = value;
    t.after(() => { if (previous === undefined) delete globalThis[name]; else globalThis[name] = previous; });
  }
  t.after(() => { if (previous === undefined) delete globalThis.document; else globalThis.document = previous; });
  t.mock.method(globalThis, 'fetch', fetch);
  let saved = 0;
  const controller = mountSeasonRemap('token', () => saved++);
  t.after(() => controller.stop());
  return { modal, get: id => modal.querySelector(`#${id}`), controller, saved: () => saved,
    click(button) {
      const event = new Event('click');
      Object.defineProperty(event, 'target', { value: button });
      document.dispatchEvent(event);
    },
  };
}

test('show matching auto-searches once and submits the selected TVDB identity', async t => {
  const calls = [];
  const state = page(t, async (url, options) => {
    calls.push({ url, options });
    return Response.json(url.includes('search-tvdb') ? [{ tvdb_id: 42, title: 'Show' }] : {});
  });
  state.controller.openMatchModal('Original');
  await drain();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/proxy/media/search-tvdb?q=Original');
  state.get('remap-tvdb-results').children[0].click();
  await drain();
  assert.equal(state.get('remap-save').disabled, false);
  state.get('remap-save').click();
  await drain();
  assert.equal(calls[1].url, '/api/proxy/sync/match-unmatched-show');
  assert.deepEqual(JSON.parse(calls[1].options.body), { show_title: 'Original', tvdb_id: 42 });
  assert.equal(state.saved(), 1);
});

function warningButton(marker, dataset) {
  const button = new Node();
  button.classList.add(marker);
  button.dataset = dataset;
  return button;
}

test('delegated warning clicks handle nested targets and refreshed rows without duplicate bindings', async t => {
  const calls = [];
  const state = page(t, async url => { calls.push(url); return Response.json({ results: [] }); });
  const remap = warningButton('remap-season-btn', { srcTmdb: '4', srcSeason: '0', srcTitle: 'Source' });
  const icon = new Node();
  icon.closest = selector => remap.closest(selector);
  state.click(icon);
  assert.equal(state.get('remap-source-label').textContent, '"Source" — Season 0');
  for (let i = 0; i < 3; i++) {
    state.click(warningButton('match-show-btn', { mediaType: 'movie', seriesName: 'Movie' }));
    await drain();
  }
  assert.deepEqual(calls, Array(3).fill('/api/proxy/media/search?q=Movie&type=movie'));
  state.controller.stop();
  state.click(warningButton('match-show-btn', { mediaType: 'movie', seriesName: 'Movie' }));
  assert.equal(calls.length, 3);
  const replacement = mountSeasonRemap('token', () => {});
  t.after(() => replacement.stop());
  state.click(warningButton('match-show-btn', { mediaType: 'movie', seriesName: 'Movie' }));
  await drain();
  assert.equal(calls.length, 4);
});

test('unmatching submits each media identity once while its request is pending', async t => {
  const calls = [];
  let finish;
  const state = page(t, (url, options) => {
    calls.push({ url, options });
    return new Promise(resolve => { finish = resolve; });
  });
  for (const mediaType of ['show', 'movie']) {
    const button = warningButton('unmatch-show-btn', { mediaType, seriesName: 'Title' });
    state.click(button);
    state.click(button);
    assert.equal(calls.at(-1).url, `/api/proxy/sync/unmatch-${mediaType}`);
    assert.deepEqual(JSON.parse(calls.at(-1).options.body), { [mediaType === 'movie' ? 'movie_title' : 'show_title']: 'Title' });
    assert.equal(calls.at(-1).options.headers.Authorization, 'Bearer token');
    finish(Response.json({}));
    await drain();
  }
  assert.equal(calls.length, 2);
  assert.equal(state.saved(), 2);
});

test('failed unmatching restores the action for retry and reports the server error', async t => {
  let calls = 0;
  const state = page(t, async () => ++calls === 1 ? new Response('offline', { status: 502 }) : Response.json({}));
  const alerts = [];
  t.mock.method(globalThis, 'alert', message => alerts.push(message));
  const button = warningButton('unmatch-show-btn', { seriesName: 'Title' });
  state.click(button);
  await drain();
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Unmatch');
  assert.deepEqual(alerts, ['Failed to unmatch: offline']);
  state.click(button);
  await drain();
  assert.equal(state.saved(), 1);
});

test('remap removal confirms once, submits once, and removes an empty panel', async t => {
  let finish, calls = 0, confirmations = 0, removed = 0, panelsRemoved = 0;
  const state = page(t, () => { calls++; return new Promise(resolve => { finish = resolve; }); });
  t.mock.method(globalThis, 'confirm', () => { confirmations++; return true; });
  const button = warningButton('delete-override-btn', { overrideId: '19' });
  const closest = button.closest.bind(button);
  button.closest = selector => selector === '[data-override-id]' ? { remove: () => removed++ } : closest(selector);
  const getElementById = document.getElementById;
  document.getElementById = id => id === 'season-remaps-list' ? { children: [] }
    : id === 'season-remaps-panel' ? { remove: () => panelsRemoved++ } : getElementById(id);
  state.click(button);
  state.click(button);
  assert.equal(calls, 1);
  assert.equal(confirmations, 1);
  finish(Response.json({}));
  await drain();
  assert.equal(removed, 1);
  assert.equal(panelsRemoved, 1);
});

test('cancelled removal sends no request and navigation suppresses late UI updates', async t => {
  let finish, calls = 0, removed = 0;
  const state = page(t, () => { calls++; return new Promise(resolve => { finish = resolve; }); });
  const button = warningButton('delete-override-btn', { overrideId: '19' });
  const closest = button.closest.bind(button);
  button.closest = selector => selector === '[data-override-id]' ? { remove: () => removed++ } : closest(selector);
  const confirmation = t.mock.method(globalThis, 'confirm', () => false);
  state.click(button);
  assert.equal(calls, 0);
  assert.equal(button.disabled, false);
  confirmation.mock.mockImplementation(() => true);
  state.click(button);
  state.controller.stop();
  finish(Response.json({}));
  await drain();
  assert.equal(calls, 1);
  assert.equal(removed, 0);
  assert.equal(state.saved(), 0);
});

test('movie matching uses movie search and the selected TMDB identity', async t => {
  const calls = [];
  const state = page(t, async (url, options) => {
    calls.push({ url, options });
    return Response.json(url.includes('media/search') ? { results: [{ tmdb_id: 7, title: 'Movie' }] } : {});
  });
  state.controller.openMatchMovieModal('Original Movie');
  await drain();
  assert.equal(calls[0].url, '/api/proxy/media/search?q=Original%20Movie&type=movie');
  state.get('remap-search-results').children[0].click();
  await drain();
  state.get('remap-save').click();
  await drain();
  assert.equal(calls[1].url, '/api/proxy/sync/match-unmatched-movie');
  assert.deepEqual(JSON.parse(calls[1].options.body), { movie_title: 'Original Movie', tmdb_id: 7 });
  assert.equal(state.saved(), 1);
});

test('season zero is selectable and a remap is created before it is applied', async t => {
  const calls = [];
  const state = page(t, async (url, options) => {
    calls.push({ url, options });
    return Response.json(url.includes('media/search') ? { results: [{ tmdb_id: 7, title: 'Show' }] }
      : url.includes('/shows/') ? { seasons_meta: [{ season_number: 0, name: 'Specials' }] } : { id: 19 });
  });
  state.controller.openRemapModal(4, 2, 'Source');
  state.get('remap-search-input').value = 'Target';
  state.get('remap-search-btn').click();
  await drain();
  state.get('remap-search-results').children[0].click();
  await drain();
  assert.equal(state.get('remap-save').disabled, true);
  state.get('remap-seasons-list').children[0].click();
  assert.equal(state.get('remap-save').disabled, false);
  state.get('remap-save').click();
  await drain();
  assert.deepEqual(calls.slice(2).map(call => call.url), ['/api/proxy/sync/season-overrides', '/api/proxy/sync/season-overrides/19/apply']);
  assert.deepEqual(JSON.parse(calls[2].options.body), {
    source_show_tmdb_id: 4, source_season_number: 2, target_season_number: 0, target_show_tmdb_id: 7,
  });
  assert.equal(state.saved(), 1);
});

test('navigation aborts searches and removes listeners before mounting the replacement', async t => {
  const calls = [];
  const state = page(t, async (url, options) => {
    calls.push({ url, options });
    return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true }));
  });
  state.controller.openMatchMovieModal('Movie');
  state.controller.stop();
  await drain();
  assert.equal(calls[0].options.signal.aborted, true);
  state.get('remap-search-btn').click();
  assert.equal(calls.length, 1);
  const replacement = mountSeasonRemap('token', () => {});
  t.after(() => replacement.stop());
  state.get('remap-search-btn').click();
  assert.equal(calls.length, 2);
});

test('navigation lets an already-started remap finish applying without refreshing the new page', async t => {
  let finishCreate;
  const calls = [];
  const state = page(t, async (url, options) => {
    calls.push({ url, options });
    if (url === '/api/proxy/sync/season-overrides') return new Promise(resolve => { finishCreate = resolve; });
    return Response.json(url.includes('media/search') ? { results: [{ tmdb_id: 7, title: 'Show' }] }
      : url.includes('/shows/') ? { seasons_meta: [{ season_number: 0 }] } : {});
  });
  state.controller.openRemapModal(4, 2, 'Source');
  state.get('remap-search-input').value = 'Target';
  state.get('remap-search-btn').click();
  await drain();
  state.get('remap-search-results').children[0].click();
  await drain();
  state.get('remap-seasons-list').children[0].click();
  state.get('remap-save').click();
  state.controller.stop();
  finishCreate(Response.json({ id: 19 }));
  await drain();
  assert.equal(calls.at(-1).url, '/api/proxy/sync/season-overrides/19/apply');
  assert.equal(calls[2].options.signal, undefined);
  assert.equal(calls[3].options.signal, undefined);
  assert.equal(state.saved(), 0);
  assert.equal(state.modal.classList.contains('hidden'), false);
});
