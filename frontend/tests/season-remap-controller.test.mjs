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
  globalThis.document = { getElementById: id => id === 'season-remap-modal' ? modal : null, createElement: () => new Node() };
  t.after(() => { if (previous === undefined) delete globalThis.document; else globalThis.document = previous; });
  t.mock.method(globalThis, 'fetch', fetch);
  let saved = 0;
  const controller = mountSeasonRemap('token', () => saved++);
  t.after(() => controller.stop());
  return { modal, get: id => modal.querySelector(`#${id}`), controller, saved: () => saved };
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
