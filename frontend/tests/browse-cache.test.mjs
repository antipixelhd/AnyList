import assert from 'node:assert/strict';
import test from 'node:test';
import {createBrowseCache, clearStoredBrowseCache, waitForBrowseRequest} from '../src/lib/browse-cache.ts';

test('reloads reuse only the same viewer’s unexpired results and invalidation removes them', () => {
  let now = 0;
  const data = new Map();
  const storage = {getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key)};
  const cache = viewer => createBrowseCache(() => now, 100, 2, {storage, name: 'results', viewer});
  cache('alice').set('filtered', {results: ['one']});
  assert.deepEqual(cache('alice').get('filtered'), {results: ['one']});
  assert.equal(cache('bob').get('filtered'), undefined);
  now = 80;
  const reloaded = cache('alice');
  reloaded.set('filtered', reloaded.get('filtered'), false);
  now = 100;
  assert.equal(cache('alice').get('filtered'), undefined);
  now = 101;
  cache('alice').set('filtered', {results: ['two']});
  clearStoredBrowseCache(storage);
  assert.equal(cache('alice').get('filtered'), undefined);
});

test('unavailable session storage does not break browsing', () => {
  const storage = {getItem() { throw Error('unavailable'); }, setItem() { throw Error('unavailable'); }, removeItem() { throw Error('unavailable'); }};
  const cache = createBrowseCache(undefined, undefined, undefined, {storage, name: 'results', viewer: 'alice'});
  cache.set('one', {results: [1]});
  assert.deepEqual(cache.get('one'), {results: [1]});
  clearStoredBrowseCache(storage);
});

test('queries and pages cache independently, expire, and remain bounded', () => {
  let now = 0;
  const cache = createBrowseCache(() => now, 100, 2);
  const movies = {results: ['movie']}, series = {results: ['series']};
  cache.set('movies?page=1', movies);
  cache.set('series?page=1', series);
  assert.equal(cache.get('movies?page=1'), movies);
  assert.equal(cache.get('movies?q=Tenet'), undefined);
  cache.set('movies?page=2', {results: ['next']});
  assert.equal(cache.get('series?page=1'), undefined); // Least recently used.
  assert.equal(cache.get('movies?page=1'), movies);
  now = 100;
  assert.equal(cache.get('movies?page=1'), undefined);
  assert.equal(cache.get('movies?page=2'), undefined);
  assert.equal(createBrowseCache().get('movies?page=1'), undefined); // Another page/viewer.
});

test('cached item state stays editable without resetting cache expiry on reads', () => {
  let now = 0;
  const cache = createBrowseCache(() => now, 100);
  cache.set('movie', {results: [{score: null}]});
  now = 80;
  for (const response of cache.values()) response.results[0].score = 9;
  assert.equal(cache.get('movie').results[0].score, 9);
  now = 100;
  assert.equal(cache.get('movie'), undefined);
});

test('rapid search input aborts the debounce before a stale request can start', async t => {
  t.mock.timers.enable({apis: ['setTimeout']});
  const controller = new AbortController();
  const pending = waitForBrowseRequest(220, controller.signal);
  controller.abort();
  await assert.rejects(pending, {name: 'AbortError'});
  const active = waitForBrowseRequest(220, new AbortController().signal);
  t.mock.timers.tick(220);
  await active;
});
