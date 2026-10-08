import assert from 'node:assert/strict';
import test from 'node:test';
import {createBrowseCache, waitForBrowseRequest} from '../src/lib/browse-cache.ts';

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
